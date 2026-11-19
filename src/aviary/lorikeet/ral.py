"""Crawl the RAL Classic, Design, and Effect colors from ralcolorchart.com."""

import logging
import re
import time
from dataclasses import dataclass
from enum import StrEnum

import httpx
from bs4 import BeautifulSoup, Tag

from aviary.common.http import create_http_client

log = logging.getLogger(__name__)

BASE_URL = "https://www.ralcolorchart.com"

PAGES = (
    "/ral-design/000-095",
    "/ral-design/100-190",
    "/ral-design/200-290",
    "/ral-design/300-360",
    "/ral-classic/yellow-hues",
    "/ral-classic/orange-hues",
    "/ral-classic/red-hues",
    "/ral-classic/violet-hues",
    "/ral-classic/blue-hues",
    "/ral-classic/green-hues",
    "/ral-classic/grey-hues",
    "/ral-classic/brown-hues",
    "/ral-classic/white-and-black-hues",
    "/ral-effect/110-190",
    "/ral-effect/210-290",
    "/ral-effect/310-390",
    "/ral-effect/410-490",
    "/ral-effect/510-590",
    "/ral-effect/610-690",
    "/ral-effect/710-790",
    "/ral-effect/810-870",
)

PAGE_DELAY_SECONDS = 0.5


class Range(StrEnum):
    CLASSIC = "Classic"
    DESIGN = "Design"
    EFFECT = "Effect"


# Codes as written on the chart: Design is hue, lightness, chroma
# ("000 15 00"), Classic four digits ("1000"), Effect a hue group and a shade
# number or M for metallic ("110-1", "840-M").
CODE_PATTERNS = {
    Range.DESIGN: re.compile(r"\d{3} \d{2} \d{2}"),
    Range.CLASSIC: re.compile(r"\d{4}"),
    Range.EFFECT: re.compile(r"\d{3}-(?:\d+|M)"),
}

TITLE_PATTERN = re.compile(
    r"^RAL\s+(?P<code>\d{3}\s+\d{2}\s+\d{2}|\d{3}-(?:\d+|M)|\d{4})(?:\s+(?P<name>.+))?$"
)
HEX_PATTERN = re.compile(r"#[0-9a-fA-F]{6}\b")


@dataclass(frozen=True)
class Color:
    """A RAL color as Keebdex stores it in `colors`."""

    code: str
    name: str | None
    hex: str
    system: str = "RAL"

    @property
    def range(self) -> Range:
        return color_range(self.code)


class CrawlError(RuntimeError):
    """A page could not be read; the crawl is incomplete."""


def color_range(code: str) -> Range:
    for ral_range, pattern in CODE_PATTERNS.items():
        if pattern.fullmatch(code):
            return ral_range
    raise ValueError(f"Not a RAL code: {code!r}")


def parse_swatch(link: Tag) -> Color | None:
    """Read a color from a swatch link, whose title is "RAL <code> [name]"
    and whose style holds the color. Returns None for other links."""
    title = " ".join(str(link.get("title", "")).split())
    match = TITLE_PATTERN.match(title)
    if not match:
        return None

    hex_match = HEX_PATTERN.search(str(link.get("style", "")))
    if not hex_match:
        log.warning("No color in the swatch of %s", title)
        return None

    code = " ".join(match["code"].split())
    name = match["name"].title() if match["name"] else None
    return Color(code=code, name=name, hex=hex_match.group(0).lower())


def parse_page(html: str) -> list[Color]:
    soup = BeautifulSoup(html, "lxml")
    colors = (parse_swatch(link) for link in soup.select('a[href*="/ral-"]'))
    return [color for color in colors if color]


def crawl(client: httpx.Client | None = None) -> list[Color]:
    """Read every page and return each color once, in page order.

    Raises CrawlError when a page fails or has no colors, so a partial crawl
    is never mistaken for a complete one.
    """
    colors: dict[str, Color] = {}
    with client or create_http_client() as http:
        for index, path in enumerate(PAGES):
            if index:
                time.sleep(PAGE_DELAY_SECONDS)
            url = BASE_URL + path
            try:
                response = http.get(url)
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise CrawlError(f"{url}: {error}") from error

            page_colors = parse_page(response.text)
            if not page_colors:
                raise CrawlError(f"{url}: no colors found")
            log.info("%s: %d colors", path, len(page_colors))
            for color in page_colors:
                colors.setdefault(color.code, color)

    return list(colors.values())
