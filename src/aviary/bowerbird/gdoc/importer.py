"""Sync makers from their Google Docs: port of catalog-sync's gdocs-importer.js.

For each maker with `document_ids`: download every doc (a deleted one is
skipped and the sync turns partial), parse them, merge them into one catalog,
then plan and apply the sync like the website scrapers, under source `gdoc`.
When rows changed, the maker's contributors and update time are refreshed from
the last doc's Drive metadata.
"""

import copy
import logging
from typing import Any

from supabase import Client

from aviary.bowerbird.gdoc.google import DocNotFound, Google
from aviary.bowerbird.gdoc.parser import Regexes, parse_document
from aviary.bowerbird.plan import Row, SyncPlan

log = logging.getLogger(__name__)

SOURCE = "gdoc"
MAKERS = "artisan_makers"


def fetch_makers(client: Client, maker_ids: list[str] | None = None) -> list[Row]:
    """Published makers whose doc sync is on and who have docs. `.neq` keeps
    catalog-sync's semantics: a null `deleted` or `disable_google_sync` is
    excluded too."""
    query = (
        client.table(MAKERS)
        .select("*")
        .or_("review_status.is.null,review_status.eq.Approved")
        .neq("deleted", True)
        .neq("disable_google_sync", True)
        .order("maker_id")
    )
    if maker_ids:
        query = query.in_("maker_id", maker_ids)
    makers = query.execute().data
    return [
        m
        for m in makers
        if isinstance(m.get("document_ids"), list) and m["document_ids"]
    ]


def merge_documents(documents: list[dict[str, Row]]) -> dict[str, Row]:
    """deepmerge.all(documents, { customMerge }): a sculpt in several docs
    takes the later doc's fields, and its colorways are concatenated, the later
    doc's orders shifted past the earlier ones (length + 1)."""
    merged: dict[str, Row] = {}
    for document in documents:
        for key, sculpt in document.items():
            if key not in merged:
                merged[key] = copy.deepcopy(sculpt)
                continue
            target = merged[key]
            for field, value in sculpt.items():
                if field == "colorways" and isinstance(target.get(field), list):
                    offset = len(target[field]) + 1
                    target[field] = target[field] + [
                        {**copy.deepcopy(c), "order": c["order"] + offset}
                        for c in value
                    ]
                else:
                    target[field] = copy.deepcopy(value)
    return merged


def update_metadata(client: Client, maker_id: str, data: Row, *, dry_run: bool) -> None:
    if dry_run:
        log.info("[DRY RUN] Would update maker %s: %s", maker_id, sorted(data))
        return
    client.table(MAKERS).update(data).eq("maker_id", maker_id).execute()


def contributors_from(
    google: Google, file_id: str, known: list[Row]
) -> tuple[list[Row], Any]:
    """The maker's contributors plus everyone who edited the doc, once each,
    and the doc's modified time."""
    file = google.file(file_id)
    people = list(known)
    if (file.get("capabilities") or {}).get("canReadRevisions"):
        seen = set()
        for revision in google.revisions(file_id):
            user = revision.get("lastModifyingUser")
            if not user:
                continue  # catalog-sync would fail the maker here
            if user.get("permissionId") in seen:
                continue
            seen.add(user.get("permissionId"))
            people.append(
                {
                    "name": user.get("displayName"),
                    "picture": user.get("photoLink"),
                    "pid": user.get("permissionId"),
                }
            )
    elif user := file.get("lastModifyingUser"):
        people.append(
            {
                "name": user.get("displayName"),
                "picture": user.get("photoLink"),
                "pid": user.get("permissionId"),
            }
        )

    unique, pids = [], set()
    for person in people:
        if person.get("pid") not in pids:
            pids.add(person.get("pid"))
            unique.append(person)
    return unique, file.get("modifiedTime")


def load_catalog(
    google: Google, maker: Row, base_url: str, rx: Regexes
) -> tuple[list[Row] | None, list[str], bool]:
    """(sculpts, ids of the docs that loaded, whether one was missing); None
    sculpts when every doc is gone. Errors other than a 404 propagate."""
    maker_id = maker["maker_id"]
    documents, available, missing = [], [], False
    for document_id in maker["document_ids"]:
        try:
            raw = google.document(document_id)
        except DocNotFound:
            log.warning(
                "Doc %s of %s not found (404), skipping it", document_id, maker_id
            )
            missing = True
            continue
        parsed = parse_document(raw, maker_id, base_url, rx)
        for sculpt in parsed.values():
            sculpt["source_document_id"] = document_id
            for colorway in sculpt["colorways"]:
                colorway["source_document_id"] = document_id
        documents.append(parsed)
        available.append(document_id)

    if not documents:
        return None, available, missing
    catalog = merge_documents(documents) if len(documents) > 1 else documents[0]
    return list(catalog.values()), available, missing


def rows_modified(plan: SyncPlan) -> bool:
    """catalog-sync's `modified`: colorways were added or changed."""
    return bool(plan.colorway_inserts or plan.colorway_updates)
