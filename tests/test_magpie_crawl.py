"""`magpie crawl` with the network and the database stubbed out."""

from datetime import UTC, datetime

import pytest
from magpie_fakes import PROFILES, keyset

from aviary.magpie import __main__ as magpie
from aviary.magpie import crawl as crawler
from aviary.magpie import ingest, keebdex
from aviary.magpie.fetch import FetchError
from aviary.magpie.parsers import BoardPage, KitImage, TopicDetail, TopicSummary
from aviary.magpie.plan import KeysetIndex

LAST_POST = "Thu, 05 November 2026, 10:00:00"


def summary(topic_id, last_post_at=LAST_POST):
    return TopicSummary(topic_id, f"[GB] GMK T{topic_id}", "a", 0, 0, last_post_at)


class Site:
    """A board of pages; a page that is an exception raises."""

    def __init__(self, pages):
        self.pages, self.read = pages, []

    def get(self, url):
        offset = int(url.rsplit(".", 1)[1])
        self.read.append(offset)
        page = self.pages[offset // 50]
        if isinstance(page, Exception):
            raise page
        return page

    def parse(self, topics, offset):
        has_next = offset // 50 + 1 < len(self.pages)
        return BoardPage(topics, offset + 50 if has_next else None)

    def close(self):
        pass


@pytest.fixture
def site(monkeypatch):
    def setup(pages):
        board = Site(pages)
        monkeypatch.setattr(crawler, "parse_board", board.parse)
        return board

    return setup


def test_list_board_stops_at_the_cap(site):
    board = site([[summary(1)], [summary(2)], [summary(3)]])
    listing = crawler.list_board(board, "gb", None, max_pages=2)
    assert board.read == [0, 50]
    assert not listing.done


def test_list_board_stops_at_the_watermark(site):
    board = site([[summary(1)], [summary(2)]])
    mark = datetime(2026, 11, 5, 10, tzinfo=UTC)
    assert crawler.list_board(board, "gb", mark, max_pages=5).done
    assert board.read == [0]


def test_an_empty_page_fails_the_listing(site):
    with pytest.raises(crawler.CrawlError):
        crawler.list_board(site([[summary(1)], []]), "gb", None, max_pages=5)


@pytest.fixture
def crawl_env(monkeypatch, site):
    """A crawl against a stubbed database; `written` holds what it wrote."""
    written = {"listing": [], "finish": []}

    def setup(pages, due=()):
        board = site(pages)
        monkeypatch.setattr(crawler, "create_supabase", lambda: None)
        monkeypatch.setattr(crawler, "Fetcher", lambda: board)
        monkeypatch.setattr(keebdex, "fetch_profiles", lambda c: PROFILES)
        monkeypatch.setattr(keebdex, "fetch_keysets", lambda c: [keyset()])
        monkeypatch.setattr(ingest, "last_watermarks", lambda c: {})
        monkeypatch.setattr(ingest, "start_run", lambda c, pages, now: 9)
        monkeypatch.setattr(ingest, "fetch_threads", lambda c: {})
        monkeypatch.setattr(
            ingest,
            "save_listing",
            lambda c, b, plan, dry: written["listing"].append((b, plan)),
        )
        monkeypatch.setattr(ingest, "due_threads", lambda c, limit, now: list(due))
        monkeypatch.setattr(
            ingest,
            "finish_run",
            lambda c, run, values: written["finish"].append(values),
        )
        return board

    return setup, written


def test_a_failed_board_writes_no_listing_and_fails_the_run(crawl_env):
    setup, written = crawl_env
    setup([[summary(1)], FetchError("HTTP 500", 500)])
    assert magpie.main(["crawl", "--board", "gb"]) == 1
    assert written["listing"] == []
    (finish,) = written["finish"]
    assert finish["status"] == "failed"
    # a failed run never moves the watermark
    assert finish["watermarks"] == {}


def test_a_complete_listing_moves_the_watermark(crawl_env):
    setup, written = crawl_env
    setup([[summary(1), summary(2, "Thu, 05 November 2026, 09:00:00")]])
    assert magpie.main(["crawl", "--board", "gb"]) == 0
    (finish,) = written["finish"]
    assert finish["status"] == "succeeded"
    assert finish["watermarks"] == {"gb": "2026-11-05T10:00:00+00:00"}
    assert (finish["threads_listed"], finish["threads_new"]) == (2, 2)
    assert finish["incomplete_boards"] == []


def test_another_running_run_stops_the_crawl(crawl_env, monkeypatch):
    setup, written = crawl_env
    setup([[summary(1)]])

    def running(c, pages, now):
        raise ingest.RunningError("crawl run 3 is still running")

    monkeypatch.setattr(ingest, "start_run", running)
    assert magpie.main(["crawl"]) == 1
    assert written["listing"] == []


class Fetches:
    def __init__(self, result):
        self.result = result

    def get(self, url):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def crawl(monkeypatch, result, writes):
    monkeypatch.setattr(crawler, "parse_topic", lambda html: html)
    monkeypatch.setattr(
        ingest,
        "update_thread",
        lambda c, t, values, dry: writes.append(("thread", values)),
    )
    monkeypatch.setattr(
        ingest,
        "save_candidate",
        lambda c, row, dry: writes.append(("candidate", row)) or ingest.ADDED,
    )
    return crawler.Crawl(
        None, Fetches(result), PROFILES, KeysetIndex([keyset()]), False
    )


THREAD = {
    "id": 3,
    "external_id": "100",
    "board": "gb",
    "title": "[GB] GMK CYL Selene",
    "content_hash": None,
    "fetch_attempts": 0,
}


def test_a_fetched_thread_stores_its_post_and_adds_a_candidate(monkeypatch):
    writes = []
    detail = TopicDetail(
        title="x",
        author="someone",
        posted_at="Sat, 14 November 2026, 23:03:52",
        body='<div class="post">hi</div>',
        kit_images=[KitImage("https://i.example/a.png")],
    )
    run = crawl(monkeypatch, detail, writes)
    run.fetch(THREAD)
    (_, thread), (_, candidate) = writes
    assert thread["raw_body"] == '<div class="post">hi</div>'
    assert thread["changed_at"] == thread["fetched_at"]
    assert (thread["name"], thread["profile_id"]) == ("Selene", "gmk")
    assert thread["images"] == ["https://i.example/a.png"]
    assert thread["posted_at"] == "2026-11-14T23:03:52+00:00"
    assert candidate["thread_id"] == 3
    assert candidate["content_hash"] == thread["content_hash"]
    assert candidate["match_key"] is None
    assert candidate["payload"]["keyset"]["slug"] == "selene"
    # the first fetch is not an edit
    assert run.counters.threads_changed == 0


def test_a_candidate_of_a_keyset_keebdex_has_gets_its_match_key(monkeypatch):
    writes = []
    detail = TopicDetail(
        posted_at="Sat, 14 November 2026, 23:03:52",
        body="b",
        kit_images=[KitImage("https://i.example/a.png")],
    )
    run = crawl(monkeypatch, detail, writes)
    run.fetch({**THREAD, "title": "[GB] GMK Old R2", "content_hash": "x"})
    run.fetch({**THREAD, "title": "[GB] GMK Old"})
    candidates = [row for kind, row in writes if kind == "candidate"]
    assert [c["match_key"] for c in candidates] == [None, "gmk/old"]
    assert run.counters.threads_changed == 1


def test_a_restored_candidate_is_counted_apart_from_added_ones(monkeypatch):
    detail = TopicDetail(
        posted_at="Sat, 14 November 2026, 23:03:52",
        body="b",
        kit_images=[KitImage("https://i.example/a.png")],
    )
    run = crawl(monkeypatch, detail, [])
    monkeypatch.setattr(ingest, "save_candidate", lambda c, row, dry: ingest.RESTORED)
    run.fetch(THREAD)
    assert (run.counters.candidates, run.counters.candidates_restored) == (0, 1)
    summary = crawler.crawl_summary(False, run.counters, [], None)
    assert "Added keyset candidates: 0" in summary
    assert "earlier version): 1" in summary
    # not a crawl_runs column
    assert "candidates_restored" not in run.counters.columns()


def test_the_summary_lists_profiles_keebdex_does_not_have(monkeypatch):
    detail = TopicDetail(
        posted_at="Sat, 14 November 2026, 23:03:52",
        body="b",
        kit_images=[KitImage("https://i.example/a.png")],
    )
    run = crawl(monkeypatch, detail, [])
    # Keebdex has only GMK here (PROFILES)
    for title in (
        "[GB] SWG Art",
        "[GB] SWG Bloom",
        "[IC] DSS Drank",
        "[GB] KBS Flat Out keycaps",
        "[GB] GMK Selene",
    ):
        run.fetch({**THREAD, "title": title})
    assert run.counters.unknown_profiles == {
        "SWG": ["[GB] SWG Art", "[GB] SWG Bloom"],
        "DSS": ["[IC] DSS Drank"],
    }
    assert run.counters.no_profile_code == 1
    summary = crawler.crawl_summary(False, run.counters, [], None)
    section = summary.split("### Unknown profiles")[1]
    assert section.splitlines()[2:] == [
        "- `SWG`: 2 threads: [GB] SWG Art; [GB] SWG Bloom",
        "- `DSS`: 1 thread: [IC] DSS Drank",
        "- No profile code in the title: 1 thread",
    ]
    # only reported, not crawl_runs columns
    assert "unknown_profiles" not in run.counters.columns()


def test_a_summary_without_unknown_profiles_has_no_such_section():
    summary = crawler.crawl_summary(False, crawler.Counters(), [], None)
    assert "Unknown profiles" not in summary


def test_a_404_marks_the_thread_gone(monkeypatch):
    writes = []
    crawl(monkeypatch, FetchError("HTTP 404", 404), writes).fetch(THREAD)
    ((_, values),) = writes
    assert values["gone_at"] is not None
    assert values["next_fetch_at"] is None


def test_other_failures_back_off(monkeypatch):
    writes = []
    run = crawl(monkeypatch, ValueError("no post found"), writes)
    run.fetch(THREAD)
    ((_, values),) = writes
    assert values["fetch_attempts"] == 1
    assert values["next_fetch_at"] is not None
    assert values["fetch_error"] == "no post found"
    assert run.counters.threads_failed == 1
