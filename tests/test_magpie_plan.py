"""Listings, fetch schedules, and promotions, decided without a database."""

from datetime import UTC, date, datetime, timedelta

from aviary.magpie.parsers import BoardPage, TopicSummary
from aviary.magpie.plan import (
    MAX_FETCH_ATTEMPTS,
    BoardListing,
    Rematch,
    plan_listing,
    plan_promotions,
    refetch_at,
    retry_at,
)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def at(day, hour=0):
    return f"Thu, {day:02d} November 2026, {hour:02d}:00:00"


def summary(topic_id, last_post_at="Thu, 10 November 2026, 00:00:00", replies=3, **kw):
    values = {"title": f"[GB] GMK T{topic_id}", "author": "a", "views": 0}
    return TopicSummary(
        topic_id, replies=replies, last_post_at=last_post_at, **(values | kw)
    )


def page(*topics, last=False):
    return BoardPage(list(topics), None if last else 50)


def watermark(day):
    return datetime(2026, 11, day, tzinfo=UTC)


# listing


def test_a_first_listing_stopped_by_the_cap_still_sets_a_watermark():
    listing = BoardListing("gb", None)
    listing.add_page(page(summary(1, at(12)), summary(2, at(11))))
    # stopped by the page cap: incomplete, but the next run stops at its
    # newest thread instead of listing the whole cap again
    assert not listing.done
    assert listing.next_watermark() == watermark(12)


def test_a_listing_without_dated_threads_keeps_no_watermark():
    listing = BoardListing("gb", None)
    listing.add_page(page(summary(1, "")))
    assert listing.next_watermark() is None


def test_a_listing_that_reaches_the_end_sets_the_watermark():
    listing = BoardListing("gb", None)
    listing.add_page(page(summary(1, at(12)), summary(2, at(11)), last=True))
    assert listing.done
    assert listing.next_watermark() == watermark(12)


def test_a_listing_stops_at_the_previous_watermark():
    listing = BoardListing("gb", watermark(11))
    listing.add_page(page(summary(1, at(13)), summary(2, at(12))))
    assert not listing.done
    listing.add_page(page(summary(3, at(11)), summary(4, at(9))))
    assert listing.done
    assert listing.next_watermark() == watermark(13)


def test_an_incomplete_listing_keeps_the_old_watermark():
    listing = BoardListing("gb", watermark(5))
    listing.add_page(page(summary(1, at(13))))
    assert listing.next_watermark() == watermark(5)


def test_stickies_and_bumped_threads_count_once():
    listing = BoardListing("gb", None)
    listing.add_page(page(summary(1, at(1), is_sticky=True), summary(2, at(12))))
    listing.add_page(page(summary(2, at(13)), summary(3, at(11))))
    assert list(listing.seen) == [2, 3]
    assert listing.seen[2].last_post_at == at(12)
    # a sticky's old last post never stops a listing
    assert not listing.reached_watermark


def stored(last_post_at, gone_at=None):
    return {"last_post_at": last_post_at, "gone_at": gone_at}


def test_new_threads_and_new_activity_are_due():
    listing = BoardListing("gb", None)
    listing.add_page(page(summary(1, at(12)), summary(2, at(12)), summary(3, at(12))))
    rows = {
        "2": stored("2026-11-12T00:00:00+00:00"),
        "3": stored("2026-11-11T00:00:00+00:00"),
    }
    plan = plan_listing(listing, rows, NOW)
    assert plan.new == 1
    assert [r["external_id"] for r in plan.due] == ["1", "3"]
    assert [r["external_id"] for r in plan.seen] == ["2"]
    due = plan.due[0]
    assert due["next_fetch_at"] == NOW.isoformat()
    assert due["url"] == "https://geekhack.org/index.php?topic=1.0"
    assert due["last_post_at"] == "2026-11-12T00:00:00+00:00"
    # a row that only refreshes the listing never moves the fetch queue
    assert "next_fetch_at" not in plan.seen[0]


def test_a_gone_thread_is_fetched_again_only_after_new_activity():
    gone = stored("2026-11-12T00:00:00+00:00", gone_at="2026-11-13T00:00:00+00:00")
    listing = BoardListing("gb", None)
    listing.add_page(page(summary(1, at(12))))
    assert plan_listing(listing, {"1": gone}, NOW).due == []
    listing = BoardListing("gb", None)
    listing.add_page(page(summary(1, at(14))))
    assert plan_listing(listing, {"1": gone}, NOW).due[0]["gone_at"] is None


def test_new_activity_restarts_the_retries_of_a_thread_that_gave_up():
    # whatever its stored fetch_attempts, the due row resets them
    listing = BoardListing("gb", None)
    listing.add_page(page(summary(1, at(14))))
    (due,) = plan_listing(listing, {"1": stored("2026-11-12T00:00:00+00:00")}, NOW).due
    assert (due["fetch_attempts"], due["fetch_error"]) == (0, None)


# fetch schedule


def test_recent_threads_are_fetched_again_weekly():
    assert refetch_at(NOW - timedelta(days=10), NOW) == NOW + timedelta(days=7)
    assert refetch_at(NOW - timedelta(days=200), NOW) is None
    assert refetch_at(None, NOW) is None


def test_failed_fetches_back_off_then_stop():
    assert retry_at(1, NOW) == NOW + timedelta(hours=1)
    assert retry_at(3, NOW) == NOW + timedelta(hours=4)
    assert retry_at(MAX_FETCH_ATTEMPTS - 1, NOW) <= NOW + timedelta(days=7)
    assert retry_at(MAX_FETCH_ATTEMPTS, NOW) is None


# promotion

SINCE = date(2026, 1, 1)


def candidate(candidate_id, topic_id, slug="selene", match_key=None, **thread):
    payload = {
        "keyset": {
            "profile_id": "gmk",
            "name": slug.title(),
            "slug": slug,
            "img": "https://i.example/a.png",
            "status": "Confirmed",
        },
        "kits": [{"kit_id": "base", "name": "Base", "img": "https://i.example/a.png"}],
    }
    return {
        "id": candidate_id,
        "payload": payload,
        "match_key": match_key,
        "thread": {
            "external_id": str(topic_id),
            "posted_at": "2026-11-14T00:00:00+00:00",
        }
        | thread,
    }


def keyset(keyset_id, key, url=None, status="Confirmed"):
    profile_id, slug = key.split("/")
    return {
        "id": keyset_id,
        "profile_id": profile_id,
        "slug": slug,
        "profile_keyset_id": key,
        "url": url,
        "status": status,
    }


def promote(candidates, keysets=(), limit=10):
    return plan_promotions(candidates, list(keysets), limit=limit, posted_since=SINCE)


def test_a_complete_new_candidate_is_promoted():
    plan = promote([candidate(1, 100)])
    assert [(p.candidate_id, p.key) for p in plan.promotions] == [(1, "gmk/selene")]


def test_a_candidate_keebdex_has_now_is_matched_not_promoted():
    by_url = keyset(7, "gmk/other", "https://geekhack.org/index.php?topic=100")
    by_key = keyset(8, "gmk/b", status="Interest Check")
    plan = promote([candidate(1, 100), candidate(2, 200, "b")], [by_url, by_key])
    assert plan.promotions == []
    assert plan.rematches == [
        Rematch(2, 200, "gmk/b", "status: Keebdex Interest Check, topic Confirmed"),
        Rematch(1, 100, "gmk/other"),
    ]


def test_matched_candidates_wait_for_staff():
    already = keyset(7, "gmk/selene")
    plan = promote([candidate(1, 100, match_key="gmk/selene")], [already])
    assert (plan.promotions, plan.rematches) == ([], [])
    # a match_key whose keyset is gone (renamed, deleted) is staff's too
    assert promote([candidate(1, 100, match_key="gmk/x")]).promotions == []


def test_the_newest_topic_is_promoted_and_older_ones_matched_to_it():
    plan = promote([candidate(1, 100), candidate(2, 200)])
    assert [p.topic_id for p in plan.promotions] == [200]
    assert plan.rematches == [Rematch(1, 100, "gmk/selene")]


def test_incomplete_old_ignored_and_gone_candidates_are_left_alone():
    incomplete = candidate(1, 100)
    incomplete["payload"]["keyset"]["profile_id"] = None
    old = candidate(2, 200, "b", posted_at="2019-03-04T00:00:00+00:00")
    undated = candidate(3, 300, "c", posted_at=None)
    ignored = candidate(4, 400, "d", ignored_at="2026-11-15T00:00:00+00:00")
    gone = candidate(5, 500, "e", gone_at="2026-11-15T00:00:00+00:00")
    plan = promote([incomplete, old, undated, ignored, gone])
    assert (plan.promotions, plan.rematches) == ([], [])
    assert plan.incomplete == [(100, "profile_id")]
    assert plan.old == 2


def test_the_limit_defers_the_rest_with_their_older_topics():
    candidates = [
        candidate(1, 300, "a"),
        candidate(2, 200, "b"),
        candidate(3, 100, "b"),
    ]
    plan = promote(candidates, limit=1)
    assert [p.key for p in plan.promotions] == ["gmk/a"]
    assert plan.rematches == []
    assert plan.deferred == 2
