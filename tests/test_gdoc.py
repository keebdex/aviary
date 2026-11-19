"""The Google Docs parser, dates, and multi-doc merge on small hand-made docs.

The port was checked against catalog-sync's parser on every maker's real doc
(identical output); these cases pin the rules down.
"""

import pytest

from aviary.bowerbird.gdoc import dates
from aviary.bowerbird.gdoc.importer import merge_documents
from aviary.bowerbird.gdoc.parser import Regexes, parse_document

BASE = "https://imagedelivery.net/h"


def paragraph(*runs):
    return {"paragraph": {"elements": [{"textRun": {"content": r}} for r in runs]}}


def image_cell(object_id, *texts):
    elements = [{"inlineObjectElement": {"inlineObjectId": object_id}}]
    elements += [{"textRun": {"content": t}} for t in texts]
    return {"content": [{"paragraph": {"elements": elements}}]}


def table(*rows):
    return {"table": {"tableRows": [{"tableCells": list(cells)} for cells in rows]}}


def document(*blocks, images=()):
    return {
        "body": {"content": list(blocks)},
        "inlineObjects": {
            i: {
                "objectId": i,
                "inlineObjectProperties": {
                    "embeddedObject": {"imageProperties": {"contentUri": f"src/{i}"}}
                },
            }
            for i in images
        },
    }


def title(*nodes):
    return table([{"content": list(nodes)}])


def test_sculpt_and_colorway_tags():
    doc = document(
        title(
            paragraph("Blob (MX) ", "(March 2021)"),
            paragraph("(ka_cast_resin) (KA_DESIGN_DIGITAL)"),
        ),
        table(
            [
                image_cell("a", "Red (count 5) (pc Someone)"),
                image_cell("b", "Blue (1/1) (auction) (stemtype topre mx)"),
                {"content": [paragraph("no image")]},
            ]
        ),
        images=["a", "b"],
    )
    sculpt = parse_document(doc, "m", BASE, Regexes())["m/blob"]
    assert {k: sculpt[k] for k in ("name", "release", "cast", "design")} == {
        "name": "Blob",
        "release": "Mar 2021",
        "cast": "resin",
        "design": "digital",
    }
    red, blue = sculpt["colorways"]
    assert red == {
        "name": "Red",
        "sculpt_id": "blob",
        "maker_id": "m",
        "release": None,
        "qty": 5,
        "photo_credit": "Someone",
        "stem": ["MX"],
        "order": 0,
        "remote_img": "src/a",
        "colorway_id": "a",
        "img": f"{BASE}/artisan/m/blob/a/public",
    }
    assert (blue["qty"], blue["sale_type"], blue["stem"]) == (
        1,
        "Auction",
        ["Topre", "MX"],
    )


def test_a_grid_split_over_tables_is_merged():
    doc = document(
        title(paragraph("Blob")),
        table([image_cell("a", "Red")]),
        table([image_cell("b", "Blue")]),
        images=["a", "b"],
    )
    colorways = parse_document(doc, "m", BASE, Regexes())["m/blob"]["colorways"]
    assert [(c["name"], c["order"]) for c in colorways] == [("Red", 0), ("Blue", 1)]


def test_docs_merge_with_shifted_orders():
    first = {
        "m/a": {"name": "A", "cast": "resin", "colorways": [{"order": 0}, {"order": 1}]}
    }
    second = {
        "m/a": {"name": "A2", "cast": None, "colorways": [{"order": 0}]},
        "m/b": {"name": "B", "colorways": [{"order": 0}]},
    }
    merged = merge_documents([first, second])
    assert merged["m/a"]["name"] == "A2"
    assert merged["m/a"]["cast"] is None
    assert [c["order"] for c in merged["m/a"]["colorways"]] == [0, 1, 3]
    assert list(merged) == ["m/a", "m/b"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("January 2023", "Jan 2023"),
        ("sept 2023", "sept 2023"),
        ("Jan 2023", "Jan 2023"),
        ("j 2023", "j 2023"),  # date-fns took a one-letter month
        ("Summer 2023", "Summer 2023"),
        ("2023", "2023"),
    ],
)
def test_month_year(text, expected):
    assert dates.month_year(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2023/5/17", "17 May 2023"),
        ("2023/17/5", "17 May 2023"),  # day first when month first fails
        ("23/5/17", "23/5/17"),  # date-fns read a 2-digit year as year 23
        ("2023-5-17", "2023-5-17"),
    ],
)
def test_full_date(text, expected):
    assert dates.full_date(text) == expected
