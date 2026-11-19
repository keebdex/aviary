"""Maker website scrapers: each returns the maker's sculpts, every one with
its `colorways`, shaped as bowerbird.plan expects."""

from collections.abc import Callable

import httpx

from aviary.bowerbird.makers import alpha_keycaps, keycat
from aviary.bowerbird.plan import Row

Scraper = Callable[[httpx.Client], list[Row]]

SCRAPERS: dict[str, Scraper] = {
    alpha_keycaps.MAKER_ID: alpha_keycaps.scrape,
    keycat.MAKER_ID: keycat.scrape,
}

# Makers whose website shows only part of their catalog: rows missing from a
# scrape are kept instead of removed.
PARTIAL_MAKERS = {keycat.MAKER_ID}
