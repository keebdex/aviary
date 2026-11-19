"""lorikeet command line: `lorikeet ral [--dry-run | --csv PATH]`."""

import argparse
import csv
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from aviary.common.config import MissingSettingError
from aviary.common.db import create_supabase
from aviary.common.report import configure_logging, write_summary
from aviary.lorikeet import ral
from aviary.lorikeet.sync import (
    Plan,
    SanityCheckError,
    count_by_range,
    fetch_stored,
    insert_colors,
    plan_sync,
)

log = logging.getLogger("lorikeet")

# Differences listed in the log and job summary; the rest are only counted.
MAX_LISTED_DIFFERENCES = 50


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lorikeet", description="Sync reference color palettes into Keebdex."
    )
    palettes = parser.add_subparsers(dest="palette", required=True)
    ral_parser = palettes.add_parser(
        "ral", help="RAL Classic, Design, and Effect colors from ralcolorchart.com"
    )
    mode = ral_parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="read Keebdex and report what would be added, without writing",
    )
    mode.add_argument(
        "--csv",
        type=Path,
        metavar="PATH",
        help="write the crawl to a CSV file instead of syncing (no database)",
    )
    return parser.parse_args(argv)


def write_csv(colors: Sequence[ral.Color], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["system", "code", "name", "hex"])
        for color in colors:
            writer.writerow([color.system, color.code, color.name or "", color.hex])


def summarize(plan: Plan, crawled: int, dry_run: bool) -> str:
    verb = "Would add" if dry_run else "Added"
    lines = [
        "## lorikeet: RAL",
        "",
        f"- Crawled: {crawled}",
        f"- {verb}: {len(plan.new)}",
        f"- Unchanged: {plan.unchanged}",
        f"- Different from Keebdex (left as stored): {len(plan.different)}",
    ]
    if plan.new:
        by_range = count_by_range(plan.new)
        lines.append(
            "- New by range: "
            + ", ".join(f"{name} {count}" for name, count in sorted(by_range.items()))
        )
    if plan.different:
        lines += [
            "",
            "| Code | Stored | Crawled |",
            "| ---- | ------ | ------- |",
        ]
        for diff in plan.different[:MAX_LISTED_DIFFERENCES]:
            stored = f"{diff.stored_name or ''} {diff.stored_hex}".strip()
            crawled_value = f"{diff.crawled.name or ''} {diff.crawled.hex}".strip()
            lines.append(f"| {diff.crawled.code} | {stored} | {crawled_value} |")
        if len(plan.different) > MAX_LISTED_DIFFERENCES:
            lines.append("")
            lines.append(f"…and {len(plan.different) - MAX_LISTED_DIFFERENCES} more.")
    return "\n".join(lines)


def run_ral(args: argparse.Namespace) -> int:
    # Settings are checked before the crawl, which takes a while.
    try:
        client = None if args.csv else create_supabase()
    except MissingSettingError as error:
        log.error("%s; see .env.example", error)
        return 1

    try:
        colors = ral.crawl()
    except ral.CrawlError as error:
        log.error("Crawl failed, nothing written: %s", error)
        return 1

    by_range = count_by_range(colors)
    log.info(
        "Crawled %d colors (%s)",
        len(colors),
        ", ".join(f"{name} {count}" for name, count in sorted(by_range.items())),
    )

    if client is None:
        write_csv(colors, args.csv)
        log.info("Wrote %s", args.csv)
        return 0

    stored = fetch_stored(client, "RAL")
    try:
        plan = plan_sync(colors, stored)
    except SanityCheckError as error:
        log.error("%s, nothing written", error)
        return 1

    if plan.new and not args.dry_run:
        insert_colors(client, plan.new)

    summary = summarize(plan, len(colors), args.dry_run)
    log.info("\n%s", summary)
    write_summary(summary)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    if args.palette == "ral":
        return run_ral(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
