"""Alpha Keycaps (alphakeycaps.com): one page per sculpt, one figure per
colorway. Port of catalog-sync's artisans/makers/alphakeycaps.js."""

import time

import httpx
from bs4 import BeautifulSoup

from aviary.bowerbird.plan import Row
from aviary.common.ids import crc32_hex
from aviary.common.slug import url_slugify

BASE_URL = "https://alphakeycaps.com"
MAKER_ID = "alpha-keycaps"
TOPRE = "(T)"
PAGE_DELAY_SECONDS = 0.5

CATALOGS = (
    "Darth Looga",
    "MF Belooga",
    "Keypora",
    "Jedi Blinker",
    "Blinker",
    "Matapora",
    "Alpha Ape",
    "Cherep",
    "Salvador",
    "Albison",
    "Mr Worldwide",
    "Boosted Gamer Set",
    "Geekpora",
    "Prayge",
    "Devoura",
    "Lickely",
    "Tut",
    "Tutré",
    "Pintut",
    "Kerebro",
)


def sculpt_url(sculpt_id: str) -> str:
    return f"{BASE_URL}/{sculpt_id}"


def parse_colorways(html: str, sculpt_id: str, sculpt_name: str) -> list[Row]:
    soup = BeautifulSoup(html, "lxml")
    colorways = []
    for order, figure in enumerate(soup.select("figure")):
        strong = figure.select("strong")
        text = "".join(node.get_text() for node in strong)
        image = figure.find("img")

        name = text.replace(sculpt_name, "", 1)
        stem = None
        if TOPRE in name:
            stem = ["Topre"]
            name = name.replace(TOPRE, "", 1)

        key = f"{MAKER_ID}-{sculpt_id}-{url_slugify(name)}"
        if stem:
            key += "-" + "|".join(stem)

        colorway: Row = {
            "name": name.strip(),
            "maker_id": MAKER_ID,
            "sculpt_id": sculpt_id,
            "sale_type": None,
            "release": None,
            "qty": None,
            "photo_credit": None,
            "colorway_id": crc32_hex(key),
            "order": order,
            "stem": stem,
            "source_document_id": sculpt_url(sculpt_id),
        }
        # An absent image is left out rather than cleared, as in catalog-sync.
        if image is not None and image.get("data-src") is not None:
            colorway["img"] = image["data-src"]
        colorways.append(colorway)
    return colorways


def scrape(client: httpx.Client) -> list[Row]:
    sculpts = []
    for index, name in enumerate(CATALOGS):
        if index:
            time.sleep(PAGE_DELAY_SECONDS)
        sculpt_id = url_slugify(name)
        response = client.get(sculpt_url(sculpt_id))
        response.raise_for_status()
        sculpts.append(
            {
                "name": name,
                "maker_id": MAKER_ID,
                "sculpt_id": sculpt_id,
                "colorways": parse_colorways(response.text, sculpt_id, name),
                "source_document_id": sculpt_url(sculpt_id),
                "release": None,
                "profile": None,
                "cast": None,
                "design": None,
            }
        )
    return sculpts
