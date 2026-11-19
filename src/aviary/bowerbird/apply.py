"""Read a maker's stored rows and carry out a SyncPlan.

Every read and write raises on a Supabase error (supabase-py raises APIError):
a partial read would make stored rows look deleted and incoming ones new. In a
dry run the reads still happen and every write is logged instead.
"""

import json
import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from itertools import batched

from supabase import Client

from aviary.bowerbird.plan import SyncPlan, maker_sculpt_id
from aviary.common.db import Row, fetch_all

log = logging.getLogger(__name__)

SCULPTS = "artisan_sculpts"
COLORWAYS = "artisan_colorways"
COLLECTION_ITEMS = "user_collection_items"

# PostgREST filters live in the URL, so `in` lists stay short.
IN_CHUNK_SIZE = 100
INSERT_CHUNK_SIZE = 500


def fetch_stored(
    client: Client, maker_id: str, source: str
) -> tuple[list[Row], list[Row]]:
    """The maker's sculpts and colorways of `source`."""

    def own(query):
        return query.eq("maker_id", maker_id).eq("source", source)

    return fetch_all(client, SCULPTS, "*", own), fetch_all(client, COLORWAYS, "*", own)


class Writer:
    """Database writes, or their log lines in a dry run."""

    def __init__(self, client: Client, dry_run: bool):
        self.client = client
        self.dry_run = dry_run

    def insert(self, table: str, rows: Sequence[Row]) -> None:
        if self.dry_run:
            log.info(
                "[DRY RUN] Would insert %d rows into %s: %s",
                len(rows),
                table,
                _dump(rows),
            )
            return
        for batch in batched(rows, INSERT_CHUNK_SIZE, strict=False):
            self.client.table(table).insert(list(batch)).execute()

    def update(self, table: str, entries: Sequence[tuple[int, Row]]) -> None:
        """Rows with the same payload share one request per chunk of ids."""
        groups: dict[str, list[int]] = defaultdict(list)
        payloads: dict[str, Row] = {}
        for row_id, values in entries:
            if not values:
                continue
            key = json.dumps(values, sort_keys=True, default=str)
            groups[key].append(row_id)
            payloads[key] = values
        for key, ids in groups.items():
            for batch in batched(ids, IN_CHUNK_SIZE, strict=False):
                if self.dry_run:
                    log.info(
                        "[DRY RUN] Would update %s %s: %s", table, list(batch), key
                    )
                    continue
                self.client.table(table).update(payloads[key]).in_(
                    "id", list(batch)
                ).execute()

    def delete(self, table: str, column: str, values: Sequence) -> None:
        if not values:
            return
        if self.dry_run:
            log.info(
                "[DRY RUN] Would delete %d rows from %s: %s",
                len(values),
                table,
                list(values),
            )
            return
        for batch in batched(values, IN_CHUNK_SIZE, strict=False):
            self.client.table(table).delete().in_(column, list(batch)).execute()


def _dump(rows: Iterable[Row]) -> str:
    return json.dumps(list(rows), default=str, ensure_ascii=False)


def _select_in(
    client: Client, table: str, columns: str, column: str, values: Sequence
) -> list[Row]:
    rows: list[Row] = []
    for batch in batched(values, IN_CHUNK_SIZE, strict=False):
        rows += (
            client.table(table).select(columns).in_(column, list(batch)).execute().data
        )
    return rows


def _remove_colorways(
    client: Client, writer: Writer, ids: list[int]
) -> tuple[set[int], set[int]]:
    """Delete colorways gone from the source, or mark them deleted while a
    user collection holds them. Returns (deleted ids, soft-deleted ids)."""
    if not ids:
        return set(), set()
    collected = {
        row["artisan_item_id"]
        for row in _select_in(
            client, COLLECTION_ITEMS, "artisan_item_id", "artisan_item_id", ids
        )
    }
    soft = [i for i in ids if i in collected]
    hard = [i for i in ids if i not in collected]
    writer.update(COLORWAYS, [(i, {"deleted": True}) for i in soft])
    writer.delete(COLORWAYS, "id", hard)
    log.info("Colorways removed: %d deleted, %d soft-deleted", len(hard), len(soft))
    return set(hard), set(soft)


def _remove_sculpts(
    client: Client,
    writer: Writer,
    sculpts: list[Row],
    deleted_ids: set[int],
    soft_ids: set[int],
) -> None:
    """A sculpt gone from the source still holds colorways of other sources
    (added in the Keebdex app) and soft-deleted ones, which point at it. Delete
    it only when no colorway is left, soft-delete it when only deleted ones
    are, and keep it while colorways of other sources are."""
    if not sculpts:
        return
    keys = [maker_sculpt_id(s) for s in sculpts]
    remaining: dict[str, list[Row]] = defaultdict(list)
    for row in _select_in(
        client, COLORWAYS, "id, maker_sculpt_id, deleted", "maker_sculpt_id", keys
    ):
        if row["id"] not in deleted_ids:
            remaining[row["maker_sculpt_id"]].append(row)

    to_delete, to_soft_delete, kept = [], [], []
    for sculpt in sculpts:
        colorways = remaining[maker_sculpt_id(sculpt)]
        if not colorways:
            to_delete.append(sculpt["id"])
        elif all(c.get("deleted") or c["id"] in soft_ids for c in colorways):
            if not sculpt.get("deleted"):
                to_soft_delete.append(sculpt["id"])
        else:
            kept.append(maker_sculpt_id(sculpt))

    writer.delete(SCULPTS, "id", to_delete)
    writer.update(SCULPTS, [(i, {"deleted": True}) for i in to_soft_delete])
    if to_delete or to_soft_delete:
        log.info(
            "Sculpts removed: %d deleted, %d soft-deleted",
            len(to_delete),
            len(to_soft_delete),
        )
    if kept:
        log.info("Sculpts kept for colorways of other sources: %s", kept)


def apply_plan(client: Client, plan: SyncPlan, *, dry_run: bool) -> None:
    """Write in catalog-sync's order: sculpts first (colorways point at them),
    then colorways, then the removals."""
    writer = Writer(client, dry_run)
    writer.insert(SCULPTS, plan.sculpt_inserts)
    writer.update(SCULPTS, plan.sculpt_updates)
    writer.insert(COLORWAYS, plan.colorway_inserts)
    writer.update(COLORWAYS, plan.colorway_updates)
    deleted, soft = _remove_colorways(client, writer, plan.removed_colorway_ids)
    _remove_sculpts(client, writer, plan.removed_sculpts, deleted, soft)
    log.info(
        "Sculpts: %d new, %d updated. Colorways: %d new, %d updated.",
        len(plan.sculpt_inserts),
        len(plan.sculpt_updates),
        len(plan.colorway_inserts),
        len(plan.colorway_updates),
    )
