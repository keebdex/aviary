"""The ports of urlSlugify and crc32 against the JavaScript originals.

Expected values come from catalog-sync (`urlSlugify` from utils/index.js and
crc32 from the `crc` package). To check another input, run in ../catalog-sync:

    CF_IMAGES_ACCOUNT_HASH=x node -p "require('./utils').urlSlugify('<text>')"
"""

import pytest

from aviary.common.ids import crc32_hex
from aviary.common.slug import url_slugify

SLUGS = [
    ("Tutr\xe9", "tutre"),  # charmap: accented letters
    ("Darth Looga", "darth-looga"),
    ("Jack o' Lantern", "jack-o-lantern"),
    ("R.I.P. (2023)", "rip-2023"),
    ("Caf\xe9 Cr\xe8me", "cafe-creme"),
    ("Stra\xdfe", "strasse"),  # charmap: one letter to two
    ("\xd0o\xe0n Vi\u1ec7t", "doan-viet"),
    ("\u0130stanbul \u0131", "istanbul-i"),
    # not in the charmap: removed, not transliterated
    ("#094 \xd7 Christmas", "094-christmas"),
    # curly quotes map to ASCII ones, which survive
    ("Munkii\u2019s Dream", "munkii's-dream"),
    ("Say \u201cHi\u201d \u2026", 'say-"hi"-...'),
    # CJK is removed, not romanized
    ("Jiaozi | Chinese Dumpling | \u997a\u5b50", "jiaozi-or-chinese-dumpling-or"),
    ("\u30d4\u30f3\u30af Pink", "pink"),
    ("Red M&M", "red-mandm"),  # symbols spelled out
    ("100% & more", "100percent-and-more"),
    ("Price $5", "price-dollar5"),
    ("a < b > c", "a-less-b-greater-c"),
    ("Yip\xb2", "yip"),
    ("\u2153 Third", "third"),
    ("\xa9\xae\u2122", "(c)(r)tm"),  # charmap output keeps punctuation
    ("RTG Caps_", "rtg-caps_"),  # underscores survive
    ("--leading", "leading"),
    ("trailing - dash -", "trailing-dash"),  # hyphens become spaces
    ("en \u2013 dash", "en-dash"),
    ("tab\tand\nnewline", "tab-and-newline"),
    ("nbsp\xa0space", "nbsp-space"),
    ("bom\ufeffword", "bom-word"),  # JavaScript's \s has U+FEFF
    ("fs\x1cchar", "fschar"),  # and lacks U+001C
    ("emoji \U0001f426 bird", "emoji-bird"),
    ("e\u0301 combining", "e-combining"),  # NFC first
    ("\uff26\uff35\uff2c\uff2c\u3000wide", "wide"),  # fullwidth letters are removed
    ("", ""),
    ("   ", ""),
    ("x+y=z", "xyz"),
    ("a/b\\c", "abc"),
    ("[Bracket] ^caret^", "bracket-caret"),
]

CRC32 = [
    ("alpha-keycaps-keypora-blue-Topre", "2b1851e8"),
    ("keycat-balo-pink-camo", "606548ed"),
    ("keycat-spaceship-tutr\xe9", "b2e62c82"),
    ("", "0"),
]


@pytest.mark.parametrize(("text", "slug"), SLUGS)
def test_url_slugify_matches_javascript(text, slug):
    assert url_slugify(text) == slug


@pytest.mark.parametrize(("text", "expected"), CRC32)
def test_crc32_matches_javascript(text, expected):
    assert crc32_hex(text) == expected
