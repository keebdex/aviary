"""`magpie crawl`: one `ingest.crawl_runs` run. It lists the boards down to
the last run's watermark into `ingest.threads`, fetches the first post of due
threads, and adds what it reads as keyset candidates to `ingest.candidates`."""

import argparse
import hashlib
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from supabase import Client

from aviary.common.db import Row, create_supabase
from aviary.common.report import write_summary
from aviary.magpie import ingest, keebdex
from aviary.magpie.fetch import Fetcher, FetchError
from aviary.magpie.geekhack import BOARDS, board_url, parse_time, topic_url
from aviary.magpie.parsers import parse_board, parse_topic
from aviary.magpie.plan import (
    BoardListing,
    KeysetIndex,
    plan_listing,
    refetch_at,
    retry_at,
)
from aviary.magpie.proposal import EXTRACTOR_VERSION, MODULE, Extraction, Topic, extract
from aviary.magpie.summary import MAX_LISTED, topic_link

log = logging.getLogger(__name__)

# ingest.threads limits
MAX_BODY_CHARS = 500_000
MAX_IMAGES = 100
MAX_ERROR_CHARS = 2000


class CrawlError(Exception):
    """A board listing that can't be trusted; nothing is written."""


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
        ingest.update_thread(self.client, thread, values, self.dry_run)
        self.counters.threads_fetched += 1

        if extraction is None:
            return
        self.counters.count_profile(extraction, topic.title)
        candidate = candidate_row(thread, content_hash, extraction, self.index)
        saved = ingest.save_candidate(self.client, candidate, self.dry_run)
        if saved == ingest.ADDED:
            self.counters.candidates += 1
        elif saved == ingest.RESTORED:
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
        ingest.update_thread(self.client, thread, values, self.dry_run)


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
    lines += [f"  - {topic_link(t)}: {e}" for t, e in counters.failures[:MAX_LISTED]]
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
    profiles = keebdex.fetch_profiles(client)
    keysets = keebdex.fetch_keysets(client)
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
    previous = ingest.last_watermarks(client)
    now = datetime.now(UTC)
    run_id = None
    if not args.dry_run:
        try:
            run_id = ingest.start_run(client, args.max_pages, now)
        except ingest.RunningError as error:
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
        stored = ingest.fetch_threads(client)
        for listing in listings:
            plan = plan_listing(listing, stored, now)
            ingest.save_listing(client, listing.board, plan, args.dry_run)
            counters.pages_listed += listing.pages
            counters.threads_listed += len(listing.seen)
            counters.threads_new += plan.new
        due = ingest.due_threads(client, args.fetch_limit, datetime.now(UTC))
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
    ingest.finish_run(
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
