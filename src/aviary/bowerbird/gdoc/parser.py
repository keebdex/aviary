"""A maker's Google Doc to sculpts and colorways.

A line-by-line port of catalog-sync's artisans/utils/parser.js: the doc is a
list of table pairs, a sculpt's title table then its colorway grid (one cell
per colorway: an image, then its name and tags). Keep it that close: the
sculpt slugs, orders, and fields it produces must stay what catalog-sync made,
or every row of a maker would be replaced. Where the JavaScript behaves oddly
(a regex's left-over lastIndex, first-match-only replaces) so does this.
"""

from typing import Any

from aviary.bowerbird.gdoc import dates
from aviary.bowerbird.gdoc.jsregex import JS_DOT, JS_SPACE, GlobalRegex, js_trim
from aviary.common.cf_images import delivery_url
from aviary.common.db import Row
from aviary.common.slug import url_slugify

STEMS = "topre|mx|alps|tmx|choc|bs"


class Regexes:
    """The parser's module-level regexes; their lastIndex lives across calls,
    so one set is shared by every parse in a run, as in catalog-sync."""

    def __init__(self) -> None:
        self.artisan_keycap = GlobalRegex(r"artisan keycaps|artisan keycap")
        self.auction = GlobalRegex(r"\(auction\)")
        self.commission = GlobalRegex(r"\(\*\)|\(commission\)|\(comission\)")
        self.giveaway = GlobalRegex(r"\(giveaway\)|\(give-away\)|\(discord giveaway\)")
        self.oneoff = GlobalRegex(r"\(oneoff\)|\(one-off\)|\(1/1\)")
        self.photo_credit = GlobalRegex(rf"\(pc ({JS_DOT}*)\)")
        self.qty = GlobalRegex(r"\(count (\d+)\)")
        self.qty_same_number_fraction = GlobalRegex(r"\((\d+)/\1\)")
        self.release_jelly_key = GlobalRegex(r"\((\d{2,4}(/|-)\d{1,2}(/|-)\d{1,2})\)")
        self.release = GlobalRegex(r"\(([a-zA-Z0-9 ]*\d{4})\)")
        self.stem = GlobalRegex(
            rf"\(stemtype{JS_SPACE}+((?:{STEMS})(?:{JS_SPACE}+(?:{STEMS}))*)\b\)"
        )
        self.stem_title = GlobalRegex(rf"\(({STEMS})\)")


STEM_NAMES = {
    "t": "Topre",
    "topre": "Topre",
    "mx": "MX",
    "alps": "Alps",
    "tmx": "TMX",
    "choc": "Choc",
    "bs": "BS",
}

# (field, value, tag) in the order catalog-sync checks them
ATTRIBUTES = (
    ("profile", "sculpted", "(ka_profile_sculpt)"),
    ("profile", "blank", "(ka_profile_blank)"),
    ("design", "physical", "(ka_design_physical)"),
    ("design", "digital", "(ka_design_digital)"),
    ("design", "hybrid", "(ka_design_hybrid)"),
    ("cast", "resin", "(ka_cast_resin)"),
    ("cast", "mixed", "(ka_cast_mixed)"),
)

# Markers some makers add to colorway names, checked in this order after the
# common tags: (marker, fields it sets, text it removes, first match each).
MAKER_MARKERS: dict[str, tuple[tuple[str, Row, tuple[str, ...]], ...]] = {
    "fraktal-kaps": (
        ("\u00b0", {"sale_type": "Commission"}, ("\u00b0",)),
        ("*", {"qty": 1}, ("*",)),
    ),
    "hello": (("*", {"sale_type": "Commission"}, ("( * )", "*")),),
    "keycat": (("(GB)", {"sale_type": "Group Buy"}, ("(GB)",)),),
}


def normalize(text: str) -> str:
    text = text.replace("(ka_cover)", "", 1)
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    # zero-width marks found in cysm's sculpt names
    text = text.replace("\u200e", "").replace("\u200f", "").replace("\u200b", "")
    return js_trim(text.replace("  ", " "))


def normalize_stem(text: str) -> str:
    return STEM_NAMES.get(text.lower(), text.lower())


def _paragraph_text(node: Any, *, trim: bool) -> str:
    """The text runs of a paragraph; raises like the JavaScript when the node
    is not a paragraph of text runs (an image, a nested table)."""
    runs = [element["textRun"]["content"] for element in node["paragraph"]["elements"]]
    return "".join(js_trim(run) if trim else run for run in runs)


def parse_sculpt(
    table: Any, maker_id: str, rx: Regexes
) -> tuple[Row, list[str] | None]:
    content = table["table"]["tableRows"][0]["tableCells"][0]["content"]
    title_node = content[0] if content else None
    attr_node = content[1] if len(content) > 1 else None

    text = _paragraph_text(title_node, trim=True)
    subtext = _paragraph_text(attr_node, trim=False).lower() if attr_node else ""

    sculpt: Row = {
        "maker_id": maker_id,
        "release": None,
        "design": None,
        "profile": None,
        "cast": None,
    }

    match = rx.release.exec(text) or rx.release.exec(subtext)
    if match:
        sculpt["release"] = dates.month_year(js_trim(match.group(1)))
        text = rx.release.replace(text)

    if maker_id == "jelly-key":
        match = rx.release_jelly_key.exec(text)
        if match:
            sculpt["release"] = dates.full_date(match.group(1))
            text = rx.release_jelly_key.replace(text)
        if rx.artisan_keycap.exec(text):
            text = rx.artisan_keycap.replace(text)

    for field, value, tag in ATTRIBUTES:
        if tag in subtext:
            sculpt[field] = value
            subtext = subtext.replace(tag, "", 1)
        if tag in text:
            sculpt[field] = value
            text = text.replace(tag, "", 1)

    # a stem in the sculpt name applies to all its colorways
    stem = None
    match = rx.stem_title.exec(text)
    if match:
        stem = [normalize_stem(match.group(1))]
        text = rx.stem_title.replace(text)

    sculpt["name"] = normalize(text)
    sculpt["sculpt_id"] = url_slugify(sculpt["name"])
    return sculpt, stem


def parse_colorways(
    table: Any,
    document: Any,
    maker_id: str,
    sculpt: Row,
    stem: list[str] | None,
    base_url: str,
    rx: Regexes,
) -> list[Row]:
    cells = [cell for row in table["table"]["tableRows"] for cell in row["tableCells"]]
    colorways = []

    for order, cell in enumerate(cells):
        elements = [e for c in cell["content"] for e in c["paragraph"]["elements"]]
        colorway: Row = {
            "name": "",
            "sculpt_id": sculpt["sculpt_id"],
            "maker_id": maker_id,
            "release": None,
            "qty": None,
            "photo_credit": None,
            "stem": stem,
            "order": order,
        }

        texts = []
        for element in elements:
            element = element or {}
            content = element.get("textRun", {}).get("content")
            if content:
                texts.append(js_trim(content))
            object_id = element.get("inlineObjectElement", {}).get("inlineObjectId")
            if object_id:
                obj = document["inlineObjects"][object_id]
                image = (
                    obj.get("inlineObjectProperties", {})
                    .get("embeddedObject", {})
                    .get("imageProperties", {})
                    .get("contentUri")
                )
                if image is not None:
                    colorway["remote_img"] = image
                else:
                    colorway.pop("remote_img", None)
                colorway["colorway_id"] = obj["objectId"]
                colorway["img"] = delivery_url(
                    base_url,
                    f"artisan/{maker_id}/{sculpt['sculpt_id']}/{obj['objectId']}",
                )

        text = " ".join(texts)

        if match := rx.release.exec(text):
            colorway["release"] = dates.month_year(js_trim(match.group(1)))
            text = rx.release.replace(text)
        if match := rx.qty.exec(text):
            colorway["qty"] = int(match.group(1))
            text = rx.qty.replace(text)
        if rx.oneoff.exec(text):
            colorway["qty"] = 1
            text = rx.oneoff.replace(text)
        if match := rx.qty_same_number_fraction.exec(text):
            colorway["qty"] = int(match.group(1))
            text = rx.qty_same_number_fraction.replace(text)
        if rx.auction.exec(text):
            colorway["sale_type"] = "Auction"
            text = rx.auction.replace(text)
        if rx.commission.exec(text):
            colorway["sale_type"] = "Commission"
            text = rx.commission.replace(text)
        if rx.giveaway.exec(text):
            colorway["sale_type"] = "Giveaway"
            text = rx.giveaway.replace(text)
        if match := rx.photo_credit.exec(text):
            colorway["photo_credit"] = match.group(1)
            text = rx.photo_credit.replace(text)

        stem_match = rx.stem.exec(text)
        stem_text_match = rx.stem_title.exec(text)
        if stem_match:
            colorway["stem"] = [
                normalize_stem(s) for s in stem_match.group(1).split(" ")
            ]
            text = rx.stem.replace(text)
        elif stem_text_match:
            colorway["stem"] = [normalize_stem(stem_text_match.group(1))]
            text = rx.stem_title.replace(text)

        for marker, fields, removed in MAKER_MARKERS.get(maker_id, ()):
            if marker in text:
                colorway.update(fields)
                for part in removed:
                    text = text.replace(part, "", 1)

        colorway["name"] = normalize(text)
        colorways.append(colorway)

    return colorways


def _is_header(table: Any) -> bool:
    try:
        content = table["table"]["tableRows"][0]["tableCells"][0]["content"]
        _paragraph_text(content[0] if content else None, trim=True)
    except (KeyError, IndexError, TypeError):
        return False
    return True


def merge_consecutive_tables(tables: list[Any]) -> list[Any]:
    """A colorway grid split over several tables (a page break) becomes one;
    the rows are appended to the first, as catalog-sync does."""
    merged: list[Any] = []
    for table in tables:
        if merged and not _is_header(table) and not _is_header(merged[-1]):
            merged[-1]["table"]["tableRows"].extend(table["table"]["tableRows"])
        else:
            merged.append(table)
    return merged


def parse_document(
    document: Any, maker_id: str, base_url: str, rx: Regexes
) -> dict[str, Row]:
    """Sculpts keyed by maker_sculpt_id, in document order, each with its
    `colorways` (only cells with an image). Mutates `document`."""
    tables = [block for block in document["body"]["content"] if "table" in block]
    tables = merge_consecutive_tables(tables)

    sculpts: dict[str, Row] = {}
    for start in range(0, len(tables), 2):
        pair = tables[start : start + 2]
        if len(pair) == 1:
            continue
        sculpt, stem = parse_sculpt(pair[0], maker_id, rx)
        colorways = parse_colorways(
            pair[1], document, maker_id, sculpt, stem, base_url, rx
        )
        sculpt["colorways"] = [c for c in colorways if c.get("img")]
        if sculpt["name"] and sculpt["colorways"]:
            # keyBy: a later sculpt with the same slug replaces the earlier
            sculpts[f"{maker_id}/{sculpt['sculpt_id']}"] = sculpt
    return sculpts
