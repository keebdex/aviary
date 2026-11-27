"""magpie's reads and writes of the `ingest` schema: threads, candidates,
crawl_runs (supabase migration 20261119000000_ingest_schema.sql).

Every read and write raises on a Supabase error (supabase-py raises APIError).
In a dry run the reads still happen and every write is logged instead.
"""

import json
import logging
from datetime import datetime, timedelta
from itertools import batched

from postgrest import APIError, SyncPostgrestClient
from supabase import Client

from aviary.common.db import Row, fetch_all
from aviary.magpie.geekhack import FORUM
from aviary.magpie.plan import ListingPlan, stored_time
from aviary.magpie.proposal import MODULE

log = logging.getLogger(__name__)

SCHEMA = "ingest"
THREADS = "threads"
CANDIDATES = "candidates"
RUNS = "crawl_runs"

THREAD_KEY = "source,external_id"
UPSERT_CHUNK_SIZE = 500
# A run still `running` after this long crashed; the next run marks it failed
# so the one-running-run index lets it start.
STALE_RUN = timedelta(hours=1)
UNIQUE_VIOLATION = "23505"


class RunningError(RuntimeError):
    """Another crawl run is in progress."""


def schema(client: Client) -> SyncPostgrestClient:
    return client.schema(SCHEMA)


def _source(query):
    return query.eq("source", FORUM)


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# Crawl runs


def last_watermarks(client: Client) -> dict[str, datetime]:
    """The newest last post per board of the last succeeded run."""
    rows = (
        _source(schema(client).table(RUNS).select("watermarks"))
        .eq("status", "succeeded")
        .order("started_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    marks = rows[0]["watermarks"] if rows else {}
    return {
        board: when for board, value in marks.items() if (when := stored_time(value))
    }


def start_run(client: Client, max_pages: int, now: datetime) -> int:
    """Record a running run; a run left running past STALE_RUN is marked
    failed first. Raises RunningError while another run is in progress."""
    stale = (
        _source(
            schema(client)
            .table(RUNS)
            .update(
                {
                    "status": "failed",
                    "finished_at": now.isoformat(),
                    "error": "interrupted: still running an hour after it started",
                }
            )
        )
        .eq("status", "running")
        .lt("started_at", (now - STALE_RUN).isoformat())
        .execute()
        .data
    )
    if stale:
        log.warning("Marked %d interrupted runs failed", len(stale))
    running = (
        _source(schema(client).table(RUNS).select("id"))
        .eq("status", "running")
        .execute()
        .data
    )
    if running:
        raise RunningError(f"crawl run {running[0]['id']} is still running")
    row = {"source": FORUM, "status": "running", "max_pages": max_pages}
    try:
        return schema(client).table(RUNS).insert(row).execute().data[0]["id"]
    except APIError as error:
        # another run (a local one, outside the Actions concurrency group)
        # started between the check and the insert: crawl_runs_one_running_idx
        if error.code == UNIQUE_VIOLATION:
            raise RunningError("another crawl run just started") from error
        raise


def finish_run(client: Client, run_id: int, values: Row) -> None:
    schema(client).table(RUNS).update(values).eq("id", run_id).execute()


# Listing


def fetch_threads(client: Client) -> dict[str, Row]:
    """Every stored thread by external id, with what a listing compares."""
    rows = fetch_all(
        schema(client), THREADS, "id,external_id,last_post_at,gone_at", _source
    )
    return {row["external_id"]: row for row in rows}


def save_listing(client: Client, board: str, plan: ListingPlan, dry_run: bool) -> None:
    if dry_run:
        log.info(
            "[DRY RUN] Board %s: would upsert %d threads to fetch (%d new): %s",
            board,
            len(plan.due),
            plan.new,
            dump([(r["external_id"], r["title"]) for r in plan.due]),
        )
        log.info("[DRY RUN] Board %s: would refresh %d threads", board, len(plan.seen))
        return
    # two kinds of rows, each with the same columns, as a bulk upsert needs:
    # only due ones move next_fetch_at
    for rows in (plan.due, plan.seen):
        for batch in batched(rows, UPSERT_CHUNK_SIZE, strict=False):
            schema(client).table(THREADS).upsert(
                list(batch), on_conflict=THREAD_KEY, default_to_null=False
            ).execute()


# Fetching


def due_threads(client: Client, limit: int, now: datetime) -> list[Row]:
    """Threads to fetch now, most overdue first (threads_due_idx)."""
    return (
        _source(
            schema(client)
            .table(THREADS)
            .select("id,external_id,board,title,author,content_hash,fetch_attempts")
        )
        .lte("next_fetch_at", now.isoformat())
        .is_("ignored_at", "null")
        .is_("gone_at", "null")
        .order("next_fetch_at")
        .limit(limit)
        .execute()
        .data
    )


def update_thread(client: Client, thread: Row, values: Row, dry_run: bool) -> None:
    if dry_run:
        shown = {k: v for k, v in values.items() if k != "raw_body"}
        log.info(
            "[DRY RUN] Would update thread %s: %s", thread["external_id"], dump(shown)
        )
        return
    schema(client).table(THREADS).update(values).eq("id", thread["id"]).execute()


ADDED = "added"
RESTORED = "restored"


def save_candidate(client: Client, candidate: Row, dry_run: bool) -> str | None:
    """Insert an extraction: ADDED, RESTORED when the same extractor already
    read the same content and the supersede trigger put that superseded
    candidate back to waiting (the first post went back to an earlier
    version), None when it was already waiting, dismissed, or promoted. The
    trigger returns no row in both of the last cases, so the existing
    extraction's state is read first; a dry run reads it too, to tell what
    the insert would do."""
    existing = (
        schema(client)
        .table(CANDIDATES)
        .select("state")
        .eq("thread_id", candidate["thread_id"])
        .eq("module", candidate["module"])
        .eq("extractor_version", candidate["extractor_version"])
        .eq("content_hash", candidate["content_hash"])
        .execute()
        .data
    )
    state = existing[0]["state"] if existing else None
    if dry_run:
        if state is None:
            log.info("[DRY RUN] Would add candidate: %s", dump(candidate))
            return ADDED
        return RESTORED if state == "superseded" else None
    if schema(client).table(CANDIDATES).insert(candidate).execute().data:
        return ADDED
    return RESTORED if state == "superseded" else None


# Extracting stored posts again

# Stored first posts read per request: each can be up to 500,000 characters.
BODY_BATCH_SIZE = 50


def running_run(client: Client, now: datetime) -> int | None:
    """The crawl run in progress (started within STALE_RUN), if any."""
    rows = (
        _source(schema(client).table(RUNS).select("id"))
        .eq("status", "running")
        .gte("started_at", (now - STALE_RUN).isoformat())
        .limit(1)
        .execute()
        .data
    )
    return rows[0]["id"] if rows else None


def fetch_stored_threads(client: Client) -> list[Row]:
    """Threads with a stored first post, neither ignored nor gone, without
    the post itself (`stored_bodies` reads it in batches)."""
    return fetch_all(
        schema(client),
        THREADS,
        "id,external_id,board,title,author,posted_at,content_hash",
        lambda query: (
            _source(query)
            .not_.is_("raw_body", "null")
            .is_("ignored_at", "null")
            .is_("gone_at", "null")
        ),
    )


def stored_bodies(client: Client, ids: list[int]) -> dict[int, str]:
    """The `raw_body` of each thread in `ids`, by id."""
    rows = (
        schema(client).table(THREADS).select("id,raw_body").in_("id", ids).execute()
    ).data
    return {row["id"]: row["raw_body"] for row in rows}


# Candidates waiting for promotion


def fetch_waiting(client: Client) -> list[Row]:
    """Keyset candidates waiting for triage, with their thread."""
    return fetch_all(
        schema(client),
        CANDIDATES,
        "id,payload,match_key,thread:threads(external_id,posted_at,ignored_at,gone_at)",
        lambda query: query.eq("state", "new").eq("module", MODULE),
    )
