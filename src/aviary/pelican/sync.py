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
from dataclasses import dataclass
from functools import partial
from typing import Any

import httpx
from supabase import Client

from aviary.common import cf_images
from aviary.common.db import PUBLISHED_FILTER, Row, fetch_all
from aviary.common.ids import crc32_hex
from aviary.common.pool import run_each
from aviary.common.slug import url_slugify
from aviary.pelican.references import IMAGE_COLUMNS

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Upload:
    table: str
    key_column: str  # with key_value, finds the row to update
    key_value: Any
    column: str
    image_id: str
    source: str
    clear_on_404: bool  # a gone source clears the column to `cleared`
    cleared: Any = None


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


def variant_image_id(variant: Row, column: str) -> str:
    side = column.removeprefix("img_")
    return (
        f"keyboard/{variant['brand_keyboard_slug']}/{variant['release_id']}"
        f"/{url_slugify(variant['variant_name'])}/{side}"
        f"/{crc32_hex(variant[column])}"
    )


def colorway_image_id(colorway: Row) -> str:
    """bowerbird's scheme plus the URL hash."""
    return (
        f"artisan/{colorway['maker_id']}/{colorway['sculpt_id']}"
        f"/{colorway['colorway_id']}/{crc32_hex(colorway['img'])}"
    )


def is_unsynced(url: Any, base_url: str) -> bool:
    return bool(url) and base_url not in url


def _published(query):
    return query.or_(PUBLISHED_FILTER)


def _published_keys(client: Client, table: str, column: str) -> set:
    rows = fetch_all(client, table, f"id, {column}", _published)
    return {row[column] for row in rows}


def _like_prefix(prefix: str) -> str:
    """A PostgREST `like` pattern for values starting with `prefix` (`*` is
    its wildcard; `_` is escaped, or it would match any character)."""
    return prefix.replace("\\", "\\\\").replace("_", "\\_") + "/*"


def _with_image(query):
    return _published(query).not_.is_("img", "null").neq("img", "")


def plan_uploads(client: Client, base_url: str) -> list[list[Upload]]:
    """The uploads to make, in catalog-sync's order: keysets, kits, keyboard
    variants, colorways."""
    keyset_columns = IMAGE_COLUMNS["keysets"]
    keysets = fetch_all(
        client,
        "keysets",
        "id, profile_keyset_id, " + ", ".join(keyset_columns),
        _published,
    )
    published_keysets = {k["profile_keyset_id"] for k in keysets}
    kits = [
        kit
        for kit in fetch_all(client, "keyset_kits", "*", _with_image)
        if kit["profile_keyset_id"] in published_keysets
    ]
    keyboards = _published_keys(client, "keyboards", "brand_keyboard_slug")
    variants = [
        variant
        for variant in fetch_all(client, "keyboard_variants", "*", _published)
        if variant["brand_keyboard_slug"] in keyboards
    ]
    sculpts = _published_keys(client, "artisan_sculpts", "maker_sculpt_id")
    # Nearly every colorway already points at Cloudflare: leave those out in
    # the query (is_unsynced below still decides).
    colorways = [
        colorway
        for colorway in fetch_all(
            client,
            "artisan_colorways",
            "id, maker_id, sculpt_id, maker_sculpt_id, colorway_id, img",
            lambda q: _with_image(q).not_.like("img", _like_prefix(base_url)),
        )
        if colorway["maker_sculpt_id"] in sculpts
    ]

    return [
        [
            Upload(
                table="keysets",
                key_column="profile_keyset_id",
                key_value=k["profile_keyset_id"],
                column=column,
                image_id=keyset_image_id(k, column),
                source=k[column],
                clear_on_404=True,
            )
            for k in keysets
            for column in keyset_columns
            if is_unsynced(k.get(column), base_url)
        ],
        [
            Upload(
                table="keyset_kits",
                key_column="id",
                key_value=k["id"],
                column="img",
                image_id=kit_image_id(k),
                source=k["img"],
                clear_on_404=True,
                cleared="",
            )
            for k in kits
            if is_unsynced(k.get("img"), base_url)
        ],
        [
            Upload(
                table="keyboard_variants",
                key_column="id",
                key_value=v["id"],
                column=column,
                image_id=variant_image_id(v, column),
                source=v[column],
                clear_on_404=False,
            )
            for v in variants
            for column in IMAGE_COLUMNS["keyboard_variants"]
            if is_unsynced(v.get(column), base_url)
        ],
        [
            Upload(
                table="artisan_colorways",
                key_column="id",
                key_value=c["id"],
                column="img",
                image_id=colorway_image_id(c),
                source=c["img"],
                clear_on_404=True,
            )
            for c in colorways
            if is_unsynced(c.get("img"), base_url)
        ],
    ]


def _update(client: Client, upload: Upload, value: Any) -> bool:
    try:
        client.table(upload.table).update({upload.column: value}).eq(
            upload.key_column, upload.key_value
        ).execute()
    except Exception:
        log.exception(
            "Unable to update %s %s=%s",
            upload.table,
            upload.key_column,
            upload.key_value,
        )
        return False
    return True


def _sync_one(
    client: Client, http: httpx.Client, base_url: str, upload: Upload
) -> bool:
    result = cf_images.upload_image(http, upload.image_id, upload.source)
    if result.ok:
        return _update(
            client, upload, cf_images.delivery_url(base_url, upload.image_id)
        )
    if result.status == 404 and upload.clear_on_404:
        _update(client, upload, upload.cleared)
    return False


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

    # keysets, then kits, then variants, then colorways, as catalog-sync did
    sync = partial(_sync_one, client, http, base_url)
    failed = sum(run_each(uploads, sync) for uploads in groups)
    if failed:
        log.warning("%d images were not synced (logged above)", failed)
