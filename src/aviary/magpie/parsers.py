"""Geekhack pages to data: a board's topic list and a topic's first post.
Pure functions, no I/O."""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag

from aviary.magpie.geekhack import canonical_url, is_internal, normalize_image_url
from aviary.magpie.kits import classify_kit_label

_TOPIC_ID_RE = re.compile(r"topic=(\d+)")
_REPLIES_RE = re.compile(r"(\d[\d,]*)\s+Repl")
_VIEWS_RE = re.compile(r"(\d[\d,]*)\s+View")
_DATE_RE = re.compile(r"\w{3}, \d{1,2} \w+ \d{4}, \d{2}:\d{2}:\d{2}")
# Pagination links only ever point at the board being read.
_BOARD_OFFSET_RE = re.compile(r"board=\d+\.(\d+)")
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp")


@dataclass(frozen=True)
class TopicSummary:
    """One row of a board's topic list. `last_post_at` is as Geekhack prints
    it ("Thu, 05 November 2026, 19:34:26")."""

    topic_id: int
    title: str
    author: str
    replies: int
    views: int
    last_post_at: str
    is_sticky: bool = False
    is_locked: bool = False


@dataclass(frozen=True)
class BoardPage:
    topics: list[TopicSummary]
    next_offset: int | None


@dataclass(frozen=True)
class KitImage:
    """An image of the first post and the kit (a `kit_categories` slug) whose
    label precedes it: None before any label. `label` is the matched text."""

    url: str
    kit: str | None = None
    label: str = ""


@dataclass(frozen=True)
class PostContent:
    """What magpie reads from the body of a first post."""

    kit_images: list[KitImage] = field(default_factory=list)
    vendor_links: list[str] = field(default_factory=list)
    # image URLs rejected as "not a URL"
    dropped_image_urls: int = 0

    @property
    def image_urls(self) -> list[str]:
        return [image.url for image in self.kit_images]


@dataclass(frozen=True)
class TopicDetail(PostContent):
    """A topic's first post: the page's title, author, and posting time, and
    the post itself (`body`, its `div.post` HTML, kept so a newer extractor
    can read it again without crawling)."""

    title: str = ""
    author: str = ""
    posted_at: str = ""
    body: str = ""


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _text(node: Tag | None, separator: str = "") -> str:
    return node.get_text(separator) if node is not None else ""


def _attr(node: Tag | None, name: str) -> str:
    if node is None:
        return ""
    value = node.get(name)
    if isinstance(value, list):
        return " ".join(value)
    return value or ""


def parse_board(html: str, offset: int = 0) -> BoardPage:
    soup = _soup(html)
    topics = [t for row in soup.select("tr") if (t := _parse_row(row)) is not None]
    return BoardPage(topics=topics, next_offset=_next_offset(soup, offset))


def _parse_row(row: Tag) -> TopicSummary | None:
    subject = row.select_one("td.subject")
    if subject is None:
        return None
    link = subject.select_one('span[id^="msg_"] a')
    if link is None:
        return None
    match = _TOPIC_ID_RE.search(_attr(link, "href"))
    if match is None:
        return None

    icon = row.select_one("td.icon1")
    icon_src = _attr(icon.select_one("img") if icon else None, "src")
    stats_text = _text(row.select_one("td.stats"), " ")
    date_match = _DATE_RE.search(_text(row.select_one("td.lastpost"), " "))

    return TopicSummary(
        topic_id=int(match.group(1)),
        title=link.get_text().strip(),
        author=_text(subject.select_one("p a")).strip(),
        replies=_number(_REPLIES_RE, stats_text),
        views=_number(_VIEWS_RE, stats_text),
        last_post_at=date_match.group(0) if date_match else "",
        is_sticky="stickybg" in _attr(icon, "class"),
        is_locked="locked" in icon_src,
    )


def _number(pattern: re.Pattern[str], text: str) -> int:
    match = pattern.search(text)
    return int(match.group(1).replace(",", "")) if match else 0


def _next_offset(soup: BeautifulSoup, offset: int) -> int | None:
    offsets = {
        int(m.group(1))
        for a in soup.select("div.pagelinks a.navPages")
        if (m := _BOARD_OFFSET_RE.search(_attr(a, "href")))
    }
    later = [o for o in offsets if o > offset]
    return min(later) if later else None


def parse_topic(html: str) -> TopicDetail:
    """The first post (the announcement) only; replies are ignored."""
    soup = _soup(html)
    post = soup.select_one("div.post")
    if post is None:
        raise ValueError("no post found in topic page")

    date_match = _DATE_RE.search(_text(soup.select_one("div.keyinfo div.smalltext")))
    body = str(post)
    # read from the stored body, so a later run over it reads the same
    content = parse_post(body)
    return TopicDetail(
        title=_text(soup.select_one("title")).strip(),
        author=_text(soup.select_one("div.poster h4 a")).strip(),
        posted_at=date_match.group(0) if date_match else "",
        body=body,
        kit_images=content.kit_images,
        vendor_links=content.vendor_links,
        dropped_image_urls=content.dropped_image_urls,
    )


def parse_post(body: str) -> PostContent:
    """Images (with their kit) and vendor links of a first post's HTML."""
    post = _soup(body).select_one("div.post")
    if post is None:
        raise ValueError("no post in the stored body")
    kit_images, image_links, dropped = _images(post)
    return PostContent(
        kit_images=kit_images,
        vendor_links=_vendor_links(post, image_links),
        dropped_image_urls=dropped,
    )


def _is_image_link(href: str) -> bool:
    return "action=dlattach" in href or urlsplit(href).path.lower().endswith(
        _IMAGE_EXTENSIONS
    )


def _images(post: Tag) -> tuple[list[KitImage], set[str], int]:
    """Images in document order, each with the kit whose label last appeared
    before it; the links used as full-size images; how many were dropped."""
    images: list[KitImage] = []
    seen: set[str] = set()
    used_links: set[str] = set()
    dropped = 0
    kit: str | None = None
    label = ""
    for node in post.descendants:
        # only plain text: comments, CDATA and the like are NavigableString
        # subclasses
        if type(node) is NavigableString:
            if (found := classify_kit_label(str(node))) is not None:
                kit, label = found, str(node).strip()
            continue
        if not isinstance(node, Tag) or node.name != "img":
            continue
        src = _attr(node, "src")
        if not src or "smiley" in _attr(node, "class") or "/Smileys/" in src:
            continue
        # The wrapping link points at the full-size image; the <img> may be a
        # thumbnail.
        parent = node.parent
        href = _attr(parent, "href")
        if parent is not None and parent.name == "a" and _is_image_link(href):
            used_links.add(canonical_url(href))
            src = href
        url = normalize_image_url(canonical_url(src))
        if url is None:
            dropped += 1
        elif url not in seen:
            seen.add(url)
            images.append(KitImage(url=url, kit=kit, label=label))
    return images, used_links, dropped


def _vendor_links(post: Tag, image_links: set[str]) -> list[str]:
    links: list[str] = []
    for anchor in post.select("a"):
        href = _attr(anchor, "href")
        if not href.startswith(("http://", "https://")):
            continue
        url = canonical_url(href)
        if is_internal(url) or url in image_links or url in links:
            continue
        links.append(url)
    return links
