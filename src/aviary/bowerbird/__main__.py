"""bowerbird command line: `bowerbird scrape [--maker ID ...] [--dry-run]`."""

import argparse
import logging
import sys
from collections.abc import Sequence

from aviary.bowerbird import images
from aviary.bowerbird.apply import apply_plan, fetch_stored
from aviary.bowerbird.makers import PARTIAL_MAKERS, SCRAPERS
from aviary.bowerbird.plan import plan_maker_sync
from aviary.common import cf_images
from aviary.common.config import MissingSettingError
from aviary.common.db import create_supabase
from aviary.common.http import create_http_client
from aviary.common.report import configure_logging

log = logging.getLogger("bowerbird")

# The source column of the rows the website scrapers own, and the revision
# source Keebdex shows their changes under.
SCRAPER_SOURCE = "scraper"


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bowerbird", description="Sync artisan catalogs into Keebdex."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    scrape = commands.add_parser("scrape", help="sync makers from their websites")
    scrape.add_argument(
        "--maker",
        action="append",
        choices=sorted(SCRAPERS),
        help="sync only this maker (repeatable); every maker by default",
    )
    scrape.add_argument(
        "--dry-run",
        action="store_true",
        help="read Keebdex and log every write and upload instead of making it",
    )
    return parser.parse_args(argv)


def run_scrape(args: argparse.Namespace) -> int:
    try:
        client = create_supabase(SCRAPER_SOURCE)
        base_url = cf_images.delivery_base_url()
        http = create_http_client()
        existing_ids = cf_images.list_image_ids(http)
    except MissingSettingError as error:
        log.error("%s; see .env.example", error)
        return 1
    log.info("Cloudflare images: %d", len(existing_ids))

    failed = []
    with http:
        for maker_id in args.maker or sorted(SCRAPERS):
            log.info("Scraping %s", maker_id)
            try:
                tables = SCRAPERS[maker_id](http)
                tables = [
                    {
                        **table,
                        "colorways": [
                            images.point_at_cloudflare(c, base_url)
                            for c in table["colorways"]
                        ],
                    }
                    for table in tables
                ]
                stored_sculpts, stored_colorways = fetch_stored(
                    client, maker_id, SCRAPER_SOURCE
                )
                plan = plan_maker_sync(
                    tables,
                    stored_sculpts,
                    stored_colorways,
                    source=SCRAPER_SOURCE,
                    preserve_missing=maker_id in PARTIAL_MAKERS,
                )
                apply_plan(client, plan, dry_run=args.dry_run)
                # Failed uploads are logged and retried on the next run (their
                # ids are still missing); a dead source image must not fail
                # every run.
                images.upload_missing(
                    http, plan.colorways, existing_ids, base_url, dry_run=args.dry_run
                )
            except Exception:
                # keep going with the other makers, but fail the run
                log.exception("Maker %s failed", maker_id)
                failed.append(maker_id)

    if failed:
        log.error("Failed: %s", ", ".join(sorted(set(failed))))
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    if args.command == "scrape":
        return run_scrape(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
