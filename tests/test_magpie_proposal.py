"""Topic titles to keyset candidates."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from aviary.magpie.classify import suggest
from aviary.magpie.geekhack import parse_time
from aviary.magpie.parsers import KitImage, parse_board
from aviary.magpie.proposal import (
    Topic,
    clean_keyset_name,
    extract,
    find_profile,
    guess_status,
    keyset_row,
    kit_rows,
    missing_for_promotion,
)

FIXTURES = Path(__file__).parent / "fixtures" / "magpie"

# shaped like Keebdex's: GMK's profile is named after its CYL line, and KKB is
# found by name
PROFILES = [
    {"profile_id": "gmk", "name": "CYL", "profile": "Cherry"},
    {"profile_id": "keykobo", "name": "KKB", "profile": "Cherry"},
    {"profile_id": "dcs", "name": "DCS", "profile": "DCS"},
    {"profile_id": "mtnu", "name": "MTNU", "profile": "MTNU"},
]
POSTED = datetime(2026, 11, 14, 23, 3, 52, tzinfo=UTC)


def topic(title="[GB] GMK CYL Selene | Production Underway", board="gb", **fields):
    values = {
        "topic_id": 5,
        "board": board,
        "title": title,
        "author": "someone",
        "posted_at": POSTED,
        "kit_images": [KitImage("https://i.example/hero.png")],
    }
    return Topic(**(values | fields))


@pytest.mark.parametrize(
    ("title", "kind", "code"),
    [
        ("[GB] GMK MTNU Dasher", "keyset", "GMK"),
        ("[GB] MW Datenshi | 26 Oct - 16 Nov", "keyset", "MW"),
        ("[GB] DSS Drank Round 2 - 40% Monokit", "keyset", "DSS"),
        ("[GB]PBS-Kids Typewriter keycap set", "keyset", None),
        ("[GB] Code:002 TKL ", "keyboard", None),
        ("[GB]Space66% Spaceforge", "keyboard", None),
        ("[GB] Sagittarius optimized ergo", "keyboard", None),
        ("[GB] Enso-E by Clever | Dec 8 - 22", "unknown", None),
        ("[GB] 60% keycap set", "unknown", None),
        ("Group Buy Rules and Guidelines (Updated 2025-03-07)", "other", None),
        ("", "other", None),
        ("[GB] gmk lowercase", "keyset", "GMK"),
        ("[GB] Sagittarius", "unknown", None),
        ("[IC] GMK CYL Mayan blue", "keyset", "GMK"),
        ("[IC] Radius 68: A Mac-Friendly Keyboard with Nacre", "keyboard", None),
    ],
)
def test_suggest(title, kind, code):
    suggestion = suggest(title)
    assert (suggestion.kind, suggestion.profile_code) == (kind, code)
    assert suggestion.reason


def test_fixture_board_mix():
    page = parse_board((FIXTURES / "board_70_0.html").read_bytes().decode("cp1252"))
    kinds = [suggest(t.title).kind for t in page.topics]
    assert kinds.count("other") == 9
    assert kinds.count("unknown") == 3
    assert kinds.count("keyboard") == 8
    assert kinds.count("keyset") == len(kinds) - 20


@pytest.mark.parametrize(
    ("title", "prefixes", "name"),
    [
        ("[GB] GMK CYL Selene | Production Underway", [], "GMK CYL Selene"),
        ("[GB] GMK CYL Selene | Production Underway", ["GMK", "CYL"], "Selene"),
        (
            "[GB] GMK Orange Alert - 8/14/26 to 9/14/26 | GB Successful",
            ["GMK"],
            "Orange Alert",
        ),
        ("[GB] GMK CYL Purple. | Group Buy is live!", ["GMK", "CYL"], "Purple"),
        ("[GB] SWG Art (cancelled)", ["SWG"], "Art"),
        (
            "[GB] MW Jirai (Doubleshot ABS) | (Cancelled)",
            ["MW"],
            "Jirai (Doubleshot ABS)",
        ),
        (
            "[GB] DCS Cream Cheese R3 (Update: Oct. 27th, 2026)",
            ["DCS"],
            "Cream Cheese R3",
        ),
        ("[GB] DCS Cream – GB live", ["DCS"], "Cream"),  # noqa: RUF001
        ("[GB] GMK", ["GMK"], "GMK"),
        ("[GB] GMK !!!", ["GMK"], "GMK"),
        ("[GB] GMK CYL", ["GMK", "CYL"], "CYL"),
    ],
)
def test_clean_keyset_name(title, prefixes, name):
    assert clean_keyset_name(title, prefixes) == name


def test_guess_status():
    assert guess_status("[GB] KKB Korean Delights - Cancelled", "gb") == "Cancelled"
    assert guess_status("[IC] GMK Boho (cancelled)", "ic") == "Cancelled"
    assert guess_status("[GB] GMK Boho | Shipping", "gb") == "Confirmed"
    assert guess_status("[IC] GMK Boho", "ic") == "Interest Check"


def test_parse_time():
    assert parse_time("Sat, 14 November 2026, 23:03:52") == POSTED
    assert parse_time("Today at 10:00:00") is None
    assert parse_time("") is None


def test_find_profile_by_id_then_name():
    assert find_profile("GMK", PROFILES)["profile_id"] == "gmk"
    assert find_profile("KKB", PROFILES)["profile_id"] == "keykobo"
    assert find_profile("SA", PROFILES) is None
    twins = [{"profile_id": "a", "name": "X"}, {"profile_id": "b", "name": "X"}]
    assert find_profile("X", twins) is None


def test_candidate_from_a_group_buy():
    extraction = extract(topic(), PROFILES)
    assert (extraction.name, extraction.profile_id, extraction.key) == (
        "Selene",
        "gmk",
        "gmk/selene",
    )
    assert extraction.missing == []
    assert extraction.payload == {
        "keyset": {
            "profile_id": "gmk",
            "name": "Selene",
            "slug": "selene",
            "designer": "someone",
            "sculpt": "1-1-2-3-4-4",
            "img": "https://i.example/hero.png",
            "url": "https://geekhack.org/index.php?topic=5.0",
            "status": "Confirmed",
            "ic_date": None,
        },
        # without kit labels, one Base kit with the cover keeps it in the queue
        "kits": [
            {"kit_id": "base", "name": "Base", "img": "https://i.example/hero.png"}
        ],
    }
    # the cover is only the first image: staff should look at it
    assert extraction.confidence["img"] < extraction.confidence["profile_id"]
    assert "ic_date" not in extraction.confidence


def test_promotion_rows_are_pending_without_submitter():
    payload = extract(topic(), PROFILES).payload
    keyset = keyset_row(payload)
    assert keyset["profile_keyset_id"] == "gmk/selene"
    assert (keyset["review_status"], keyset["submitted_by"]) == ("Pending", None)
    assert kit_rows(payload) == [
        {
            "kit_id": "base",
            "name": "Base",
            "img": "https://i.example/hero.png",
            "profile_keyset_id": "gmk/selene",
            "review_status": "Pending",
            "submitted_by": None,
        }
    ]


@pytest.mark.parametrize(
    ("title", "key"),
    [
        ("[GB] GMK MTNU Dasher", "mtnu/dasher"),
        ("[GB] GMK CYL Selene", "gmk/selene"),
        ("[GB] GMK Rainy Day", "gmk/rainy-day"),
        ("[GB] GMK MTNU", "gmk/mtnu"),
        ("[GB] KKB Cobalt - MOQ REACHED!", "keykobo/cobalt"),
    ],
)
def test_the_profile_is_the_one_named_after_the_makers_code(title, key):
    assert extract(topic(title), PROFILES).key == key


def test_interest_check_gets_its_status_and_posting_day():
    extraction = extract(topic("[IC] DCS Boho", board="ic"), PROFILES)
    assert extraction.payload["keyset"]["status"] == "Interest Check"
    assert extraction.payload["keyset"]["ic_date"] == "2026-11-14"
    # only Cherry profiles get the usual row sculpt
    assert extraction.payload["keyset"]["sculpt"] is None


def test_labelled_images_become_kits_and_the_base_one_the_cover():
    images = [
        KitImage("https://i.imgur.com/a/album"),
        KitImage("https://i.example/hero.png"),
        KitImage("https://i.example/nov.png", "novelties", "Novelties"),
        KitImage("https://i.example/nov2.png", "novelties", "Novelties"),
        KitImage("https://i.example/base.png", "base", "Base"),
    ]
    extraction = extract(topic(kit_images=images), PROFILES)
    assert extraction.payload["keyset"]["img"] == "https://i.example/base.png"
    assert extraction.payload["kits"] == [
        {"kit_id": "base", "name": "Base", "img": "https://i.example/base.png"},
        {
            "kit_id": "novelties",
            "name": "Novelties",
            "img": "https://i.example/nov.png",
        },
    ]
    assert extraction.confidence["img"] == 0.7


def test_keyboards_and_other_topics_are_not_extracted():
    assert extract(topic("[GB] Seed Plan Linedge 65 Keyboard"), PROFILES) is None
    assert extract(topic("Group Buy Rules"), PROFILES) is None


@pytest.mark.parametrize(
    ("fields", "missing"),
    [
        ({"title": "[GB] PBS-Kids Typewriter keycap set"}, "no profile code"),
        ({"title": "[GB] SA Bliss"}, "no Keebdex profile for SA"),
        ({"kit_images": []}, "no image"),
        ({"kit_images": [KitImage("https://imgur.com/gallery/x")]}, "no image"),
        ({"title": "[GB] GMK CYL"}, "only the profile"),
        ({"title": "[GB] GMK !!!"}, "only the profile"),
        ({"title": "[GB] GMK \u2605"}, "slug"),
    ],
)
def test_incomplete_candidates_say_what_is_missing(fields, missing):
    extraction = extract(topic(**fields), PROFILES)
    assert any(missing in m for m in extraction.missing)
    # still stored for staff, and never promoted by magpie
    assert missing_for_promotion(extraction.payload)


def test_an_unknown_profile_still_names_the_keyset():
    extraction = extract(topic("[GB] SA Bliss"), PROFILES)
    assert (extraction.name, extraction.profile_id, extraction.key) == (
        "Bliss",
        None,
        None,
    )
    assert missing_for_promotion(extraction.payload) == ["profile_id"]
