"""magpie's reads and writes: the `ingest` schema (threads, candidates,
crawl_runs; supabase migration 20261119000000_ingest_schema.sql) and the
Pending keysets and kits it promotes into Keebdex.

Every read and write raises on a Supabase error (supabase-py raises APIError).
In a dry run the reads still happen and every write is logged instead.
"""

import json
import logging
from datetime import datetime, timedelta
from itertools import batched

from postgrest import APIError, SyncPostgrestClient
from supabase import Client

from aviary.common.db import PUBLISHED_FILTER, Row, fetch_all
from aviary.magpie.geekhack import FORUM
from aviary.magpie.plan import ListingPlan, Promotion, PromotionPlan, stored_time
from aviary.magpie.proposal import MODULE, keyset_row, kit_rows

log = logging.getLogger(__name__)

SCHEMA = "ingest"
THREADS = "threads"
CANDIDATES = "candidates"
RUNS = "crawl_runs"
KEYSETS = "keysets"
KITS = "keyset_kits"
PROFILES = "keyset_profiles"

THREAD_KEY = "source,external_id"
UPSERT_CHUNK_SIZE = 500
# A run still `running` after this long crashed; the next run marks it failed
# so the one-running-run index lets it start.
STALE_RUN = timedelta(hours=1)
UNIQUE_VIOLATION = "23505"


class RunningError(RuntimeError):
    """Another crawl run is in progress."""


def ingest(client: Client) -> SyncPostgrestClient:
    return client.schema(SCHEMA)


def _source(query):
    return query.eq("source", FORUM)


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# Crawl runs


def last_watermarks(client: Client) -> dict[str, datetime]:
    """The newest last post per board of the last succeeded run."""
    rows = (
        _source(ingest(client).table(RUNS).select("watermarks"))
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
            ingest(client)
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
        _source(ingest(client).table(RUNS).select("id"))
        .eq("status", "running")
        .execute()
        .data
    )
    if running:
        raise RunningError(f"crawl run {running[0]['id']} is still running")
    row = {"source": FORUM, "status": "running", "max_pages": max_pages}
    try:
        return ingest(client).table(RUNS).insert(row).execute().data[0]["id"]
    except APIError as error:
        # another run (a local one, outside the Actions concurrency group)
        # started between the check and the insert: crawl_runs_one_running_idx
        if error.code == UNIQUE_VIOLATION:
            raise RunningError("another crawl run just started") from error
        raise


def finish_run(client: Client, run_id: int, values: Row) -> None:
    ingest(client).table(RUNS).update(values).eq("id", run_id).execute()


# Listing


def fetch_threads(client: Client) -> dict[str, Row]:
    """Every stored thread by external id, with what a listing compares."""
    rows = fetch_all(
        ingest(client), THREADS, "id,external_id,last_post_at,gone_at", _source
    )
    return {row["external_id"]: row for row in rows}


def save_listing(client: Client, board: str, plan: ListingPlan, dry_run: bool) -> None:
    if dry_run:
        log.info(
            "[DRY RUN] Board %s: would upsert %d threads to fetch (%d new): %s",
            board,
            len(plan.due),
            plan.new,
            _dump([(r["external_id"], r["title"]) for r in plan.due]),
        )
        log.info("[DRY RUN] Board %s: would refresh %d threads", board, len(plan.seen))
        return
    # two kinds of rows, each with the same columns, as a bulk upsert needs:
    # only due ones move next_fetch_at
    for rows in (plan.due, plan.seen):
        for batch in batched(rows, UPSERT_CHUNK_SIZE, strict=False):
            ingest(client).table(THREADS).upsert(
                list(batch), on_conflict=THREAD_KEY, default_to_null=False
            ).execute()


# Fetching


def due_threads(client: Client, limit: int, now: datetime) -> list[Row]:
    """Threads to fetch now, most overdue first (threads_due_idx)."""
    return (
        _source(
            ingest(client)
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
            "[DRY RUN] Would update thread %s: %s", thread["external_id"], _dump(shown)
        )
        return
    ingest(client).table(THREADS).update(values).eq("id", thread["id"]).execute()


def save_candidate(client: Client, candidate: Row, dry_run: bool) -> bool:
    """Insert an extraction; False when the same extractor already read the
    same content (the supersede trigger drops the row)."""
    if dry_run:
        log.info("[DRY RUN] Would add candidate: %s", _dump(candidate))
        return True
    return bool(ingest(client).table(CANDIDATES).insert(candidate).execute().data)


# Keebdex


def fetch_keysets(client: Client) -> list[Row]:
    """Every keyset, of every review status: a Pending one is a proposal
    already."""
    return fetch_all(client, KEYSETS, "id,profile_id,slug,profile_keyset_id,url,status")


def fetch_profiles(client: Client) -> list[Row]:
    """Published profiles: a keyset is never proposed under one in review."""
    return fetch_all(
        client,
        PROFILES,
        "id,profile_id,name,profile",
        lambda query: query.or_(PUBLISHED_FILTER),
    )


# Promotion


def fetch_waiting(client: Client) -> list[Row]:
    """Keyset candidates waiting for triage, with their thread."""
    return fetch_all(
        ingest(client),
        CANDIDATES,
        "id,payload,match_key,thread:threads(external_id,posted_at,ignored_at,gone_at)",
        lambda query: query.eq("state", "new").eq("module", MODULE),
    )


class _Triaged(Exception):
    """The candidate is no longer waiting: staff triaged it meanwhile."""


def apply_promotions(client: Client, plan: PromotionPlan, dry_run: bool) -> list[int]:
    """Insert each promotion (keyset, then its kits) and mark its candidate
    promoted, then set the match_key of rematched candidates; the topics
    whose promotion failed.

    A promotion that cannot complete is rolled back (nothing points at the
    new keyset yet): kits that fail would leave a keyset the review queue
    never shows, and a candidate staff triaged after `fetch_waiting` read it
    would leave an orphan, or a duplicate of what staff made of it. A
    triaged candidate is skipped, not failed. A rollback that fails is
    logged and the run goes on to the other promotions."""
    failed: list[int] = []
    failed_keys: set[str] = set()
    for promotion in plan.promotions:
        payload = promotion.payload
        if dry_run:
            log.info(
                "[DRY RUN] Would insert keyset %s from topic %d: %s with kits %s",
                promotion.key,
                promotion.topic_id,
                _dump(keyset_row(payload)),
                _dump([(k["kit_id"], k["img"]) for k in payload["kits"]]),
            )
            continue
        try:
            inserted = client.table(KEYSETS).insert(keyset_row(payload)).execute()
            keyset_id = inserted.data[0]["id"]
        except Exception:
            log.exception(
                "Topic %d: inserting keyset %s failed",
                promotion.topic_id,
                promotion.key,
            )
            failed.append(promotion.topic_id)
            failed_keys.add(promotion.key)
            continue
        try:
            client.table(KITS).insert(kit_rows(payload)).execute()
            # only while still waiting: staff may have triaged it meanwhile
            claimed = (
                ingest(client)
                .table(CANDIDATES)
                .update(
                    {
                        "state": "promoted",
                        "promoted_kind": "keyset",
                        "promoted_id": keyset_id,
                    }
                )
                .eq("id", promotion.candidate_id)
                .eq("state", "new")
                .execute()
                .data
            )
            if not claimed:
                raise _Triaged
        except _Triaged:
            log.warning(
                "Topic %d: candidate %d was triaged meanwhile, removing keyset %s",
                promotion.topic_id,
                promotion.candidate_id,
                promotion.key,
            )
            failed_keys.add(promotion.key)
            if not _remove_keyset(client, promotion, keyset_id):
                failed.append(promotion.topic_id)
        except Exception:
            log.exception(
                "Topic %d: promoting %s failed, removing the keyset",
                promotion.topic_id,
                promotion.key,
            )
            _remove_keyset(client, promotion, keyset_id)
            failed.append(promotion.topic_id)
            failed_keys.add(promotion.key)

    for rematch in plan.rematches:
        if rematch.key in failed_keys:
            # its keyset was not inserted (or was removed again): the
            # candidate waits for the next run
            continue
        if dry_run:
            log.info(
                "[DRY RUN] Would match candidate of topic %d to %s",
                rematch.topic_id,
                rematch.key,
            )
            continue
        ingest(client).table(CANDIDATES).update({"match_key": rematch.key}).eq(
            "id", rematch.candidate_id
        ).eq("state", "new").execute()
    return failed


def _remove_keyset(client: Client, promotion: Promotion, keyset_id: int) -> bool:
    """Delete a keyset this run just inserted, kits first; False (logged)
    when that fails, so the caller lists the topic as failed and the run
    goes on. The kits are found by key: the keyset did not exist before."""
    try:
        client.table(KITS).delete().eq("profile_keyset_id", promotion.key).execute()
        client.table(KEYSETS).delete().eq("id", keyset_id).execute()
    except Exception:
        log.exception(
            "Topic %d: removing keyset %s (id %d) failed; delete it by hand",
            promotion.topic_id,
            promotion.key,
            keyset_id,
        )
        return False
    return True
