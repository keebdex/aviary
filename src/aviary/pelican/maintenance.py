"""Restore, prune, and back up Cloudflare images: ports of catalog-sync's
jobs/restore-images.js, prune-images.js, and backup-images.js.

Each refuses to act on numbers that look like a bad read (no images listed,
too many missing or orphaned at once), since acting on them would delete or
re-upload everything. Their thresholds and overrides are the ones catalog-sync
used.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx
from supabase import Client

from aviary.common import cf_images
from aviary.common.pool import run_each
from aviary.common.r2 import IMAGES_PREFIX, TRASH_PREFIX, Bucket
from aviary.pelican.references import referenced_images

log = logging.getLogger(__name__)

# Images uploaded in the Keebdex app before their record is saved (e.g. a
# submission still being filled in) aren't referenced yet: never prune them.
MIN_PRUNE_AGE_SECONDS = 7 * 24 * 60 * 60
# Restoring is for the rare record that points at a pruned image again (an
# Undo, a Restore, an approved suggestion); many at once means a bad listing.
MAX_RESTORE_RATIO = 0.1
# A bad listing would make every backup look orphaned.
MAX_TRASH_RATIO = 0.1


class Refused(RuntimeError):
    """The numbers look like a bad read; nothing was changed."""


def _references_and_images(
    client: Client, http: httpx.Client, base_url: str
) -> tuple[set[str], list[cf_images.CloudflareImage]]:
    """The referenced image ids and the Cloudflare listing, read at once."""
    with ThreadPoolExecutor(2) as pool:
        referenced = pool.submit(referenced_images, client, base_url)
        remote = pool.submit(cf_images.list_images, http)
        return referenced.result(), remote.result()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# Restore -------------------------------------------------------------------


def restore(
    client: Client,
    http: httpx.Client,
    bucket: Bucket,
    base_url: str,
    *,
    dry_run: bool,
    force: bool,
) -> int:
    """Re-upload, from R2 and under the same id, every referenced image
    Cloudflare no longer has. Returns how many failed."""
    referenced, remote = _references_and_images(client, http, base_url)
    if not remote:
        raise Refused("No Cloudflare images listed, refusing to restore")

    remote_ids = {image.id for image in remote}
    missing = sorted(referenced - remote_ids)
    log.info("Referenced %d, on Cloudflare %d, missing %d: %s",
             len(referenced), len(remote), len(missing), missing)  # fmt: skip
    if not force and len(missing) > len(referenced) * MAX_RESTORE_RATIO:
        raise Refused(
            f"{len(missing)} of {len(referenced)} referenced images are missing, "
            "refusing (rerun with --force if intended)"
        )
    if dry_run:
        log.info("[DRY RUN] Would restore up to %d images", len(missing))
        return 0

    def restore_one(image_id) -> bool:
        try:
            backup = bucket.get(f"{IMAGES_PREFIX}{image_id}")
        except Exception as error:
            log.error("Unable to read the backup of %s: %s", image_id, error)
            return False
        if backup is None:
            log.warning("No backup to restore %s from", image_id)
            return True
        return cf_images.upload_file(http, image_id, backup).ok

    failed = run_each(missing, restore_one)
    log.info("Restored %d images", len(missing) - failed)
    return failed


# Prune ---------------------------------------------------------------------


def prune(client: Client, http: httpx.Client, base_url: str, *, dry_run: bool) -> int:
    """Delete Cloudflare images no row references, older than a week.
    Returns how many deletions failed (logged, not fatal, as catalog-sync)."""
    referenced, remote = _references_and_images(client, http, base_url)
    if not referenced:
        raise Refused("No referenced images found, refusing to prune")

    cutoff = time.time() - MIN_PRUNE_AGE_SECONDS
    orphaned = [
        image.id
        for image in remote
        if image.id not in referenced
        and _parse_time(image.uploaded).timestamp() < cutoff
    ]
    log.info("Referenced %d, on Cloudflare %d, to prune %d: %s",
             len(referenced), len(remote), len(orphaned), orphaned)  # fmt: skip
    if dry_run:
        log.info("[DRY RUN] Would delete %d images", len(orphaned))
        return 0
    return run_each(orphaned, lambda image_id: cf_images.delete_image(http, image_id))


# Backup --------------------------------------------------------------------


def is_unlocked(key: str, modified: datetime, rules: list[dict], now: datetime) -> bool:
    """Whether the bucket's lock rules, managed outside this repo, let a
    backup leave images/: no enabled rule covering its key still retains it.
    An unknown condition counts as retaining, and so does a rule with a null
    prefix: it covers every key here, where catalog-sync's startsWith(null)
    ignored it, which kept less."""
    for rule in rules:
        if not rule.get("enabled") or not key.startswith(rule.get("prefix") or ""):
            continue
        condition = rule.get("condition") or {}
        kind = condition.get("type")
        if kind == "Age":
            if (now - modified).total_seconds() < condition["maxAgeSeconds"]:
                return False
        elif kind == "Date":
            if now < _parse_time(condition["date"]):
                return False
        else:
            return False
    return True


def _back_up_missing(
    http: httpx.Client,
    bucket: Bucket,
    images: list[cf_images.CloudflareImage],
    backed_up: dict[str, datetime],
    *,
    limit: int | None,
    dry_run: bool,
) -> int:
    # each image is fetched once: a backed-up id is never fetched again
    missing = [i for i in images if f"{IMAGES_PREFIX}{i.id}" not in backed_up][:limit]
    log.info("Images to back up: %d", len(missing))
    if dry_run:
        log.info("[DRY RUN] Would back up %s", [i.id for i in missing])
        return 0

    def back_up(image: cf_images.CloudflareImage) -> bool:
        try:
            file = cf_images.download_original(http, image.id)
            bucket.put(
                f"{IMAGES_PREFIX}{image.id}",
                file.data,
                file.content_type,
                {"uploaded": image.uploaded},
            )
        except cf_images.ImageNotFound:
            return True  # deleted since it was listed
        except Exception as error:
            log.error("Unable to back up %s: %s", image.id, error)
            return False
        return True

    failed = run_each(missing, back_up)
    log.info("Backed up %d images", len(missing) - failed)
    return failed


def _trash_orphaned(
    bucket: Bucket,
    images: list[cf_images.CloudflareImage],
    backed_up: dict[str, datetime],
    rules: list[dict],
    *,
    force: bool,
    dry_run: bool,
) -> int:
    if not images:
        raise Refused("No Cloudflare images listed, refusing to trash")
    remote_ids = {image.id for image in images}
    now = datetime.now(UTC)
    unused = [
        (key, modified)
        for key, modified in backed_up.items()
        if key[len(IMAGES_PREFIX) :] not in remote_ids
    ]
    orphaned = [
        key for key, modified in unused if is_unlocked(key, modified, rules, now)
    ]
    log.info("Unused backups %d, still locked %d, to trash %d",
             len(unused), len(unused) - len(orphaned), len(orphaned))  # fmt: skip
    if not force and len(orphaned) > len(backed_up) * MAX_TRASH_RATIO:
        raise Refused(
            f"{len(orphaned)} of {len(backed_up)} backups would be trashed, "
            "refusing (rerun with --force-trash if intended)"
        )
    if dry_run:
        log.info("[DRY RUN] Would trash %s", orphaned)
        return 0

    def trash(key: str) -> bool:
        try:
            bucket.move(key, f"{TRASH_PREFIX}{key[len(IMAGES_PREFIX) :]}")
        except Exception as error:
            log.error("Unable to trash %s: %s", key, error)
            return False
        return True

    failed = run_each(orphaned, trash)
    log.info("Trashed %d backups", len(orphaned) - failed)
    return failed


def backup(
    http: httpx.Client,
    bucket: Bucket,
    *,
    limit: int | None,
    force_trash: bool,
    dry_run: bool,
) -> int:
    """Copy new Cloudflare images to R2 (images/<id>), then move the backups of
    images gone from Cloudflare to trash/ once the bucket's lock rules allow.
    Returns how many backups and moves failed."""
    images = cf_images.list_images(http)
    backed_up = bucket.list_objects(IMAGES_PREFIX)
    log.info("On Cloudflare %d, backed up %d", len(images), len(backed_up))

    failed = _back_up_missing(
        http, bucket, images, backed_up, limit=limit, dry_run=dry_run
    )

    # rules that can't be read count as a lock on everything
    try:
        rules = bucket.lock_rules(http)
    except Exception as error:
        log.warning("Unable to read the bucket lock rules, skipping trash: %s", error)
        return failed
    return failed + _trash_orphaned(
        bucket, images, backed_up, rules, force=force_trash, dry_run=dry_run
    )
