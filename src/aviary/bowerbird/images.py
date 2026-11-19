"""Colorway images: the Cloudflare id each one is stored under, and uploads.

A colorway's `img` holds its Cloudflare delivery URL, decided before the row is
written, and `remote_img` the source URL to upload from. The id never changes
for a given colorway: a synced image keeps the path it was first uploaded to,
which still holds the sculpt's source slug after a rename in Keebdex.
"""

import logging

import httpx

from aviary.common import cf_images
from aviary.common.db import Row
from aviary.common.pool import run_each

log = logging.getLogger(__name__)


def image_id(colorway: Row, base_url: str) -> str:
    """catalog-sync's makeImageId: the id the colorway's Cloudflare URL
    already holds, else one built from its keys."""
    img = colorway.get("img")
    stored = cf_images.to_image_id(img, base_url)
    if stored and stored.startswith("artisan/") and img.endswith("/public"):
        return stored
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
        "img": cf_images.delivery_url(base_url, image_id(colorway, base_url)),
    }


def upload_missing(
    client: httpx.Client,
    colorways: list[Row],
    existing_ids: set[str],
    base_url: str,
    *,
    dry_run: bool,
) -> None:
    """Upload, a few at a time, the source image of every colorway whose id
    Cloudflare lacks, skipping images locked in the app. Failures are logged;
    their ids stay missing, so the next run retries them."""
    pending = {
        image: c["remote_img"]
        for c in colorways
        if c.get("remote_img") and not c.get("img_overridden")
        if (image := image_id(c, base_url)) not in existing_ids
    }
    if not pending:
        return
    if dry_run:
        log.info("[DRY RUN] Would upload %d images", len(pending))
        return

    uploaded: set[str] = set()

    def upload(image: str) -> bool:
        ok = cf_images.upload_image(client, image, pending[image]).ok
        if ok:
            uploaded.add(image)
        return ok

    run_each(pending, upload)
    existing_ids |= uploaded
    log.info("Images uploaded: %d of %d", len(uploaded), len(pending))
