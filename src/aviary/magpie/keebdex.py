"""magpie's reads of Keebdex (keysets, profiles) and the Pending keysets and
kits it promotes into it, claiming their `ingest.candidates`.

Every read raises on a Supabase error; a promotion that fails is logged and
reported. In a dry run the reads still happen and every write is logged
instead.
"""

import logging
from dataclasses import dataclass, field

from supabase import Client

from aviary.common.db import PUBLISHED_FILTER, Row, fetch_all
from aviary.magpie.ingest import CANDIDATES, dump, schema
from aviary.magpie.plan import Promotion, PromotionPlan
from aviary.magpie.proposal import keyset_row, kit_rows

log = logging.getLogger(__name__)

KEYSETS = "keysets"
KITS = "keyset_kits"
PROFILES = "keyset_profiles"


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


@dataclass
class PromotionResult:
    """Topics whose promotion failed (`promote` exits 1), and topics whose
    candidate staff triaged meanwhile, so their keyset was removed again."""

    failed: list[int] = field(default_factory=list)
    triaged: list[int] = field(default_factory=list)


def apply_promotions(
    client: Client, plan: PromotionPlan, dry_run: bool
) -> PromotionResult:
    """Insert each promotion (keyset, then its kits), then claim its
    candidate (mark it promoted while it still waits), then set the
    match_key of rematched candidates.

    A keyset is removed again (nothing points at it yet) when its kits fail,
    since the review queue would never show it, and when the claim finds the
    candidate no longer waiting: staff triaged it after `fetch_waiting` read
    it, and the keyset would be an orphan or a duplicate of what staff made
    of it. A claim that raises may still have committed (a lost response),
    so the candidate is read back first: the keyset is removed only when the
    candidate is not promoted to it, and kept when that cannot be read. A
    removal that fails is logged and the run goes on."""
    result = PromotionResult()
    failed_keys: set[str] = set()
    for promotion in plan.promotions:
        payload = promotion.payload
        topic = promotion.topic_id
        if dry_run:
            log.info(
                "[DRY RUN] Would insert keyset %s from topic %d: %s with kits %s",
                promotion.key,
                topic,
                dump(keyset_row(payload)),
                dump([(k["kit_id"], k["img"]) for k in payload["kits"]]),
            )
            continue
        try:
            inserted = client.table(KEYSETS).insert(keyset_row(payload)).execute()
            keyset_id = inserted.data[0]["id"]
        except Exception:
            log.exception("Topic %d: inserting keyset %s failed", topic, promotion.key)
            result.failed.append(topic)
            failed_keys.add(promotion.key)
            continue
        try:
            client.table(KITS).insert(kit_rows(payload)).execute()
        except Exception:
            log.exception(
                "Topic %d: inserting the kits of %s failed, removing the keyset",
                topic,
                promotion.key,
            )
            _remove_keyset(client, promotion, keyset_id)
            result.failed.append(topic)
            failed_keys.add(promotion.key)
            continue
        try:
            claimed = _claim(client, promotion, keyset_id)
        except Exception:
            log.exception(
                "Topic %d: claiming candidate %d failed, reading it back",
                topic,
                promotion.candidate_id,
            )
            current = _read_candidate(client, promotion.candidate_id)
            if current is None:
                log.error(
                    "Topic %d: candidate %d could not be read back; keyset %s"
                    " (id %d) is kept, check it by hand",
                    topic,
                    promotion.candidate_id,
                    promotion.key,
                    keyset_id,
                )
                result.failed.append(topic)
                continue
            if current["state"] == "promoted" and current["promoted_id"] == keyset_id:
                log.info("Topic %d: the claim went through after all", topic)
                continue
            if current["state"] == "new":
                # the claim did not apply: still waiting, try again next run
                _remove_keyset(client, promotion, keyset_id)
                result.failed.append(topic)
                failed_keys.add(promotion.key)
                continue
            claimed = False
        if not claimed:
            log.warning(
                "Topic %d: candidate %d was triaged meanwhile, removing keyset %s",
                topic,
                promotion.candidate_id,
                promotion.key,
            )
            failed_keys.add(promotion.key)
            # listed once: triaged only when its keyset is really gone again
            if _remove_keyset(client, promotion, keyset_id):
                result.triaged.append(topic)
            else:
                result.failed.append(topic)

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
        schema(client).table(CANDIDATES).update({"match_key": rematch.key}).eq(
            "id", rematch.candidate_id
        ).eq("state", "new").execute()
    return result


def _claim(client: Client, promotion: Promotion, keyset_id: int) -> bool:
    """Mark the candidate promoted to `keyset_id`, only while it still
    waits; False when staff triaged it meanwhile."""
    return bool(
        schema(client)
        .table(CANDIDATES)
        .update(
            {"state": "promoted", "promoted_kind": "keyset", "promoted_id": keyset_id}
        )
        .eq("id", promotion.candidate_id)
        .eq("state", "new")
        .execute()
        .data
    )


def _read_candidate(client: Client, candidate_id: int) -> Row | None:
    """A candidate's state and promoted_id; None (logged) when it cannot be
    read."""
    try:
        rows = (
            schema(client)
            .table(CANDIDATES)
            .select("state,promoted_id")
            .eq("id", candidate_id)
            .execute()
            .data
        )
    except Exception:
        log.exception("Reading candidate %d failed", candidate_id)
        return None
    return rows[0] if rows else None


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
