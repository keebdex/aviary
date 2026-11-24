"""`magpie promote`: inserts the complete new keyset candidates into Keebdex
as Pending keysets with their kits; the rest wait for staff."""

import argparse
import logging
from datetime import UTC, datetime, timedelta

from aviary.common.db import create_supabase, revision_source
from aviary.common.report import write_summary
from aviary.magpie import ingest, keebdex
from aviary.magpie.plan import PromotionPlan, plan_promotions
from aviary.magpie.summary import MAX_LISTED, topic_link

log = logging.getLogger(__name__)

# The revision source Keebdex shows magpie's keysets under: the job, then the
# site its candidates come from.
SOURCE = revision_source("magpie", "geekhack")


def promote_summary(
    plan: PromotionPlan, result: keebdex.PromotionResult, dry_run: bool
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
            f"- `{p.key}` ({len(p.payload['kits'])} kits) from {topic_link(p.topic_id)}"
            for p in inserted[:MAX_LISTED]
        ]
    if differences:
        lines += ["", "### Different from Keebdex (left as stored)", ""]
        lines += [
            f"- `{r.key}` ({topic_link(r.topic_id)}): {r.difference}"
            for r in differences[:MAX_LISTED]
        ]
    if plan.incomplete:
        lines += ["", "### Incomplete", ""]
        lines += [f"- {topic_link(t)}: {m}" for t, m in plan.incomplete[:MAX_LISTED]]
    if triaged:
        lines += ["", "### Triaged by staff meanwhile", ""]
        lines += [f"- {topic_link(t)}" for t in triaged[:MAX_LISTED]]
    if failed:
        lines += ["", "### Failed", ""]
        lines += [f"- {topic_link(t)}" for t in failed[:MAX_LISTED]]
    return "\n".join(lines)


def run_promote(args: argparse.Namespace) -> int:
    client = create_supabase(SOURCE)
    keysets = keebdex.fetch_keysets(client)
    if not keysets:
        log.error("Read no keysets from Keebdex, nothing written")
        return 1
    since = datetime.now(UTC).date() - timedelta(days=args.max_age_days)
    plan = plan_promotions(
        ingest.fetch_waiting(client),
        keysets,
        limit=args.limit,
        posted_since=since,
    )
    result = keebdex.apply_promotions(client, plan, args.dry_run)
    summary = promote_summary(plan, result, args.dry_run)
    log.info("\n%s", summary)
    write_summary(summary)
    return 1 if result.failed else 0
