"""bowerbird command line.

    bowerbird gdoc   [--maker ID ...] [--dry-run]   makers' Google Docs
    bowerbird scrape [--maker ID ...] [--dry-run]   maker websites

A maker that fails (a broken doc, a site down, a database error) is logged and
skipped, and the run still exits 0, as catalog-sync's does: a deleted or broken
doc is routine and must not hold up the other makers. The failed makers are
listed at the end of the log and in the job summary.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
from supabase import Client

from aviary.bowerbird import images
from aviary.bowerbird.apply import apply_plan, fetch_stored
from aviary.bowerbird.gdoc import importer
from aviary.bowerbird.gdoc.google import DocNotFound, Google
from aviary.bowerbird.gdoc.parser import Regexes
from aviary.bowerbird.makers import PARTIAL_MAKERS, SCRAPERS
from aviary.bowerbird.plan import Row, SyncPlan, plan_maker_sync
from aviary.common import cf_images
from aviary.common.config import MissingSettingError
from aviary.common.db import create_supabase
from aviary.common.http import create_http_client
from aviary.common.report import configure_logging, write_summary

log = logging.getLogger("bowerbird")

# The `source` of the rows each command owns, which is also the revision
# source Keebdex shows their changes under.
SCRAPER_SOURCE = "scraper"


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bowerbird", description="Sync artisan catalogs into Keebdex."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text, makers in (
        ("gdoc", "sync makers from their Google Docs", None),
        ("scrape", "sync makers from their websites", sorted(SCRAPERS)),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--maker",
            action="append",
            choices=makers,
            help="sync only this maker (repeatable); every maker by default",
        )
        command.add_argument(
            "--dry-run",
            action="store_true",
            help="read Keebdex and log every write and upload instead of making it",
        )
    return parser.parse_args(argv)


@dataclass
class Run:
    client: Client
    http: httpx.Client
    base_url: str
    existing_ids: set[str]
    dry_run: bool

    def sync(
        self,
        maker_id: str,
        tables: list[Row],
        source: str,
        *,
        preserve_missing: bool,
        available_document_ids: Sequence[str] = (),
    ) -> SyncPlan:
        stored_sculpts, stored_colorways, other_sculpts = fetch_stored(
            self.client, maker_id, source
        )
        plan = plan_maker_sync(
            tables,
            stored_sculpts,
            stored_colorways,
            source=source,
            preserve_missing=preserve_missing,
            available_document_ids=set(available_document_ids),
            other_sculpts=other_sculpts,
        )
        apply_plan(self.client, plan, dry_run=self.dry_run)
        return plan

    def upload_images(self, plan: SyncPlan) -> None:
        # Failed uploads are logged and retried on the next run (their ids
        # are still missing); a dead source image must not fail the maker.
        images.upload_missing(
            self.http,
            plan.colorways,
            self.existing_ids,
            self.base_url,
            dry_run=self.dry_run,
        )


def start(source: str, dry_run: bool) -> Run:
    http = create_http_client()
    existing_ids = cf_images.list_image_ids(http)
    log.info("Cloudflare images: %d", len(existing_ids))
    return Run(
        client=create_supabase(source),
        http=http,
        base_url=cf_images.delivery_base_url(),
        existing_ids=existing_ids,
        dry_run=dry_run,
    )


def sync_websites(run: Run, maker_ids: list[str]) -> list[str]:
    failed = []
    for maker_id in maker_ids:
        log.info("Scraping %s", maker_id)
        try:
            tables = [
                {
                    **table,
                    "colorways": [
                        images.point_at_cloudflare(c, run.base_url)
                        for c in table["colorways"]
                    ],
                }
                for table in SCRAPERS[maker_id](run.http)
            ]
            plan = run.sync(
                maker_id,
                tables,
                SCRAPER_SOURCE,
                preserve_missing=maker_id in PARTIAL_MAKERS,
            )
            run.upload_images(plan)
        except Exception:
            log.exception("Maker %s failed", maker_id)
            failed.append(maker_id)
    return failed


def disable(run: Run, maker_id: str) -> None:
    log.warning(
        "Every doc of %s is gone: disabling its sync, marking it deleted", maker_id
    )
    importer.update_metadata(
        run.client,
        maker_id,
        {"disable_google_sync": True, "deleted": True},
        dry_run=run.dry_run,
    )


def sync_documents(run: Run, maker_ids: list[str] | None) -> list[str]:
    google = Google(run.http)
    # one set of regexes for the whole run: their state carries over between
    # makers, as catalog-sync's module-level ones do
    rx = Regexes()
    failed = []
    for maker in importer.fetch_makers(run.client, maker_ids):
        maker_id = maker["maker_id"]
        log.info("Syncing %s from %d doc(s)", maker_id, len(maker["document_ids"]))
        try:
            tables, available, missing = importer.load_catalog(
                google, maker, run.base_url, rx
            )
            if tables is None:
                disable(run, maker_id)
                continue
            plan = run.sync(
                maker_id,
                tables,
                importer.SOURCE,
                preserve_missing=maker_id in PARTIAL_MAKERS or missing,
                available_document_ids=available,
            )
            if importer.rows_modified(plan):
                contributors, modified_time = importer.contributors_from(
                    google, available[-1], maker.get("contributors") or []
                )
                importer.update_metadata(
                    run.client,
                    maker_id,
                    {"contributors": contributors, "updated_at": modified_time},
                    dry_run=run.dry_run,
                )
            run.upload_images(plan)
        except DocNotFound:
            disable(run, maker_id)
        except Exception:
            log.exception("Maker %s failed", maker_id)
            failed.append(maker_id)
    return failed


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    source = importer.SOURCE if args.command == "gdoc" else SCRAPER_SOURCE
    try:
        run = start(source, args.dry_run)
        # start() already used the client, so it can't be entered with `with`
        try:
            if args.command == "gdoc":
                failed = sync_documents(run, args.maker)
            else:
                failed = sync_websites(run, args.maker or sorted(SCRAPERS))
        finally:
            run.http.close()
    except MissingSettingError as error:
        log.error("%s; see .env.example", error)
        return 1

    if failed:
        log.error("Makers that failed and were skipped: %s", ", ".join(failed))
        write_summary(
            f"## bowerbird {args.command}\n\nFailed and skipped: "
            + ", ".join(f"`{m}`" for m in failed)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
