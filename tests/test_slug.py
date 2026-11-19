"""url_slugify: python-slugify with Keebdex's punctuation rules, plus the
slugs catalog-sync already gave names python-slugify would slug differently."""

import pytest

from aviary.common.ids import crc32_hex
from aviary.common.slug import LEGACY_SLUGS, url_slugify


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Darth Looga", "darth-looga"),
        ("Jack o' Lantern", "jack-o-lantern"),
        ("R.I.P. (2023)", "rip-2023"),
        ("Tutr\xe9", "tutre"),
        ("Stra\xdfe", "strasse"),
        ("Red M&M", "red-mandm"),
        ("100% & more", "100percent-and-more"),
        ("Price $5", "price-dollar5"),
        ("RTG Caps_", "rtg-caps_"),
        ("trailing - dash -", "trailing-dash"),
        ("x+y=z", "xy-z"),  # + is dropped, = separates
        ("\u4e2d\u6587 sculpt", "zhong-wen-sculpt"),  # new names transliterate
        ("", ""),
    ],
)
def test_url_slugify(text, slug):
    assert url_slugify(text) == slug


@pytest.mark.parametrize(("name", "slug"), sorted(LEGACY_SLUGS.items()))
def test_names_catalog_sync_slugged_keep_their_slug(name, slug):
    assert url_slugify(name) == slug
    assert url_slugify(f" {name} ") == slug  # scrapers slug untrimmed names


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("alpha-keycaps-keypora-blue-Topre", "2b1851e8"),
        ("keycat-balo-pink-camo", "606548ed"),
        ("keycat-spaceship-tutr\xe9", "b2e62c82"),
        ("", "0"),
    ],
)
def test_crc32_matches_javascript(text, expected):
    assert crc32_hex(text) == expected
