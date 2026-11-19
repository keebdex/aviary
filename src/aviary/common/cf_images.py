"""Cloudflare Images: list the stored ids and upload images under chosen ids.

A port of catalog-sync's utils/image.js (uploadImage, getListImages). Nothing
here deletes an image: catalog-sync's prune-images removes the ones no row
references any more.
"""

import io
import logging
import re
from dataclasses import dataclass
from functools import cache

import httpx
from PIL import Image

from aviary.common.config import require_env
from aviary.common.http import BROWSER_USER_AGENT

log = logging.getLogger(__name__)

API_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/images"

# Cloudflare cannot fetch from Cloudflare Images (another account's
# imagedelivery.net or /cdn-cgi/imagedelivery/ URLs, error 5454), so those are
# downloaded here and uploaded as a file.
CLOUDFLARE_DELIVERY_URL = re.compile(
    r"^https://(?:imagedelivery\.net|[^/]+/cdn-cgi/imagedelivery)/"
)

# Could not fetch the source URL (it answers 424 for some fetch failures), and
# the image is over the size limits: both are retried as a downloaded file.
FETCH_ERROR_CODE = 5454
FETCH_ERROR_STATUS = 424
TOO_LARGE_CODE = 5443
MAX_DIMENSION = 12000
MAX_AREA = 100_000_000

# imgur answers a deleted image with a redirect to a placeholder, which
# Cloudflare would store as if it were the real one.
IMGUR_URL = re.compile(r"^https?://(?:i\.)?imgur\.com/")
IMGUR_REMOVED_URL = "https://i.imgur.com/removed.png"

RESIZE_FORMATS = {"JPEG", "PNG", "WEBP"}


@dataclass(frozen=True)
class UploadResult:
    ok: bool
    status: int | None = None
    message: str | None = None


@dataclass(frozen=True)
class SourceFile:
    data: bytes
    content_type: str | None


@cache
def _account() -> tuple[str, str]:
    return require_env("CF_IMAGES_ACCOUNT_ID"), require_env("CF_IMAGES_API_KEY")


def delivery_base_url() -> str:
    """Where Keebdex serves Cloudflare images: `<base>/<id>/public`."""
    return f"https://imagedelivery.net/{require_env('CF_IMAGES_ACCOUNT_HASH')}"


def _api(client: httpx.Client, method: str, path: str, **kwargs) -> httpx.Response:
    account, token = _account()
    return client.request(
        method,
        API_URL.format(account=account) + path,
        headers={"Authorization": f"Bearer {token}"},
        **kwargs,
    )


def list_image_ids(client: httpx.Client) -> set[str]:
    """Every image id on the account."""
    ids: set[str] = set()
    token = None
    while True:
        params = {"per_page": 5000}
        if token:
            params["continuation_token"] = token
        response = _api(client, "GET", "/v2", params=params)
        response.raise_for_status()
        result = response.json()["result"]
        ids.update(image["id"] for image in result["images"])
        token = result.get("continuation_token")
        if not token:
            return ids


def _download_headers() -> dict[str, str]:
    # imgur answers library user agents with a 429.
    return {"User-Agent": BROWSER_USER_AGENT}


def _is_removed_imgur(client: httpx.Client, url: str) -> bool:
    if not IMGUR_URL.match(url):
        return False
    try:
        response = client.head(url, headers=_download_headers())
    except httpx.HTTPError:
        return False  # a failed check falls back to a normal upload
    return str(response.url) == IMGUR_REMOVED_URL


def _fetch(client: httpx.Client, url: str) -> SourceFile | UploadResult:
    try:
        response = client.get(url, headers=_download_headers())
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        log.error("Unable to download %s: %s", url, error)
        return UploadResult(False, error.response.status_code, str(error))
    except httpx.HTTPError as error:
        log.error("Unable to download %s: %s", url, error)
        return UploadResult(False, None, str(error))
    return SourceFile(response.content, response.headers.get("content-type"))


def _fit(file: SourceFile) -> SourceFile:
    """The file within Cloudflare's dimension and area limits, downscaled if
    needed (JPEG, PNG, and WebP keep their format; others become JPEG)."""
    try:
        image = Image.open(io.BytesIO(file.data))
        width, height = image.size
    except OSError:
        return file  # not an image Pillow reads: let Cloudflare reject it
    scale = min(
        MAX_DIMENSION / max(width, height), (MAX_AREA / (width * height)) ** 0.5
    )
    if scale >= 1:
        return file
    output = image.format if image.format in RESIZE_FORMATS else "JPEG"
    new_width = int(width * scale)
    resized = image.resize((new_width, round(height * new_width / width)))
    if output == "JPEG" and resized.mode not in ("RGB", "L"):
        resized = resized.convert("RGB")
    buffer = io.BytesIO()
    resized.save(buffer, format=output, quality=90)
    return SourceFile(buffer.getvalue(), f"image/{output.lower()}")


def _upload_file(client: httpx.Client, image_id: str, file: SourceFile) -> UploadResult:
    fitted = _fit(file)
    response = _api(
        client,
        "POST",
        "/v1",
        data={"id": image_id},
        files={
            "file": (
                image_id.rsplit("/", 1)[-1],
                fitted.data,
                fitted.content_type or "application/octet-stream",
            )
        },
    )
    if response.is_success:
        return UploadResult(True)
    log.error(
        "Unable to upload %s: %s %s", image_id, response.status_code, response.text
    )
    return UploadResult(False, response.status_code, response.text)


def _upload_downloaded(client: httpx.Client, image_id: str, url: str) -> UploadResult:
    file = _fetch(client, url)
    if isinstance(file, UploadResult):
        return file
    return _upload_file(client, image_id, file)


def upload_image(client: httpx.Client, image_id: str, url: str) -> UploadResult:
    """Upload the image at `url` under `image_id`. Never raises; failures are
    logged and returned. A deleted imgur image answers like a 404."""
    if _is_removed_imgur(client, url):
        log.error("Source image of %s was deleted: %s", image_id, url)
        return UploadResult(False, 404, "deleted on imgur")

    if CLOUDFLARE_DELIVERY_URL.match(url):
        return _upload_downloaded(client, image_id, url)

    try:
        response = _api(client, "POST", "/v1", data={"id": image_id, "url": url})
    except httpx.HTTPError as error:
        log.error("Unable to upload %s: %s", image_id, error)
        return UploadResult(False, None, str(error))
    if response.is_success:
        return UploadResult(True)

    try:
        codes = {error["code"] for error in response.json().get("errors", [])}
    except ValueError:
        codes = set()
    if response.status_code != 404 and (
        response.status_code == FETCH_ERROR_STATUS
        or FETCH_ERROR_CODE in codes
        or TOO_LARGE_CODE in codes
    ):
        return _upload_downloaded(client, image_id, url)

    log.error(
        "Unable to upload %s: %s %s", image_id, response.status_code, response.text
    )
    return UploadResult(False, response.status_code, response.text)
