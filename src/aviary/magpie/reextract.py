"""`magpie reextract`: extracts every stored first post again with the
current extractor and saves candidates as `crawl` does, without requests to
Geekhack and without a crawl run."""

import argparse
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import batched

from supabase import Client

from aviary.common.db import Row, create_supabase
from aviary.common.report import write_summary
from aviary.magpie import ingest, keebdex
from aviary.magpie.crawl import candidate_row
from aviary.magpie.parsers import parse_post
from aviary.magpie.plan import KeysetIndex, stored_time
from aviary.magpie.proposal import EXTRACTOR_VERSION, Topic, extract
from aviary.magpie.summary import MAX_LISTED, topic_link

log = logging.getLogger(__name__)


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
    saved = ingest.save_candidate(client, candidate, dry_run)
    if saved == ingest.ADDED:
        counters.added += 1
    elif saved == ingest.RESTORED:
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
        f"  - {topic_link(t)}: {e}" for t, e in counters.parse_errors[:MAX_LISTED]
    ]
    return "\n".join(lines)


def run_reextract(args: argparse.Namespace) -> int:
    """Read every stored first post again with the current extractor. No
    requests to Geekhack and no crawl run (watermarks untouched), but never
    while a crawl runs, so the two don't add the same candidates."""
    client = create_supabase()
    profiles = keebdex.fetch_profiles(client)
    keysets = keebdex.fetch_keysets(client)
    if not profiles or not keysets:
        log.error(
            "Read %d profiles and %d keysets, nothing written",
            len(profiles),
            len(keysets),
        )
        return 1
    if (run_id := ingest.running_run(client, datetime.now(UTC))) is not None:
        log.error("Crawl run %d is running; not extracting meanwhile", run_id)
        return 1
    threads = ingest.fetch_stored_threads(client)[: args.limit]
    log.info("Extracting %d stored threads with %s", len(threads), EXTRACTOR_VERSION)
    index = KeysetIndex(keysets)
    counters = ReextractCounters()
    for batch in batched(threads, ingest.BODY_BATCH_SIZE, strict=False):
        bodies = ingest.stored_bodies(client, [thread["id"] for thread in batch])
        for thread in batch:
            if (raw_body := bodies.get(thread["id"])) is not None:
                reextract_thread(
                    client, thread, raw_body, profiles, index, counters, args.dry_run
                )
    summary = reextract_summary(args.dry_run, counters)
    log.info("\n%s", summary)
    write_summary(summary)
    return 0
