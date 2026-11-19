"""Add crawled colors that Keebdex lacks to its `colors` table.

Colors are only ever added. A color already in Keebdex is left as it is, even
when the crawl reads another hex or name for it, since staff may have fixed it
by hand; those differences are reported for a person to check. Nothing is
deleted: keysets point at colors through `keyset_colors.color_id`.
"""

import logging
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from supabase import Client

from aviary.lorikeet.ral import Color

log = logging.getLogger(__name__)

TABLE = "colors"
PAGE_SIZE = 1000
INSERT_BATCH_SIZE = 500

# A crawl returning fewer colors than this share of what Keebdex already has
# means the site changed or the parser broke: stop before writing anything.
MIN_CRAWL_SHARE = 0.9


@dataclass(frozen=True)
class Difference:
    crawled: Color
    stored_name: str | None
    stored_hex: str


@dataclass(frozen=True)
class Plan:
    new: list[Color]
    different: list[Difference]
    unchanged: int


class SanityCheckError(RuntimeError):
    """The crawl does not look complete enough to write."""


def fetch_stored(client: Client, system: str) -> dict[str, dict]:
    """Return the stored colors of `system`, keyed by code."""
    stored: dict[str, dict] = {}
    start = 0
    while True:
        response = (
            client.table(TABLE)
            .select("code,name,hex")
            .eq("system", system)
            .order("code")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        for row in response.data:
            stored[row["code"]] = row
        if len(response.data) < PAGE_SIZE:
            return stored
        start += PAGE_SIZE


def plan_sync(crawled: Sequence[Color], stored: dict[str, dict]) -> Plan:
    if len(crawled) < MIN_CRAWL_SHARE * len(stored):
        raise SanityCheckError(
            f"Crawled {len(crawled)} colors but Keebdex has {len(stored)}; "
            "the crawl looks incomplete"
        )

    new: list[Color] = []
    different: list[Difference] = []
    unchanged = 0
    for color in crawled:
        row = stored.get(color.code)
        if row is None:
            new.append(color)
        elif (row["name"] or None) != color.name or row["hex"].lower() != color.hex:
            different.append(Difference(color, row["name"], row["hex"]))
        else:
            unchanged += 1
    return Plan(new=new, different=different, unchanged=unchanged)


def insert_colors(client: Client, colors: Sequence[Color]) -> None:
    rows = [
        {"system": c.system, "code": c.code, "name": c.name, "hex": c.hex}
        for c in colors
    ]
    for start in range(0, len(rows), INSERT_BATCH_SIZE):
        # ignore_duplicates: a color added since fetch_stored stays as it is.
        client.table(TABLE).upsert(
            rows[start : start + INSERT_BATCH_SIZE],
            on_conflict="system,code",
            ignore_duplicates=True,
        ).execute()


def count_by_range(colors: Iterable[Color]) -> Counter:
    return Counter(color.range.value for color in colors)
