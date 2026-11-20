"""Geekhack addresses: boards, topics, and the URLs found in posts."""

import ipaddress
import re
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urljoin, urlsplit

FORUM = "geekhack"
BASE_URL = "https://geekhack.org"

Board = Literal["ic", "gb"]
# The boards magpie reads, by the short name used on the command line and in
# `ingest.threads.board`.
BOARDS: dict[str, int] = {"ic": 132, "gb": 70}

_SESSION_RE = re.compile(r"PHPSESSID=[^&;#]*[&;]?")
_TOPIC_ID_RE = re.compile(r"topic=(\d+)")
_BBCODE_CLOSING_TAG_RE = re.compile(r"\[/[A-Za-z]+\]")
_HOST_LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")
_PREFIX_RE = re.compile(r"^\s*\[([^\]]+)\]")


def parse_time(text: str) -> datetime | None:
    """A Geekhack timestamp ("Thu, 05 November 2026, 19:34:26"), or None.

    Guests see the forum's own clock with no zone; magpie reads it as UTC. It
    only has to be consistent: listings are compared with earlier listings,
    and posting days are a day off at worst.
    """
    try:
        parsed = datetime.strptime(text.strip(), "%a, %d %B %Y, %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC)


def board_url(board: Board, offset: int = 0) -> str:
    return f"{BASE_URL}/index.php?board={BOARDS[board]}.{offset}"


def topic_url(topic_id: int) -> str:
    """The address Keebdex stores in `keysets.url` for a topic."""
    return f"{BASE_URL}/index.php?topic={topic_id}.0"


def extract_topic_id(value: str) -> int:
    """A bare id, or the id in any URL containing `topic=<id>`."""
    value = value.strip()
    if value.isdigit():
        return int(value)
    match = _TOPIC_ID_RE.search(value)
    if not match:
        raise ValueError(f"cannot find a topic id in {value!r}")
    return int(match.group(1))


def topic_id_of(url: str | None) -> int | None:
    """The topic a Geekhack URL points at (`topic=123`, `topic=123.0`, with or
    without `www.`), or None for any other URL."""
    if not url or not is_internal(url):
        return None
    match = _TOPIC_ID_RE.search(url)
    return int(match.group(1)) if match else None


def canonical_url(url: str) -> str:
    """Make `url` absolute and drop the per-visit PHPSESSID so links stay
    stable."""
    absolute = urljoin(BASE_URL + "/", url)
    cleaned = _SESSION_RE.sub("", absolute)
    return cleaned.rstrip("?&;")


def is_internal(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "geekhack.org" or host.endswith(".geekhack.org")


def extract_prefix(title: str) -> str:
    """The `[GB]` / `[IC]` tag a title starts with, without brackets."""
    match = _PREFIX_RE.match(title)
    return match.group(1).strip() if match else ""


def strip_prefix(title: str) -> str:
    return _PREFIX_RE.sub("", title, count=1).strip()


def normalize_image_url(raw: str) -> str | None:
    """A clean image URL, or None if `raw` is not shaped like a URL (e.g. a
    sentence that got `http://` glued in front of it).

    Only the format is checked, nothing is fetched. A URL is kept untouched
    when it has an http(s) scheme, a real host, and no whitespace; a file
    extension is not required (CDN and forum attachment URLs often have none).
    Stray BBCode left in the post (`.png[/center]`) is cut off first so the
    image behind it is not lost.
    """
    url = _strip_bbcode_tail(raw.strip())
    if not url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not _is_valid_host(parts.hostname):
        return None
    return url


def _strip_bbcode_tail(text: str) -> str:
    """Cut `[/center]`-style closing tags and a lone `]` and what follows.

    Brackets that are part of the URL are kept: `?a[]=1`, `?filter[x]=y`,
    `http://[::1]/`.
    """
    closing = _BBCODE_CLOSING_TAG_RE.search(text)
    if closing:
        text = text[: closing.start()]
    stray = text.find("]")
    if stray != -1 and "[" not in text[:stray]:
        text = text[:stray]
    return text.strip()


def _is_valid_host(host: str | None) -> bool:
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return True
    try:
        ascii_host = host.rstrip(".").encode("idna").decode("ascii")
    except UnicodeError:
        return False
    labels = ascii_host.split(".")
    # "We" or "Thanks" (a sentence's first word) has no dot: not a domain.
    return len(labels) > 1 and all(_HOST_LABEL_RE.fullmatch(label) for label in labels)
