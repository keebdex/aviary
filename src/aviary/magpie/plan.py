"""What magpie writes, decided without a database: how far to list a board,
which threads to fetch again and when, and which candidates it promotes
itself. Pure functions, tested in tests/test_magpie_plan.py."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from aviary.common.db import Row
from aviary.magpie.geekhack import FORUM, parse_time, topic_id_of, topic_url
from aviary.magpie.parsers import BoardPage, TopicSummary
from aviary.magpie.proposal import missing_for_promotion

# A thread posted this recently is fetched again every REFETCH_EVERY, since
# editing the first post does not bump it on the board.
ACTIVE_FOR = timedelta(days=90)
REFETCH_EVERY = timedelta(days=7)
# Failed fetches back off from an hour, doubling, up to a week; after
# MAX_FETCH_ATTEMPTS in a row the thread waits for new activity in a listing.
RETRY_FIRST = timedelta(hours=1)
RETRY_MAX = timedelta(days=7)
MAX_FETCH_ATTEMPTS = 5


def stored_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass
class BoardListing:
    """The pages of a board read by one run, newest last post first.

    A listing stops at the first page that reaches `watermark` (the newest
    last post the previous succeeded run saw), at the board's last page, or
    at the run's page cap. Stopped by the cap, it is incomplete: threads below
    may have been missed, so the board keeps its old watermark.
    """

    board: str
    watermark: datetime | None
    seen: dict[int, TopicSummary] = field(default_factory=dict)
    pages: int = 0
    reached_end: bool = False
    reached_watermark: bool = False
    newest: datetime | None = None

    def add_page(self, page: BoardPage) -> None:
        """Record a page. Stickies (rules, FAQs) are skipped, and a thread
        seen twice (bumped while the run went on) counts once, as first
        seen."""
        self.pages += 1
        self.reached_end = page.next_offset is None
        for topic in page.topics:
            if topic.is_sticky or topic.topic_id in self.seen:
                continue
            self.seen[topic.topic_id] = topic
            when = parse_time(topic.last_post_at)
            if when is None:
                continue
            if self.newest is None or when > self.newest:
                self.newest = when
            if self.watermark is not None and when <= self.watermark:
                self.reached_watermark = True

    @property
    def done(self) -> bool:
        return self.reached_end or self.reached_watermark

    def next_watermark(self) -> datetime | None:
        if not self.done or self.newest is None:
            return self.watermark
        if self.watermark is None:
            return self.newest
        return max(self.newest, self.watermark)


@dataclass
class ListingPlan:
    """`ingest.threads` upserts for a listing: `due` rows (new threads, and
    threads with new activity, gone ones included) are fetched next, `seen`
    rows only refresh what the listing shows."""

    due: list[Row] = field(default_factory=list)
    seen: list[Row] = field(default_factory=list)
    new: int = 0


def plan_listing(
    listing: BoardListing, stored: dict[str, Row], now: datetime
) -> ListingPlan:
    plan = ListingPlan()
    for topic in listing.seen.values():
        external_id = str(topic.topic_id)
        when = parse_time(topic.last_post_at)
        row = {
            "source": FORUM,
            "external_id": external_id,
            "url": topic_url(topic.topic_id),
            "board": listing.board,
            "title": topic.title,
            "author": topic.author or None,
            "reply_count": topic.replies,
            "last_post_at": when.isoformat() if when else None,
            "listed_at": now.isoformat(),
        }
        old = stored.get(external_id)
        if old is None:
            plan.new += 1
        elif stored_time(old.get("last_post_at")) == when:
            # nothing new: a gone thread stays gone (the forum can list a
            # topic it then refuses to show)
            plan.seen.append(row)
            continue
        # new activity starts the retries over: a thread that ran out of
        # attempts gets the full backoff again, not a single try
        plan.due.append(
            {
                **row,
                "next_fetch_at": now.isoformat(),
                "gone_at": None,
                "fetch_attempts": 0,
                "fetch_error": None,
            }
        )
    return plan


def refetch_at(posted_at: datetime | None, now: datetime) -> datetime | None:
    """When a fetched thread is read again without new activity: weekly while
    recent, else only when a listing shows new activity."""
    if posted_at is not None and now - posted_at <= ACTIVE_FOR:
        return now + REFETCH_EVERY
    return None


def retry_at(attempts: int, now: datetime) -> datetime | None:
    """When a thread whose fetch failed `attempts` times in a row is tried
    again."""
    if attempts >= MAX_FETCH_ATTEMPTS:
        return None
    return now + min(RETRY_FIRST * 2 ** (attempts - 1), RETRY_MAX)


class KeysetIndex:
    """Keebdex keysets by the Geekhack topic their `url` points at and by
    key (`profile_keyset_id`, or profile and slug), of every source and
    review status."""

    def __init__(self, keysets: Sequence[Row]):
        self.by_topic: dict[int, Row] = {}
        self.by_key: dict[str, Row] = {}
        for keyset in keysets:
            if (topic_id := topic_id_of(keyset.get("url"))) is not None:
                self.by_topic.setdefault(topic_id, keyset)
            self.by_key[keyset["profile_keyset_id"]] = keyset
            self.by_key.setdefault(f"{keyset['profile_id']}/{keyset['slug']}", keyset)

    def match(self, topic_id: int, key: str | None) -> Row | None:
        found = self.by_topic.get(topic_id)
        if found is None and key is not None:
            found = self.by_key.get(key)
        return found


def payload_key(payload: Row) -> str | None:
    keyset = payload.get("keyset") or {}
    if keyset.get("profile_id") and keyset.get("slug"):
        return f"{keyset['profile_id']}/{keyset['slug']}"
    return None


@dataclass(frozen=True)
class Promotion:
    candidate_id: int
    topic_id: int
    payload: Row

    @property
    def key(self) -> str:
        return payload_key(self.payload) or ""


@dataclass(frozen=True)
class Rematch:
    """A candidate whose keyset Keebdex has now: its match_key is set, so it
    waits for staff as an edit of that page."""

    candidate_id: int
    topic_id: int
    key: str
    # what the candidate says that differs from the stored keyset
    difference: str | None = None


@dataclass
class PromotionPlan:
    promotions: list[Promotion] = field(default_factory=list)
    rematches: list[Rematch] = field(default_factory=list)
    # (topic id, what is missing): left for staff
    incomplete: list[tuple[int, str]] = field(default_factory=list)
    # left for the next run by the limit
    deferred: int = 0
    # posted before the cut-off (or undated): left for staff
    old: int = 0


def plan_promotions(
    candidates: Sequence[Row],
    keysets: Sequence[Row],
    *,
    limit: int,
    posted_since: date,
) -> PromotionPlan:
    """Which waiting keyset candidates magpie promotes itself, newest topic
    first: complete ones (missing_for_promotion), with no match_key and no
    keyset in Keebdex now, posted on or after `posted_since`, at most `limit`.

    A candidate whose keyset Keebdex has (its topic's URL or its key, another
    source's, an earlier topic's, or one this run inserts) gets that match_key
    instead; a different status is only reported, the keyset is never
    updated. Candidates of ignored or gone threads are left alone.
    """
    index = KeysetIndex(keysets)
    plan = PromotionPlan()
    planned: set[str] = set()
    deferred: set[str] = set()

    def topic_of(candidate: Row) -> int:
        return int(candidate["thread"]["external_id"])

    for candidate in sorted(candidates, key=topic_of, reverse=True):
        thread = candidate["thread"]
        if thread.get("ignored_at") or thread.get("gone_at"):
            continue
        topic_id = topic_of(candidate)
        payload = candidate["payload"]
        key = payload_key(payload)
        stored = index.match(topic_id, key)
        if stored is not None:
            if candidate.get("match_key") != stored["profile_keyset_id"]:
                plan.rematches.append(
                    Rematch(
                        candidate["id"],
                        topic_id,
                        stored["profile_keyset_id"],
                        _status_difference(stored, payload),
                    )
                )
            continue
        if candidate.get("match_key"):
            continue
        if missing := missing_for_promotion(payload):
            plan.incomplete.append((topic_id, ", ".join(missing)))
            continue
        posted = stored_time(thread.get("posted_at"))
        if posted is None or posted.date() < posted_since:
            plan.old += 1
            continue
        if key in planned:
            # an older topic of a keyset this run inserts
            plan.rematches.append(Rematch(candidate["id"], topic_id, key))
        elif key in deferred or len(plan.promotions) >= limit:
            deferred.add(key)
            plan.deferred += 1
        else:
            planned.add(key)
            plan.promotions.append(Promotion(candidate["id"], topic_id, payload))
    return plan


def _status_difference(stored: Row, payload: Row) -> str | None:
    suggested = (payload.get("keyset") or {}).get("status")
    if suggested is None or stored.get("status") == suggested:
        return None
    return f"status: Keebdex {stored.get('status')}, topic {suggested}"
