"""Keycat (thekeycat.com): a WooCommerce shop listing colorways as
"<colorway> <sculpt>". Port of catalog-sync's artisans/makers/keycat.js."""

import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from aviary.bowerbird.plan import Row
from aviary.common.ids import crc32_hex
from aviary.common.slug import url_slugify

SHOP_URL = "https://thekeycat.com/shop"
MAKER_ID = "keycat"
VALID_SCULPTS = {"Balo", "Beee", "Pipo", "Puffy", "Spaceship", "Tete", "Tony"}
SALE_FORMATS = {"RAFFLE": "Raffle", "GROUPBUY": "Fulfillment"}
PAGE_DELAY_SECONDS = 0.5

# The shop lists newest first. Keycat's Google Doc already holds every colorway
# up to Pink Camo, so the scraper stops there instead of syncing them a second
# time under its own source.
LAST_GDOC_COLORWAY = "pink camo"


def parse_title(title: str) -> tuple[str, str] | None:
    """(colorway, sculpt) from "<colorway> <sculpt>"."""
    parts = title.split()
    if not parts or parts[-1] not in VALID_SCULPTS:
        return None
    colorway = " ".join(parts[:-1])
    return (colorway, parts[-1]) if colorway else None


def extract_image(container: Tag, base_url: str) -> str | None:
    img = container.select_one("img.wp-post-image") or container.find("img")
    if img is None:
        return None
    if srcset := img.get("data-srcset"):
        # the last candidate, the largest
        return urljoin(base_url, srcset.split(",")[-1].strip().split(" ")[0])
    if src := img.get("data-src"):
        return urljoin(base_url, src)
    return None


def extract_sales(html: str, base_url: str = SHOP_URL) -> tuple[list[Row], str | None]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    for container in soup.select(".product-small.col.has-hover"):
        link = container.select_one(
            "a.woocommerce-LoopProduct-link.woocommerce-loop-product__link"
        )
        if link is None:
            continue
        title = link.get_text().strip()
        if not title:
            heading = container.select_one(".woocommerce-loop-product__title")
            title = heading.get_text().strip() if heading else ""
        parsed = parse_title(title)
        if parsed is None:
            continue
        colorway, sculpt = parsed

        badge_node = container.select_one(".badge")
        badge = badge_node.get_text().strip() if badge_node else ""
        sculpt_id = url_slugify(sculpt)
        items.append(
            {
                "name": colorway.strip(),
                "img": extract_image(container, base_url),
                "maker_id": MAKER_ID,
                "sculpt_id": sculpt_id,
                "sale_type": SALE_FORMATS.get(badge),
                "colorway_id": crc32_hex(
                    f"{MAKER_ID}-{sculpt_id}-{url_slugify(colorway)}"
                ),
                "release": None,
                "qty": None,
                "photo_credit": None,
                "source_document_id": SHOP_URL,
                "stem": None,
            }
        )

    next_link = soup.select_one("a.next.page-number")
    return items, next_link.get("href") if next_link else None


def scrape(client: httpx.Client) -> list[Row]:
    url: str | None = SHOP_URL
    sales: list[Row] = []
    first = True
    while url:
        if not first:
            time.sleep(PAGE_DELAY_SECONDS)
        first = False
        response = client.get(url)
        response.raise_for_status()
        items, next_url = extract_sales(response.text, url)
        if not items:
            break

        reached_gdoc = False
        for item in items:
            if item["name"].lower() == LAST_GDOC_COLORWAY:
                reached_gdoc = True
                break
            sales.append(item)
        url = None if reached_gdoc else next_url

    by_sculpt: dict[str, list[Row]] = {}
    for item in sales:
        by_sculpt.setdefault(item["sculpt_id"], []).append(item)

    return [
        {
            "maker_id": MAKER_ID,
            "sculpt_id": sculpt_id,
            "name": sculpt_id[:1].upper() + sculpt_id[1:],
            "release": None,
            "profile": None,
            "cast": None,
            "design": None,
            "colorways": list(reversed(colorways)),
            "source_document_id": SHOP_URL,
        }
        for sculpt_id, colorways in by_sculpt.items()
    ]
