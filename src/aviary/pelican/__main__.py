"""pelican command line: Cloudflare images and their R2 backups.

    pelican sync    [--dry-run]                    external images -> Cloudflare
    pelican restore [--dry-run] [--force]          referenced images back from R2
    pelican prune   [--dry-run]                    unreferenced images, a week old
    pelican backup  [--dry-run] [--limit N] [--force-trash]   Cloudflare -> R2

Unlike bowerbird, these exit 1 when something failed or looked wrong, as
catalog-sync's did: a failed backup or restore needs a person, and the next
scheduled run retries it anyway. A failed prune deletion only logs.
"""

import argparse
import logging
import sys
from collections.abc import Sequence

from aviary.common import cf_images
from aviary.common.config import MissingSettingError
from aviary.common.db import create_supabase, revision_source
from aviary.common.http import create_http_client
from aviary.common.r2 import Bucket
from aviary.common.report import configure_logging
from aviary.pelican import maintenance, sync

log = logging.getLogger("pelican")

# The revision source Keebdex shows sync's row changes under.
SYNC_SOURCE = revision_source("pelican", "cloudflare-images")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pelican", description="Keep Keebdex's images on Cloudflare and in R2."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("sync", "upload external images of published records to Cloudflare"),
        ("restore", "re-upload referenced images missing from Cloudflare, from R2"),
        ("prune", "delete Cloudflare images no record references"),
        ("backup", "copy new Cloudflare images to R2 and trash orphaned backups"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--dry-run",
            action="store_true",
            help="read everything and log what would change instead of changing it",
        )
        if name == "restore":
            command.add_argument(
                "--force",
                action="store_true",
                help=f"restore past {maintenance.MAX_RESTORE_RATIO:.0%} missing",
            )
        if name == "backup":
            command.add_argument(
                "--limit", type=int, help="back up at most N images (a trial run)"
            )
            command.add_argument(
                "--force-trash",
                action="store_true",
                help=f"trash past {maintenance.MAX_TRASH_RATIO:.0%} of the backups",
            )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    with create_http_client() as http:
        if args.command == "sync":
            client = create_supabase(SYNC_SOURCE)
            sync.run(client, http, cf_images.delivery_base_url(), dry_run=args.dry_run)
            return 0
        if args.command == "prune":
            failed = maintenance.prune(
                create_supabase(),
                http,
                cf_images.delivery_base_url(),
                dry_run=args.dry_run,
            )
            if failed:
                log.warning("%d images could not be deleted", failed)
            return 0
        if args.command == "restore":
            failed = maintenance.restore(
                create_supabase(),
                http,
                Bucket(),
                cf_images.delivery_base_url(),
                dry_run=args.dry_run,
                force=args.force,
            )
        else:
            failed = maintenance.backup(
                http,
                Bucket(),
                limit=args.limit,
                force_trash=args.force_trash,
                dry_run=args.dry_run,
            )
        if failed:
            log.error("%d failed", failed)
            return 1
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    try:
        return run(args)
    except MissingSettingError as error:
        log.error("%s; see .env.example", error)
    except maintenance.Refused as error:
        log.error("%s", error)
    except Exception:
        log.exception("pelican %s failed", args.command)
    return 1


if __name__ == "__main__":
    sys.exit(main())
