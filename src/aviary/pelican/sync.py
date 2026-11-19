"""Copy external images of published records to Cloudflare Images.

A port of catalog-sync's jobs/sync-images.js. Keysets (cover, order graph,
order history), kits, keyboard variants (front, back), and artisan colorways
added in the app can point at an external URL; this uploads it under a
readable id and points the row at the Cloudflare copy.

- Every id ends with a CRC32 of the source URL: a row whose image is replaced
  by a new URL gets a new id (Cloudflare rejects an existing one), and an id
  never holds different content, which the R2 backup relies on. The old upload
  is pruned once nothing references it.
- Only published rows sync (`review_status` null or Approved, and so is their
  keyset, keyboard, or sculpt): rows in review keep the original URL so
  reviewers see the source, and rejected rows leave no upload behind.
- A source answering 404 (or a deleted imgur image) clears the row's image,
  except on keyboard variants, as catalog-sync did.
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import httpx
from supabase import Client

from aviary.common import cf_images
from aviary.common.ids import crc32_hex
from aviary.common.slug import url_slugify
from aviary.pelican.references import Row, fetch_all

log = logging.getLogger(__name__)

OFFICIAL = "review_status.is.null,review_status.eq.Approved"
KEYSET_IMAGE_COLUMNS = ("img", "order_graph", "order_history")
KEYBOARD_SIDES = ("front", "back")
CONCURRENCY = 5

_KEEP = object()  # a 404 leaves the row's image alone


@dataclass(frozen=True)
class Upload:
    table: str
    key: tuple[str, Any]  # the column and value that find the row
    column: str
    image_id: str
    source: str
    cleared: Any  # what a 404 sets the column to, or _KEEP


def keyset_image_id(keyset: Row, column: str = "img") -> str:
    """The cover keeps the original scheme; order graphs and histories get
    their own folder (`keyset/<profile_keyset_id>/order-graph/<hash>`)."""
    base = f"keyset/{keyset['profile_keyset_id']}"
    if column == "img":
        return f"{base}/{crc32_hex(keyset['img'])}"
    return f"{base}/{column.replace('_', '-', 1)}/{crc32_hex(keyset[column])}"


def kit_image_id(kit: Row) -> str:
    kit_key = kit.get("kit_id") or url_slugify(kit["name"])
    return (
        f"keyset/{kit['profile_keyset_id']}/kit/{kit['id']}/{kit_key}"
        f"/{crc32_hex(kit['img'])}"
    )


def variant_image_id(variant: Row, side: str) -> str:
    return (
        f"keyboard/{variant['brand_keyboard_slug']}/{variant['release_id']}"
        f"/{url_slugify(variant['variant_name'])}/{side}"
        f"/{crc32_hex(variant[f'img_{side}'])}"
    )


def colorway_image_id(colorway: Row) -> str:
    """bowerbird's scheme plus the URL hash."""
    return (
        f"artisan/{colorway['maker_id']}/{colorway['sculpt_id']}"
        f"/{colorway['colorway_id']}/{crc32_hex(colorway['img'])}"
    )


def is_unsynced(url: Any, base_url: str) -> bool:
    return bool(url) and base_url not in url


def _official_keys(client: Client, table: str, column: str) -> set:
    rows = fetch_all(client, table, f"id, {column}", lambda q: q.or_(OFFICIAL))
    return {row[column] for row in rows}


def _with_image(query):
    return query.or_(OFFICIAL).not_.is_("img", "null").neq("img", "")


def plan_uploads(client: Client, base_url: str) -> list[list[Upload]]:
    """The uploads to make, in catalog-sync's order: keysets, kits, keyboard
    variants, colorways."""
    keysets = fetch_all(
        client,
        "keysets",
        "id, profile_keyset_id, " + ", ".join(KEYSET_IMAGE_COLUMNS),
        lambda q: q.or_(OFFICIAL),
    )
    official_keysets = _official_keys(client, "keysets", "profile_keyset_id")
    kits = [
        kit
        for kit in fetch_all(client, "keyset_kits", "*", _with_image)
        if kit["profile_keyset_id"] in official_keysets
    ]
    official_keyboards = _official_keys(client, "keyboards", "brand_keyboard_slug")
    variants = [
        variant
        for variant in fetch_all(
            client, "keyboard_variants", "*", lambda q: q.or_(OFFICIAL)
        )
        if variant["brand_keyboard_slug"] in official_keyboards
    ]
    official_sculpts = _official_keys(client, "artisan_sculpts", "maker_sculpt_id")
    colorways = [
        colorway
        for colorway in fetch_all(
            client,
            "artisan_colorways",
            "id, maker_id, sculpt_id, maker_sculpt_id, colorway_id, img",
            _with_image,
        )
        if colorway["maker_sculpt_id"] in official_sculpts
    ]

    unsynced = lambda url: is_unsynced(url, base_url)  # noqa: E731
    return [
        [
            Upload(
                "keysets",
                ("profile_keyset_id", k["profile_keyset_id"]),
                column,
                keyset_image_id(k, column),
                k[column],
                None,
            )
            for k in keysets
            for column in KEYSET_IMAGE_COLUMNS
            if unsynced(k.get(column))
        ],
        [
            Upload("keyset_kits", ("id", k["id"]), "img", kit_image_id(k), k["img"], "")
            for k in kits
            if unsynced(k.get("img"))
        ],
        [
            Upload(
                "keyboard_variants",
                ("id", v["id"]),
                f"img_{side}",
                variant_image_id(v, side),
                v[f"img_{side}"],
                _KEEP,
            )
            for v in variants
            for side in KEYBOARD_SIDES
            if unsynced(v.get(f"img_{side}"))
        ],
        [
            Upload(
                "artisan_colorways",
                ("id", c["id"]),
                "img",
                colorway_image_id(c),
                c["img"],
                None,
            )
            for c in colorways
            if unsynced(c.get("img"))
        ],
    ]


def _update(client: Client, upload: Upload, value: Any) -> None:
    column, key = upload.key
    try:
        client.table(upload.table).update({upload.column: value}).eq(
            column, key
        ).execute()
    except Exception:
        log.exception("Unable to update %s %s=%s", upload.table, column, key)


def _sync_one(
    client: Client, http: httpx.Client, base_url: str, upload: Upload
) -> None:
    result = cf_images.upload_image(http, upload.image_id, upload.source)
    if result.ok:
        _update(client, upload, f"{base_url}/{upload.image_id}/public")
    elif result.status == 404 and upload.cleared is not _KEEP:
        _update(client, upload, upload.cleared)


def run(client: Client, http: httpx.Client, base_url: str, *, dry_run: bool) -> None:
    groups = plan_uploads(client, base_url)
    names = ("keyset images", "kits", "keyboard variant images", "colorways")
    for name, uploads in zip(names, groups, strict=True):
        log.info("%s to sync: %d", name, len(uploads))
    if dry_run:
        for uploads in groups:
            for upload in uploads:
                log.info("[DRY RUN] Would upload %s", upload.image_id)
        return

    sync: Callable[[Upload], None] = lambda u: _sync_one(client, http, base_url, u)  # noqa: E731
    for uploads in groups:
        with ThreadPoolExecutor(CONCURRENCY) as pool:
            list(pool.map(sync, uploads))
