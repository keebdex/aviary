"""Colorway images: the Cloudflare id each one is stored under, and uploads.

A colorway's `img` holds its Cloudflare delivery URL, decided before the row is
written, and `remote_img` the source URL to upload from. The id never changes
for a given colorway: a synced image keeps the path it was first uploaded to,
which still holds the sculpt's source slug after a rename in Keebdex.
"""

import logging

import httpx

from aviary.bowerbird.plan import Row
from aviary.common import cf_images

log = logging.getLogger(__name__)


def image_id(colorway: Row, base_url: str) -> str:
    """catalog-sync's makeImageId."""
    prefix = f"{base_url}/artisan/"
    img = colorway.get("img")
    if isinstance(img, str) and img.startswith(prefix) and img.endswith("/public"):
        return img[len(base_url) + 1 : -len("/public")]
    maker, sculpt = colorway["maker_id"], colorway["sculpt_id"]
    return f"artisan/{maker}/{sculpt}/{colorway['colorway_id']}"


def point_at_cloudflare(colorway: Row, base_url: str) -> Row:
    """Swap an external image URL for the Cloudflare URL it will be uploaded
    to, keeping the source in `remote_img` (catalog-sync's
    normalizeColorwayImage)."""
    img = colorway.get("img")
    if not img or base_url in img:
        return colorway
    return {
        **colorway,
        "remote_img": img,
        "img": f"{base_url}/{image_id(colorway, base_url)}/public",
    }


def upload_missing(
    client: httpx.Client,
    colorways: list[Row],
    existing_ids: set[str],
    base_url: str,
    *,
    dry_run: bool,
) -> int:
    """Upload the source image of every colorway whose id Cloudflare lacks,
    skipping images locked in the app. Returns how many failed."""
    pending = [
        (image_id(c, base_url), c["remote_img"])
        for c in colorways
        if c.get("remote_img") and not c.get("img_overridden")
    ]
    pending = [(i, url) for i, url in pending if i not in existing_ids]
    if not pending:
        return 0
    if dry_run:
        log.info("[DRY RUN] Would upload %d images", len(pending))
        return 0

    failed = 0
    for image, url in pending:
        result = cf_images.upload_image(client, image, url)
        if result.ok:
            existing_ids.add(image)
        else:
            failed += 1
    log.info("Images uploaded: %d of %d", len(pending) - failed, len(pending))
    return failed
