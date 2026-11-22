"""Read a keyset candidate out of a crawled topic. Pure functions, no I/O.

A candidate's payload is shaped like Keebdex's keyset submission: the
`keysets` columns under `keyset` and its `keyset_kits` under `kits`, without
moderation fields. Promoting it inserts both `Pending` with no submitter, the
way catalog-sync's vendor scraper proposes keysets. Keebdex's keyset review
queue lists keysets by their kits, so a payload always carries at least one
kit when it has an image.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from aviary.common.db import Row
from aviary.common.slug import url_slugify
from aviary.magpie.classify import suggest
from aviary.magpie.geekhack import strip_prefix, topic_url
from aviary.magpie.kits import DETECTED_KITS
from aviary.magpie.parsers import KitImage

# Bump when what extract() reads changes: the next crawl extracts every
# fetched thread again (ingest.candidates keeps one row per version and
# content).
EXTRACTOR_VERSION = "magpie-2"
MODULE = "Keyset"
PENDING = "Pending"

# Everything after " | ", " - " or " : " is GB progress or a tagline ("|
# Shipping!", "- GB live", "Medusa : A Gaze Forged in Grief"), and so is
# everything after two spaces, which is where the listing drops a dash ("KBS
# Flat Out  A Motorsport Inspired Keycap Set"). A colon needs spaces on both
# sides, so "Re:Zero" stays whole.
_TAIL_RE = re.compile(r"\s*\|.*$|\s+[-–—:]\s.*$|\s{2,}.*$")  # noqa: RUF001
# Notes in parentheses: progress ("(Cancelled)", "(R2 Update)"), new kits,
# and former names ("(Previously Brown on Beige)").
_STATUS_PARENS_RE = re.compile(
    r"\s*\((?=[^)]*\b(?:cancel\w*|update\w*|extras?|live|shipp\w*|production"
    r"|complete\w*|ended|moq|sold out|in stock|new kits?|previously|formerly)\b)"
    r"[^)]*\)",
    re.IGNORECASE,
)
_ALBUM_MARKERS = ("/a/", "/gallery/")
# The row sculpt of nearly every Cherry-profile keyset in Keebdex; other
# profiles get none.
CHERRY_SCULPT = "1-1-2-3-4-4"

# How sure extract() is of each field (0..1), for staff to see what to check:
# a title follows conventions, the thread starter is often a vendor rather
# than the designer, and the first image is often a render of a board. Names
# still carry taglines and notes often enough that staff should read them.
CONFIDENCE = {
    "profile_id": 1.0,
    "name": 0.6,
    "slug": 0.6,
    "url": 1.0,
    "status": 0.8,
    "ic_date": 0.9,
    "designer": 0.5,
    "sculpt": 0.6,
    "img_labelled": 0.7,
    "img_first": 0.4,
    "kits_labelled": 0.7,
    "kits_cover": 0.4,
}


@dataclass(frozen=True)
class Topic:
    """A crawled topic: what a candidate is read from."""

    topic_id: int
    board: str
    title: str
    author: str
    posted_at: datetime | None
    kit_images: Sequence[KitImage]


@dataclass(frozen=True)
class Extraction:
    """A keyset candidate. `missing` lists what magpie lacks to promote it
    itself; staff can still complete it from the app."""

    name: str | None
    profile_id: str | None
    payload: Row
    confidence: dict[str, float]
    missing: list[str]
    # the keycap code the title starts with ("GMK"), found in Keebdex or not
    profile_code: str | None = None

    @property
    def key(self) -> str | None:
        """The `profile_keyset_id` the keyset would get."""
        slug = self.payload["keyset"].get("slug")
        return f"{self.profile_id}/{slug}" if self.profile_id and slug else None


def clean_keyset_name(title: str, prefixes: Sequence[str] = ()) -> str:
    """The keyset name in a title: no `[GB]` tag, no progress tail, and none
    of `prefixes` (the profile code, then the profile's own name, so "GMK CYL
    Selene" under GMK's "CYL" profile is "Selene"). Never strips the name down
    to nothing."""
    name = strip_prefix(title)
    name = _TAIL_RE.sub("", name)
    name = _STATUS_PARENS_RE.sub("", name)
    name = name.strip(" .!-")
    for prefix in prefixes:
        rest = re.sub(rf"^{re.escape(prefix)}\s+", "", name, flags=re.IGNORECASE)
        name = rest or name
    return name


def guess_status(title: str, board: str) -> str:
    """A `keyset_status`: Cancelled when the title says so, Interest Check on
    the IC board, else Confirmed."""
    if re.search(r"cancel", title, re.IGNORECASE):
        return "Cancelled"
    return "Interest Check" if board == "ic" else "Confirmed"


def is_album(url: str) -> bool:
    """An imgur album page: a link to several images, not an image."""
    return "imgur" in url and any(m in url for m in _ALBUM_MARKERS)


def usable_images(images: Sequence[KitImage]) -> list[KitImage]:
    return [
        i
        for i in images
        if i.url.startswith(("http://", "https://")) and not is_album(i.url)
    ]


def find_profile(code: str, profiles: Sequence[Row]) -> Row | None:
    """The published profile a title's keycap code names, by `profile_id`
    first ("gmk"), then by name ("KKB" is `keykobo`); None when nothing or
    more than one profile matches."""
    code = code.lower()
    for column in ("profile_id", "name"):
        found = [p for p in profiles if (p.get(column) or "").lower() == code]
        if len(found) == 1:
            return found[0]
        if found:
            return None
    return None


def extract(topic: Topic, profiles: Sequence[Row]) -> Extraction | None:
    """The keyset candidate of a topic, or None when its title doesn't look
    like a keyset (keyboards are not extracted yet)."""
    suggestion = suggest(topic.title)
    if suggestion.kind != "keyset":
        return None
    missing: list[str] = []
    confidence: dict[str, float] = {}

    codes = [suggestion.profile_code] if suggestion.profile_code else []
    profile = None
    if codes:
        # A second code names the profile within the maker's: "GMK MTNU
        # Dasher" is MTNU's (`mtnu/dasher`), "GMK CYL Selene" GMK's own line.
        words = strip_prefix(topic.title).split(maxsplit=2)
        if len(words) > 2 and find_profile(words[1], profiles) is not None:
            codes.append(words[1])
        profile = find_profile(codes[-1], profiles)
        if profile is None:
            missing.append(f"profile (no Keebdex profile for {codes[0]})")
    else:
        missing.append("profile (no profile code in the title)")
    profile_id = profile["profile_id"] if profile else None
    if profile_id:
        confidence["profile_id"] = CONFIDENCE["profile_id"]

    prefixes = [*codes, profile["name"]] if profile else codes
    name = clean_keyset_name(topic.title, prefixes)
    if name.lower() in {p.lower() for p in prefixes}:
        missing.append("name (only the profile in the title)")
        name = ""
    slug = url_slugify(name) if name else ""
    if name and not slug:
        missing.append(f"slug (none for {name!r})")
    if slug:
        confidence["name"] = CONFIDENCE["name"]
        confidence["slug"] = CONFIDENCE["slug"]

    images = usable_images(topic.kit_images)
    kit_images: dict[str, str] = {}
    for image in images:
        if image.kit is not None:
            kit_images.setdefault(image.kit, image.url)
    cover = kit_images.get("base") or (images[0].url if images else None)
    if cover is None:
        missing.append("cover image (no image in the first post)")
    else:
        labelled = "base" in kit_images
        confidence["img"] = CONFIDENCE["img_labelled" if labelled else "img_first"]
        confidence["kits"] = CONFIDENCE["kits_labelled" if kit_images else "kits_cover"]

    status = guess_status(topic.title, topic.board)
    ic_date = (
        topic.posted_at.date() if topic.board == "ic" and topic.posted_at else None
    )
    sculpt = CHERRY_SCULPT if profile and profile.get("profile") == "Cherry" else None
    keyset = {
        "profile_id": profile_id,
        "name": name or None,
        "slug": slug or None,
        "designer": topic.author or None,
        "sculpt": sculpt,
        "img": cover,
        "url": topic_url(topic.topic_id),
        "status": status,
        "ic_date": ic_date.isoformat() if ic_date else None,
    }
    for field in ("url", "status", "ic_date", "designer", "sculpt"):
        if keyset[field]:
            confidence[field] = CONFIDENCE[field]

    # No kit label in the post: one Base kit with the cover, so the keyset
    # shows up in the review queue. `name` is only for a kit's own name ("TKL
    # Base"); Keebdex shows the category's name when it is null.
    chosen = kit_images or ({"base": cover} if cover else {})
    kits = [
        {"kit_id": kit, "name": None, "img": chosen[kit]}
        for kit in DETECTED_KITS
        if kit in chosen
    ]
    return Extraction(
        name=name or None,
        profile_id=profile_id,
        profile_code=codes[0] if codes else None,
        payload={"keyset": keyset, "kits": kits},
        confidence=confidence,
        missing=missing,
    )


def missing_for_promotion(payload: Row) -> list[str]:
    """What a stored payload lacks for magpie to insert it: the keyset's
    required columns, a cover, and a kit."""
    keyset = payload.get("keyset") or {}
    missing = [f for f in ("profile_id", "name", "slug", "img") if not keyset.get(f)]
    if not payload.get("kits"):
        missing.append("kits")
    return missing


def keyset_row(payload: Row) -> Row:
    """The `keysets` row a promoted payload inserts. The database sets
    `profile_keyset_id` from profile and slug too; it is sent to the same
    value."""
    keyset = payload["keyset"]
    return {
        **keyset,
        "profile_keyset_id": f"{keyset['profile_id']}/{keyset['slug']}",
        "review_status": PENDING,
        "submitted_by": None,
    }


def kit_rows(payload: Row) -> list[Row]:
    """The `keyset_kits` rows a promoted payload inserts."""
    keyset = payload["keyset"]
    key = f"{keyset['profile_id']}/{keyset['slug']}"
    return [
        {
            **kit,
            "profile_keyset_id": key,
            "review_status": PENDING,
            "submitted_by": None,
        }
        for kit in payload["kits"]
    ]
