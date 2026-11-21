"""Geekhack pages, URLs, and kit labels, on pages saved from the forum."""

from pathlib import Path

import pytest

from aviary.magpie.geekhack import (
    canonical_url,
    extract_topic_id,
    normalize_image_url,
    topic_id_of,
)
from aviary.magpie.kits import classify_kit_label
from aviary.magpie.parsers import parse_board, parse_topic

FIXTURES = Path(__file__).parent / "fixtures" / "magpie"


def read_fixture(name: str) -> str:
    # the decoding the Fetcher applies to live pages
    return (FIXTURES / name).read_bytes().decode("cp1252")


@pytest.fixture
def gb_board() -> str:
    return read_fixture("board_70_0.html")


@pytest.fixture
def topic_html() -> str:
    return read_fixture("topic_127176.html")


def test_board_page_has_50_topics_with_8_stickies(gb_board):
    page = parse_board(gb_board, 0)
    assert len(page.topics) == 50
    assert sum(t.is_sticky for t in page.topics) == 8
    assert page.next_offset == 50


def test_board_topic_fields(gb_board):
    topic = next(t for t in parse_board(gb_board).topics if t.topic_id == 127176)
    assert topic.title == "[GB] DSS Drank Round 2 - 40% Monokit"
    assert topic.author == "MikeTheTiger"
    assert (topic.replies, topic.views) == (19, 39818)
    assert topic.last_post_at == "Thu, 05 November 2026, 19:34:26"
    assert not topic.is_sticky


def test_board_sticky_is_flagged(gb_board):
    sticky = next(t for t in parse_board(gb_board).topics if t.topic_id == 57761)
    assert sticky.is_sticky


def test_last_board_page_has_no_next_offset(gb_board):
    assert parse_board(gb_board, 10_000).next_offset is None


def test_ic_board_parses_like_the_gb_board():
    page = parse_board(read_fixture("board_132_0.html"))
    assert len(page.topics) == 50
    assert [t.topic_id for t in page.topics if t.is_sticky] == [36672]
    assert page.next_offset == 50
    topic = next(t for t in page.topics if t.topic_id == 126906)
    assert topic.title == "[IC] GMK Knight"
    assert topic.author == "Ephem_eral"
    assert topic.last_post_at == "Sat, 14 November 2026, 23:03:52"


def test_topic_fields(topic_html):
    detail = parse_topic(topic_html)
    assert detail.title == "[GB] DSS Drank Round 2 - 40% Monokit"
    assert detail.author == "MikeTheTiger"
    assert detail.posted_at == "Sun, 04 October 2026, 13:18:44"


def test_topic_images_are_only_from_the_first_post(topic_html):
    assert parse_topic(topic_html).image_urls == [
        "https://geekhack.org/index.php?action=dlattach;topic=127176.0;attach=318293;image",
        "https://geekhack.org/index.php?action=dlattach;topic=127176.0;attach=318301;image",
    ]


def test_vendor_links_leave_out_images_and_the_forum(topic_html):
    detail = parse_topic(topic_html)
    assert detail.vendor_links[0] == "https://keyboardarcade.xyz"
    assert all("geekhack.org" not in link for link in detail.vendor_links)
    assert not any("PHPSESSID" in u for u in detail.image_urls + detail.vendor_links)


def test_topic_without_a_post_raises():
    with pytest.raises(ValueError):
        parse_topic("<html><body>nothing</body></html>")


def test_topic_drops_non_url_images_and_cleans_bbcode_tails():
    html = """
    <html><head><title>[GB] Demo</title></head><body>
    <div class="poster"><h4><a>someone</a></h4></div>
    <div class="keyinfo"><div class="smalltext">
      on: Sun, 04 October 2026, 13:18:44
    </div></div>
    <div class="post"><div class="inner">
      <img src="https://i.imgur.com/ok.png" alt="">
      <img src="http://We have re-introduced the 4mm PEEK plate. High-end" alt="">
      <img src="https://i.imgur.com/tail.png[/center]" alt="">
      <img src="https://i.imgur.com/ok.png" alt="">
      <img src="https://imagedelivery.net/abc/def/public" alt="">
    </div></div></body></html>
    """
    detail = parse_topic(html)
    assert detail.image_urls == [
        "https://i.imgur.com/ok.png",
        "https://i.imgur.com/tail.png",
        "https://imagedelivery.net/abc/def/public",
    ]
    assert detail.dropped_image_urls == 1


def test_images_follow_the_kit_label_before_them():
    html = """
    <html><head><title>[GB] Demo</title></head><body>
    <div class="post"><div class="inner">
      <img src="https://i.imgur.com/hero.png" alt="">
      <b>Base Kit</b><br /><img src="https://i.imgur.com/base.png" alt="">
      <!-- Spacebars -->
      <br /><span>Novelties</span><br />
      <a href="https://i.imgur.com/nov-full.png"><img src="https://i.imgur.com/nov.png"></a>
      Convex DSA Space Bars<br /><img src="https://i.imgur.com/still-nov.png" alt="">
      Spacebars<br /><img src="https://i.imgur.com/base.png" alt="">
      <img src="https://i.imgur.com/space.png" alt="">
    </div></div></body></html>
    """
    detail = parse_topic(html)
    assert [(i.url, i.kit, i.label) for i in detail.kit_images] == [
        ("https://i.imgur.com/hero.png", None, ""),
        ("https://i.imgur.com/base.png", "base", "Base Kit"),
        ("https://i.imgur.com/nov-full.png", "novelties", "Novelties"),
        ("https://i.imgur.com/still-nov.png", "novelties", "Novelties"),
        ("https://i.imgur.com/space.png", "spacebars", "Spacebars"),
    ]


def test_topic_without_kit_labels_leaves_images_unassigned(topic_html):
    assert {i.kit for i in parse_topic(topic_html).kit_images} == {None}


@pytest.mark.parametrize(
    ("text", "kit"),
    [
        ("Base", "base"),
        ("Base Kit", "base"),
        ("  NOVELTIES: ", "novelties"),
        ("Novelty Kit", "novelties"),
        ("Extra Novelties", "novelties"),
        ("Spacebars", "spacebars"),
        ("Space bars - $30", "spacebars"),
        ("Space bars – $30", "spacebars"),  # noqa: RUF001
        ("Spacebar kit 35", "spacebars"),
    ],
)
def test_kit_labels(text, kit):
    assert classify_kit_label(text) == kit


@pytest.mark.parametrize(
    "text",
    [
        "",
        "database",
        "Convex DSA Space Bars",
        "Base kit includes alphas",
        "Monokit: $130",
    ],
)
def test_not_kit_labels(text):
    assert classify_kit_label(text) is None


def test_canonical_url_drops_the_session_and_makes_urls_absolute():
    assert (
        canonical_url("https://geekhack.org/index.php?PHPSESSID=abc123&topic=5.0")
        == "https://geekhack.org/index.php?topic=5.0"
    )
    assert (
        canonical_url("index.php?topic=5.0;PHPSESSID=abc")
        == "https://geekhack.org/index.php?topic=5.0"
    )


def test_extract_topic_id():
    assert extract_topic_id("127176") == 127176
    assert extract_topic_id("https://geekhack.org/index.php?topic=127176.0") == 127176
    with pytest.raises(ValueError):
        extract_topic_id("https://example.com")


@pytest.mark.parametrize(
    ("url", "topic_id"),
    [
        ("https://geekhack.org/index.php?topic=110062.0", 110062),
        ("https://geekhack.org/index.php?topic=91883", 91883),
        ("http://www.geekhack.org/index.php?topic=5.msg1#msg1", 5),
        ("https://novelkeys.xyz/products/topic=5", None),
        ("https://geekhack.org/index.php?board=70.0", None),
        (None, None),
        ("", None),
    ],
)
def test_topic_id_of(url, topic_id):
    assert topic_id_of(url) == topic_id


@pytest.mark.parametrize(
    "url",
    [
        "https://i.imgur.com/a.png",
        "https://geekhack.org/index.php?action=dlattach;topic=1.0;attach=2;image",
        "https://imagedelivery.net/x/y/public",
        "https://cdn.example.com:8443/a.png",
        "http://192.168.0.10/a.png",
        "https://cdn.example.com/img.php?a[]=1&b[]=2",
        "https://cdn.example.com/img.php?filter[size]=big",
        "http://[::1]/a.png",
        "https://example.com./a.png",
        "https://例え.jp/a.png",
    ],
)
def test_real_image_urls_are_kept_untouched(url):
    assert normalize_image_url(url) == url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://i.imgur.com/a.png[/center]", "https://i.imgur.com/a.png"),
        ("https://i.postimg.cc/x/r3.jpg]", "https://i.postimg.cc/x/r3.jpg"),
        ("  https://i.imgur.com/a.png  ", "https://i.imgur.com/a.png"),
    ],
)
def test_bbcode_tails_and_whitespace_are_cut(raw, expected):
    assert normalize_image_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://We are introducing the new set",
        "https://We",
        "http://localhost/a.png",
        "ftp://x.com/a.png",
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "//cdn.example.com/a.png",
        "",
        "https://",
        "https://-bad.example/a.png",
        "https://exa mple.com/a.png",
        "https://example.com/a\x00.png",
        "https://[/center]x.png",
    ],
)
def test_things_that_are_not_urls_are_dropped(raw):
    assert normalize_image_url(raw) is None
