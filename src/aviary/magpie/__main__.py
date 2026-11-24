"""magpie command line.

    magpie crawl [--board ic|gb ...] [--max-pages N] [--fetch-limit N] [--dry-run]
    magpie promote [--limit N] [--max-age-days N] [--dry-run]
    magpie reextract [--limit N] [--dry-run]
    magpie topic <id|url>

`crawl` is one `ingest.crawl_runs` run: it lists the boards down to the last
run's watermark into `ingest.threads`, fetches the first post of due threads,
and adds what it reads as keyset candidates to `ingest.candidates`. `promote`
inserts the complete new keyset candidates into Keebdex as Pending keysets
with their kits (revision source `magpie.geekhack`); the rest wait for staff. `topic`
prints one topic as JSON, without a database.
"""

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import asdict

from aviary.common.config import MissingSettingError
from aviary.common.report import configure_logging
from aviary.magpie.classify import suggest
from aviary.magpie.crawl import run_crawl
from aviary.magpie.fetch import Fetcher, FetchError
from aviary.magpie.geekhack import BOARDS, extract_topic_id, topic_url
from aviary.magpie.parsers import parse_topic
from aviary.magpie.promote import run_promote
from aviary.magpie.reextract import run_reextract

log = logging.getLogger("magpie")

DEFAULT_MAX_PAGES = 5
DEFAULT_FETCH_LIMIT = 100
DEFAULT_PROMOTE_LIMIT = 25
DEFAULT_MAX_AGE_DAYS = 90


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="magpie",
        description="Crawl Geekhack interest checks and group buys for Keebdex.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    crawl = commands.add_parser("crawl", help="list boards, fetch threads, extract")
    crawl.add_argument(
        "--board",
        action="append",
        choices=sorted(BOARDS),
        help="list only this board (repeatable; default: every board)",
    )
    crawl.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        metavar="N",
        help="list at most N pages per board; raise it once to backfill "
        f"(default {DEFAULT_MAX_PAGES})",
    )
    crawl.add_argument(
        "--fetch-limit",
        type=int,
        default=DEFAULT_FETCH_LIMIT,
        metavar="N",
        help=f"fetch at most N due threads (default {DEFAULT_FETCH_LIMIT})",
    )

    promote = commands.add_parser("promote", help="insert complete keyset candidates")
    promote.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_PROMOTE_LIMIT,
        metavar="N",
        help=f"insert at most N keysets (default {DEFAULT_PROMOTE_LIMIT})",
    )
    promote.add_argument(
        "--max-age-days",
        type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        metavar="N",
        help=f"only topics posted in the last N days (default {DEFAULT_MAX_AGE_DAYS})",
    )

    reextract = commands.add_parser(
        "reextract", help="extract stored first posts again (no requests to Geekhack)"
    )
    reextract.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="extract at most N threads, oldest first (default: all)",
    )

    for command in (crawl, promote, reextract):
        command.add_argument(
            "--dry-run",
            action="store_true",
            help="read Keebdex and log every write instead of making it",
        )

    topic = commands.add_parser("topic", help="print one topic as JSON (no database)")
    topic.add_argument("topic", help="topic id or URL")
    args = parser.parse_args(argv)
    if args.command == "crawl" and not 1 <= args.fetch_limit <= 1000:
        parser.error("--fetch-limit must be between 1 and 1000")
    if args.command == "reextract" and args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.command == "crawl" and args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    return args


def run_topic(args: argparse.Namespace) -> int:
    topic_id = extract_topic_id(args.topic)
    fetcher = Fetcher()
    try:
        detail = parse_topic(fetcher.get(topic_url(topic_id)))
    except (FetchError, ValueError) as error:
        log.error("Topic %d: %s", topic_id, error)
        return 1
    finally:
        fetcher.close()
    output = {"topic_id": topic_id, "url": topic_url(topic_id), **asdict(detail)}
    del output["body"]
    output["suggestion"] = asdict(suggest(detail.title))
    sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return 0


COMMANDS = {
    "crawl": run_crawl,
    "promote": run_promote,
    "reextract": run_reextract,
    "topic": run_topic,
}


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except MissingSettingError as error:
        log.error("%s; see .env.example", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
