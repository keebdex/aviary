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
import hashlib
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import batched

from supabase import Client

from aviary.common.config import MissingSettingError
from aviary.common.db import Row, create_supabase, revision_source
from aviary.common.report import configure_logging, write_summary
from aviary.magpie import apply
from aviary.magpie.classify import suggest
from aviary.magpie.fetch import Fetcher, FetchError
from aviary.magpie.geekhack import (
    BOARDS,
    board_url,
    extract_topic_id,
    parse_time,
    topic_url,
)
from aviary.magpie.parsers import parse_board, parse_post, parse_topic
from aviary.magpie.plan import (
    BoardListing,
    KeysetIndex,
    PromotionPlan,
    plan_listing,
    plan_promotions,
    refetch_at,
    retry_at,
    stored_time,
)
from aviary.magpie.proposal import EXTRACTOR_VERSION, MODULE, Extraction, Topic, extract

log = logging.getLogger("magpie")

# The revision source Keebdex shows magpie's keysets under: the job, then the
# site its candidates come from.
SOURCE = revision_source("magpie", "geekhack")
DEFAULT_MAX_PAGES = 5
DEFAULT_FETCH_LIMIT = 100
DEFAULT_PROMOTE_LIMIT = 25
DEFAULT_MAX_AGE_DAYS = 90
# ingest.threads limits
MAX_BODY_CHARS = 500_000
MAX_IMAGES = 100
MAX_ERROR_CHARS = 2000
# Rows listed in the log and job summary; the rest are only counted.
MAX_LISTED = 50


class CrawlError(Exception):
    """A board listing that can't be trusted; nothing is written."""


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


def _topic_link(topic_id: int | str) -> str:
    return f"[{topic_id}]({topic_url(int(topic_id))})"


# crawl


def list_board(
    fetcher: Fetcher, board: str, watermark: datetime | None, max_pages: int
) -> BoardListing:
    """Read a board page by page until the listing is done or the cap."""
    listing = BoardListing(board, watermark)
    offset: int | None = 0
    while offset is not None and listing.pages < max_pages:
        page = parse_board(fetcher.get(board_url(board, offset)), offset)
        if not page.topics:
            raise CrawlError(
                f"board {board}: no topics on page {listing.pages + 1}, "
                "has the layout changed?"
            )
        listing.add_page(page)
        log.info(
            "Board %s page %d: %d threads", board, listing.pages, len(listing.seen)
        )
        if listing.done:
            break
        offset = page.next_offset
    return listing


@dataclass
class Counters:
    pages_listed: int = 0
    threads_listed: int = 0
    threads_new: int = 0
    threads_fetched: int = 0
    threads_changed: int = 0
    threads_failed: int = 0
    candidates: int = 0
    candidates_restored: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    # keyset titles whose keycap code names no Keebdex profile, by code, and
    # how many keyset titles had no code at all: profiles staff may add
    unknown_profiles: dict[str, list[str]] = field(default_factory=dict)
    no_profile_code: int = 0

    def columns(self) -> Row:
        """The `crawl_runs` counters."""
        values = asdict(self)
        for only_reported in (
            "candidates",
            "candidates_restored",
            "failures",
            "unknown_profiles",
            "no_profile_code",
        ):
            del values[only_reported]
        return values

    def count_profile(self, extraction: Extraction, title: str) -> None:
        if extraction.profile_id is not None:
            return
        if extraction.profile_code is None:
            self.no_profile_code += 1
        else:
            self.unknown_profiles.setdefault(extraction.profile_code, []).append(title)


def candidate_row(
    thread: Row, content_hash: str, extraction: Extraction, index: KeysetIndex
) -> Row:
    """The `ingest.candidates` row of a thread's extraction, with the
    match_key of the keyset Keebdex has for it."""
    match = index.match(int(thread["external_id"]), extraction.key)
    return {
        "thread_id": thread["id"],
        "module": MODULE,
        "extractor_version": EXTRACTOR_VERSION,
        "content_hash": content_hash,
        "payload": extraction.payload,
        "confidence": extraction.confidence,
        "match_key": match["profile_keyset_id"] if match else None,
    }


@dataclass
class Crawl:
    client: Client
    fetcher: Fetcher
    profiles: list[Row]
    index: KeysetIndex
    dry_run: bool
    counters: Counters = field(default_factory=Counters)

    def fetch(self, thread: Row) -> None:
        """Fetch a thread's first post, store it, and add its candidate."""
        now = datetime.now(UTC)
        topic_id = int(thread["external_id"])
        try:
            detail = parse_topic(self.fetcher.get(topic_url(topic_id)))
        except (FetchError, ValueError) as error:
            self.fail(thread, error, now)
            return
        if detail.dropped_image_urls:
            log.warning(
                "Topic %d: dropped %d invalid image URLs",
                topic_id,
                detail.dropped_image_urls,
            )

        body = detail.body
        if len(body) > MAX_BODY_CHARS:
            log.warning(
                "Topic %d: first post cut to %d characters", topic_id, MAX_BODY_CHARS
            )
            body = body[:MAX_BODY_CHARS]
        content_hash = hashlib.sha256(body.encode()).hexdigest()
        posted_at = parse_time(detail.posted_at)
        topic = Topic(
            topic_id=topic_id,
            board=thread["board"],
            title=thread["title"] or detail.title,
            author=detail.author or thread.get("author") or "",
            posted_at=posted_at,
            kit_images=detail.kit_images,
        )
        extraction = extract(topic, self.profiles)
        next_fetch = refetch_at(posted_at, now)
        values = {
            "raw_body": body,
            "content_hash": content_hash,
            "fetched_at": now.isoformat(),
            "fetch_attempts": 0,
            "fetch_error": None,
            "author": topic.author or None,
            "posted_at": posted_at.isoformat() if posted_at else None,
            "images": detail.image_urls[:MAX_IMAGES],
            "name": extraction.name if extraction else None,
            "profile_id": extraction.profile_id if extraction else None,
            "next_fetch_at": next_fetch.isoformat() if next_fetch else None,
        }
        if content_hash != thread.get("content_hash"):
            values["changed_at"] = now.isoformat()
            if thread.get("content_hash") is not None:
                self.counters.threads_changed += 1
        apply.update_thread(self.client, thread, values, self.dry_run)
        self.counters.threads_fetched += 1

        if extraction is None:
            return
        self.counters.count_profile(extraction, topic.title)
        candidate = candidate_row(thread, content_hash, extraction, self.index)
        saved = apply.save_candidate(self.client, candidate, self.dry_run)
        if saved == apply.ADDED:
            self.counters.candidates += 1
        elif saved == apply.RESTORED:
            self.counters.candidates_restored += 1

    def fail(self, thread: Row, error: Exception, now: datetime) -> None:
        """A failed fetch is routine (a deleted topic, a hiccup): back off,
        and stop after a few in a row until a listing shows new activity. A
        404 marks the thread gone."""
        topic_id = thread["external_id"]
        message = str(error)[:MAX_ERROR_CHARS]
        log.warning("Topic %s: %s", topic_id, message)
        self.counters.threads_failed += 1
        self.counters.failures.append((topic_id, message))
        if isinstance(error, FetchError) and error.status == 404:
            values = {"gone_at": now.isoformat(), "next_fetch_at": None}
        else:
            attempts = (thread.get("fetch_attempts") or 0) + 1
            retry = retry_at(attempts, now)
            values = {
                "fetch_attempts": attempts,
                "next_fetch_at": retry.isoformat() if retry else None,
            }
        values["fetch_error"] = message
        apply.update_thread(self.client, thread, values, self.dry_run)


def crawl_summary(
    dry_run: bool, counters: Counters, incomplete: list[str], error: str | None
) -> str:
    lines = [
        "## magpie crawl",
        "",
        f"- Pages listed: {counters.pages_listed}",
        f"- Threads listed: {counters.threads_listed} ({counters.threads_new} new)",
        f"- Threads fetched: {counters.threads_fetched}"
        f" ({counters.threads_changed} with an edited first post)",
        f"- {'Would add' if dry_run else 'Added'} keyset candidates: "
        f"{counters.candidates}",
        "- Keyset candidates waiting again (first post reverted to an earlier"
        f" version): {counters.candidates_restored}",
        f"- Failed fetches: {counters.threads_failed}",
    ]
    lines += [f"  - {_topic_link(t)}: {e}" for t, e in counters.failures[:MAX_LISTED]]
    if incomplete:
        lines.append(
            "- Stopped by --max-pages before reaching the last run's threads: "
            f"{', '.join(incomplete)} (run once with more pages to fill the gap)"
        )
    if error:
        lines.append(f"- **Failed**: {error}")
    lines += _unknown_profiles(counters)
    return "\n".join(lines)


# Titles shown per unknown profile code.
MAX_TITLES_PER_CODE = 3


def _unknown_profiles(counters: Counters) -> list[str]:
    """Keycap codes keyset titles start with that name no Keebdex profile,
    most threads first: candidates for a new profile."""
    if not counters.unknown_profiles and not counters.no_profile_code:
        return []
    lines = ["", "### Unknown profiles", ""]
    by_count = sorted(
        counters.unknown_profiles.items(), key=lambda item: (-len(item[1]), item[0])
    )
    for code, titles in by_count[:MAX_LISTED]:
        shown = "; ".join(titles[:MAX_TITLES_PER_CODE])
        more = (
            f" (+{len(titles) - MAX_TITLES_PER_CODE} more)"
            if len(titles) > MAX_TITLES_PER_CODE
            else ""
        )
        lines.append(f"- `{code}`: {_threads(len(titles))}: {shown}{more}")
    if counters.no_profile_code:
        lines.append(
            f"- No profile code in the title: {_threads(counters.no_profile_code)}"
        )
    return lines


def _threads(count: int) -> str:
    return f"{count} thread" if count == 1 else f"{count} threads"


def run_crawl(args: argparse.Namespace) -> int:
    client = create_supabase()
    profiles = apply.fetch_profiles(client)
    keysets = apply.fetch_keysets(client)
    # Keebdex holds hundreds of both: an empty read is a bad read, and every
    # candidate would miss its profile and match
    if not profiles or not keysets:
        log.error(
            "Read %d profiles and %d keysets, nothing written",
            len(profiles),
            len(keysets),
        )
        return 1
    boards = args.board or sorted(BOARDS)
    previous = apply.last_watermarks(client)
    now = datetime.now(UTC)
    run_id = None
    if not args.dry_run:
        try:
            run_id = apply.start_run(client, args.max_pages, now)
        except apply.RunningError as error:
            log.error("%s; not starting another", error)
            return 1

    fetcher = Fetcher()
    crawl = Crawl(client, fetcher, profiles, KeysetIndex(keysets), args.dry_run)
    counters = crawl.counters
    listings: list[BoardListing] = []
    error: str | None = None
    try:
        # every board is read before anything is written: a listing that
        # fails part-way writes nothing
        listings = [
            list_board(fetcher, board, previous.get(board), args.max_pages)
            for board in boards
        ]
        stored = apply.fetch_threads(client)
        for listing in listings:
            plan = plan_listing(listing, stored, now)
            apply.save_listing(client, listing.board, plan, args.dry_run)
            counters.pages_listed += listing.pages
            counters.threads_listed += len(listing.seen)
            counters.threads_new += plan.new
        due = apply.due_threads(client, args.fetch_limit, datetime.now(UTC))
        log.info("Fetching %d due threads", len(due))
        for thread in due:
            crawl.fetch(thread)
    except (FetchError, CrawlError) as failure:
        error = str(failure)
        log.error("Crawl failed: %s", error)
    except Exception as failure:
        error = f"unexpected error: {failure}"
        raise
    finally:
        fetcher.close()
        incomplete = [listing.board for listing in listings if not listing.done]
        if run_id is not None:
            finish(client, run_id, previous, listings, counters, incomplete, error)
    summary = crawl_summary(args.dry_run, counters, incomplete, error)
    log.info("\n%s", summary)
    write_summary(summary)
    return 1 if error else 0


def finish(
    client: Client,
    run_id: int,
    previous: dict[str, datetime],
    listings: list[BoardListing],
    counters: Counters,
    incomplete: list[str],
    error: str | None,
) -> None:
    """Close the run. Only a succeeded run moves watermarks; boards it did not
    list keep theirs."""
    watermarks = dict(previous)
    if error is None:
        for listing in listings:
            if (mark := listing.next_watermark()) is not None:
                watermarks[listing.board] = mark
    apply.finish_run(
        client,
        run_id,
        {
            **counters.columns(),
            "status": "failed" if error else "succeeded",
            "finished_at": datetime.now(UTC).isoformat(),
            "watermarks": {b: w.isoformat() for b, w in watermarks.items()},
            "incomplete_boards": incomplete,
            "error": error[:MAX_ERROR_CHARS] if error else None,
        },
    )


# promote


def promote_summary(
    plan: PromotionPlan, result: apply.PromotionResult, dry_run: bool
) -> str:
    verb = "Would insert" if dry_run else "Inserted"
    failed, triaged = result.failed, result.triaged
    inserted = [p for p in plan.promotions if p.topic_id not in failed + triaged]
    differences = [r for r in plan.rematches if r.difference]
    lines = [
        "## magpie promote",
        "",
        f"- {verb} (Pending): {len(inserted)}",
        f"- Matched to a keyset Keebdex has (left for staff): {len(plan.rematches)}",
        f"- Incomplete (left for staff): {len(plan.incomplete)}",
        f"- Older than the cut-off (left for staff): {plan.old}",
        f"- Left for the next run (limit): {plan.deferred}",
        f"- Triaged by staff meanwhile (keyset removed again): {len(triaged)}",
        f"- Failed: {len(failed)}",
    ]
    if inserted:
        lines += ["", f"### {verb}", ""]
        lines += [
            f"- `{p.key}` ({len(p.payload['kits'])} kits)"
            f" from {_topic_link(p.topic_id)}"
            for p in inserted[:MAX_LISTED]
        ]
    if differences:
        lines += ["", "### Different from Keebdex (left as stored)", ""]
        lines += [
            f"- `{r.key}` ({_topic_link(r.topic_id)}): {r.difference}"
            for r in differences[:MAX_LISTED]
        ]
    if plan.incomplete:
        lines += ["", "### Incomplete", ""]
        lines += [f"- {_topic_link(t)}: {m}" for t, m in plan.incomplete[:MAX_LISTED]]
    if triaged:
        lines += ["", "### Triaged by staff meanwhile", ""]
        lines += [f"- {_topic_link(t)}" for t in triaged[:MAX_LISTED]]
    if failed:
        lines += ["", "### Failed", ""]
        lines += [f"- {_topic_link(t)}" for t in failed[:MAX_LISTED]]
    return "\n".join(lines)


def run_promote(args: argparse.Namespace) -> int:
    client = create_supabase(SOURCE)
    keysets = apply.fetch_keysets(client)
    if not keysets:
        log.error("Read no keysets from Keebdex, nothing written")
        return 1
    since = datetime.now(UTC).date() - timedelta(days=args.max_age_days)
    plan = plan_promotions(
        apply.fetch_waiting(client),
        keysets,
        limit=args.limit,
        posted_since=since,
    )
    result = apply.apply_promotions(client, plan, args.dry_run)
    summary = promote_summary(plan, result, args.dry_run)
    log.info("\n%s", summary)
    write_summary(summary)
    return 1 if result.failed else 0


# reextract


@dataclass
class ReextractCounters:
    threads: int = 0
    not_keysets: int = 0
    added: int = 0
    restored: int = 0
    unchanged: int = 0
    # (topic id, error) of stored posts the parser can't read
    parse_errors: list[tuple[str, str]] = field(default_factory=list)


def reextract_thread(
    client: Client,
    thread: Row,
    raw_body: str,
    profiles: list[Row],
    index: KeysetIndex,
    counters: ReextractCounters,
    dry_run: bool,
) -> None:
    """Extract one stored first post with the current extractor and save
    its candidate as a crawl would."""
    counters.threads += 1
    topic_id = int(thread["external_id"])
    try:
        content = parse_post(raw_body)
    except ValueError as error:
        log.warning("Topic %d: %s", topic_id, error)
        counters.parse_errors.append((thread["external_id"], str(error)))
        return
    topic = Topic(
        topic_id=topic_id,
        board=thread["board"],
        title=thread["title"] or "",
        author=thread.get("author") or "",
        posted_at=stored_time(thread.get("posted_at")),
        kit_images=content.kit_images,
    )
    extraction = extract(topic, profiles)
    if extraction is None:
        counters.not_keysets += 1
        return
    content_hash = (
        thread.get("content_hash") or hashlib.sha256(raw_body.encode()).hexdigest()
    )
    candidate = candidate_row(thread, content_hash, extraction, index)
    saved = apply.save_candidate(client, candidate, dry_run)
    if saved == apply.ADDED:
        counters.added += 1
    elif saved == apply.RESTORED:
        counters.restored += 1
    else:
        counters.unchanged += 1


def reextract_summary(dry_run: bool, counters: ReextractCounters) -> str:
    added = "Would add" if dry_run else "Added"
    lines = [
        "## magpie reextract",
        "",
        f"- Extractor: {EXTRACTOR_VERSION}",
        f"- Stored threads read: {counters.threads}",
        f"- Not keysets: {counters.not_keysets}",
        f"- {added} keyset candidates: {counters.added}",
        f"- Waiting again (superseded before): {counters.restored}",
        f"- Already extracted (skipped): {counters.unchanged}",
        f"- Posts that could not be parsed: {len(counters.parse_errors)}",
    ]
    lines += [
        f"  - {_topic_link(t)}: {e}" for t, e in counters.parse_errors[:MAX_LISTED]
    ]
    return "\n".join(lines)


def run_reextract(args: argparse.Namespace) -> int:
    """Read every stored first post again with the current extractor. No
    requests to Geekhack and no crawl run (watermarks untouched), but never
    while a crawl runs, so the two don't add the same candidates."""
    client = create_supabase()
    profiles = apply.fetch_profiles(client)
    keysets = apply.fetch_keysets(client)
    if not profiles or not keysets:
        log.error(
            "Read %d profiles and %d keysets, nothing written",
            len(profiles),
            len(keysets),
        )
        return 1
    if (run_id := apply.running_run(client, datetime.now(UTC))) is not None:
        log.error("Crawl run %d is running; not extracting meanwhile", run_id)
        return 1
    threads = apply.fetch_stored_threads(client)[: args.limit]
    log.info("Extracting %d stored threads with %s", len(threads), EXTRACTOR_VERSION)
    index = KeysetIndex(keysets)
    counters = ReextractCounters()
    for batch in batched(threads, apply.BODY_BATCH_SIZE, strict=False):
        bodies = apply.stored_bodies(client, [thread["id"] for thread in batch])
        for thread in batch:
            if (raw_body := bodies.get(thread["id"])) is not None:
                reextract_thread(
                    client, thread, raw_body, profiles, index, counters, args.dry_run
                )
    summary = reextract_summary(args.dry_run, counters)
    log.info("\n%s", summary)
    write_summary(summary)
    return 0


# topic


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
