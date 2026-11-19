"""What a maker sync changes, worked out without touching the database.

A port of `updateMakerDatabase` in catalog-sync's artisans/utils/database.js,
split into this pure planning step and `apply.py`. Every source of artisan data
(the maker website scrapers here, the Google Docs importer later) produces the
same incoming shape and goes through this one planner.

Incoming sculpts and colorways are plain dicts shaped like the database rows.
A key that is absent means "not provided" (JavaScript's `undefined`) and never
syncs, while a key set to None clears the stored value, as in catalog-sync.

Rules (kept from catalog-sync's AGENTS.md, which explains them further):
- Only rows of the sync's own `source` are read, matched, or removed.
- Updates patch only the syncable fields, skipping those in the row's
  `overridden_fields`; the always-synced fields are metadata the app cannot
  edit, so they sync regardless.
- Colorways match by colorway_id first, then by name against orphaned stored
  rows only, each claimable once.
- With `preserve_missing`, rows absent from the input are kept unless their
  `source_document_id` is one of `available_document_ids` (a doc that synced).
- A sculpt renamed in Keebdex keeps its source slug in `source_slug`; incoming
  rows are pointed at the renamed slug.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

Row = dict[str, Any]

# Colorway fields a sync may change, unless overridden in the app.
SYNCABLE_FIELDS = ("name", "release", "qty", "photo_credit", "img", "stem")
# Colorway metadata derived from the source, never editable in the app.
ALWAYS_SYNCED_FIELDS = ("order", "source_document_id")
SCULPT_SYNCABLE_FIELDS = ("release", "profile", "cast", "design")
SCULPT_ALWAYS_SYNCED_FIELDS = ("source_document_id",)

_MISSING = object()


@dataclass
class SyncPlan:
    sculpt_inserts: list[Row] = field(default_factory=list)
    sculpt_updates: list[tuple[int, Row]] = field(default_factory=list)
    # Stored sculpts gone from the source; apply.py decides delete, soft
    # delete, or keep once their colorways are handled.
    removed_sculpts: list[Row] = field(default_factory=list)
    colorway_inserts: list[Row] = field(default_factory=list)
    colorway_updates: list[tuple[int, Row]] = field(default_factory=list)
    # Stored colorways gone from the source: deleted, or soft-deleted when a
    # user collection holds them (decided in apply.py).
    removed_colorway_ids: list[int] = field(default_factory=list)
    # Every incoming colorway (plus preserved stored ones), with
    # `img_overridden` set on matched ones, for the image upload.
    colorways: list[Row] = field(default_factory=list)

    @property
    def changes_rows(self) -> bool:
        return bool(
            self.sculpt_inserts
            or self.sculpt_updates
            or self.colorway_inserts
            or self.colorway_updates
            or self.removed_colorway_ids
            or self.removed_sculpts
        )


def maker_sculpt_id(row: Row) -> str:
    return f"{row['maker_id']}/{row['sculpt_id']}"


def _colorway_key(row: Row) -> str:
    return f"{row['maker_id']}-{row['sculpt_id']}-{row['colorway_id']}"


def _name_key(row: Row) -> str:
    return f"{row['maker_id']}-{row['sculpt_id']}-{row.get('name')}"


def is_field_changed(incoming: Any, stored: Any) -> bool:
    """catalog-sync's isFieldChanged: lists compare by sorted content, and a
    missing list equals an empty one."""
    if isinstance(incoming, list) or isinstance(stored, list):
        left = sorted(incoming) if isinstance(incoming, list) else []
        right = sorted(stored) if isinstance(stored, list) else []
        return ",".join(map(str, left)) != ",".join(map(str, right))
    return incoming != stored


def follow_renamed_sculpts(
    stored_sculpts: list[Row], sculpts: list[Row], colorways: list[Row]
) -> None:
    """Point incoming rows whose sculpt was renamed in Keebdex at its new slug.
    A slug some stored sculpt still has matches that row directly, and a
    renamed slug the source also uses belongs to that incoming sculpt."""
    renamed = {
        s["source_slug"]: s["sculpt_id"]
        for s in stored_sculpts
        if s.get("source_slug") and s["source_slug"] != s["sculpt_id"]
    }
    if not renamed:
        return
    stored_ids = {s["sculpt_id"] for s in stored_sculpts}
    incoming_ids = {s["sculpt_id"] for s in sculpts}
    for row in [*sculpts, *colorways]:
        sculpt_id = renamed.get(row["sculpt_id"])
        if (
            sculpt_id
            and row["sculpt_id"] not in stored_ids
            and sculpt_id not in incoming_ids
        ):
            row["sculpt_id"] = sculpt_id


def _plan_sculpts(
    sculpts: list[Row], stored_sculpts: list[Row], source: str, plan: SyncPlan
) -> None:
    stored_by_key = {maker_sculpt_id(s): s for s in stored_sculpts}
    matched: set[int] = set()

    for incoming in sculpts:
        stored = stored_by_key.get(maker_sculpt_id(incoming))
        if stored is None:
            plan.sculpt_inserts.append(
                {**incoming, "source": source, "overridden_fields": []}
            )
            continue

        matched.add(stored["id"])
        overridden = stored.get("overridden_fields") or []
        update: Row = {}
        for name in SCULPT_SYNCABLE_FIELDS:
            value = incoming.get(name, _MISSING)
            if (
                name not in overridden
                and value is not _MISSING
                and value != stored.get(name)
            ):
                update[name] = value
        for name in SCULPT_ALWAYS_SYNCED_FIELDS:
            value = incoming.get(name, _MISSING)
            if value is not _MISSING and value != stored.get(name):
                update[name] = value
        if update:
            plan.sculpt_updates.append((stored["id"], update))

    plan.removed_sculpts = [s for s in stored_sculpts if s["id"] not in matched]


def plan_maker_sync(
    tables: list[Row],
    stored_sculpts: list[Row],
    stored_colorways: list[Row],
    *,
    source: str,
    preserve_missing: bool = False,
    available_document_ids: frozenset[str] | set[str] = frozenset(),
) -> SyncPlan:
    """Plan the sync of one maker. `tables` are incoming sculpts, each with a
    `colorways` list; `stored_*` are the maker's rows of `source`."""
    plan = SyncPlan()
    if not tables:
        return plan

    sculpts = [{k: v for k, v in table.items() if k != "colorways"} for table in tables]
    incoming = [dict(colorway) for table in tables for colorway in table["colorways"]]

    follow_renamed_sculpts(stored_sculpts, sculpts, incoming)

    if preserve_missing:
        incoming_sculpt_ids = {s["sculpt_id"] for s in sculpts}
        incoming_keys = {_colorway_key(c) for c in incoming}

        # keep sculpts belonging to a doc that wasn't synced this run
        sculpts += [
            s
            for s in stored_sculpts
            if s["sculpt_id"] not in incoming_sculpt_ids
            and (
                not s.get("source_document_id")
                or s["source_document_id"] not in available_document_ids
            )
        ]

        # a partial sync's order is only trustworthy for new colorways: existing
        # ones keep theirs, new ones go after the sculpt's stored colorways
        stored_counts: dict[str, int] = defaultdict(int)
        for c in stored_colorways:
            stored_counts[c["sculpt_id"]] += 1
        stored_by_key = {_colorway_key(c): c for c in stored_colorways}
        for index, colorway in enumerate(incoming):
            stored = stored_by_key.get(_colorway_key(colorway))
            if stored is not None:
                colorway["order"] = stored.get("order")
            else:
                colorway["order"] = stored_counts[colorway["sculpt_id"]] + index

        # keep non-deleted colorways belonging to a doc that wasn't synced
        incoming += [
            dict(c)
            for c in stored_colorways
            if not c.get("deleted")
            and _colorway_key(c) not in incoming_keys
            and (
                not c.get("source_document_id")
                or c["source_document_id"] not in available_document_ids
            )
        ]

    _plan_sculpts(sculpts, stored_sculpts, source, plan)

    stored_by_key = {_colorway_key(c): c for c in stored_colorways}
    id_matched = {
        stored_by_key[_colorway_key(c)]["id"]
        for c in incoming
        if _colorway_key(c) in stored_by_key
    }
    # per-name queues, so duplicate or empty names don't collapse into one
    # candidate claimed by several incoming rows
    name_candidates: dict[str, list[Row]] = defaultdict(list)
    for c in stored_colorways:
        if c["id"] not in id_matched:
            name_candidates[_name_key(c)].append(c)

    claimed: set[int] = set()
    for colorway in incoming:
        by_colorway_id = stored_by_key.get(_colorway_key(colorway))
        stored = by_colorway_id
        if stored is None:
            # colorway_id changed in the source: fall back to an unclaimed
            # orphaned row with the same name
            stored = next(
                (
                    c
                    for c in name_candidates[_name_key(colorway)]
                    if c["id"] not in claimed
                ),
                None,
            )

        if stored is None:
            row = {k: v for k, v in colorway.items() if k != "remote_img"}
            plan.colorway_inserts.append(
                {**row, "source": source, "overridden_fields": []}
            )
            continue

        claimed.add(stored["id"])
        overridden = stored.get("overridden_fields") or []
        colorway["img_overridden"] = "img" in overridden
        update: Row = {}
        for name in SYNCABLE_FIELDS:
            value = colorway.get(name, _MISSING)
            if (
                name not in overridden
                and value is not _MISSING
                and is_field_changed(value, stored.get(name))
            ):
                update[name] = value
        for name in ALWAYS_SYNCED_FIELDS:
            value = colorway.get(name, _MISSING)
            if value is not _MISSING and is_field_changed(value, stored.get(name)):
                update[name] = value
        if by_colorway_id is None and "img" not in overridden:
            # matched by name only: resync the identity so later syncs match
            # by colorway_id again (the old image is left for prune-images)
            update["colorway_id"] = colorway["colorway_id"]
        if update:
            plan.colorway_updates.append((stored["id"], update))

    plan.removed_colorway_ids = [
        c["id"] for c in stored_colorways if c["id"] not in claimed
    ]
    plan.colorways = incoming
    return plan
