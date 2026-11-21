"""magpie's commands with the network and the database stubbed out."""

from datetime import UTC, datetime

import pytest
from postgrest import APIError

from aviary.magpie import __main__ as magpie
from aviary.magpie import apply
from aviary.magpie.fetch import FetchError
from aviary.magpie.parsers import BoardPage, KitImage, TopicDetail, TopicSummary
from aviary.magpie.plan import KeysetIndex, Promotion, PromotionPlan, Rematch
from aviary.magpie.proposal import kit_rows

PROFILES = [{"profile_id": "gmk", "name": "CYL", "profile": "Cherry"}]
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
        monkeypatch.setattr(magpie, "parse_board", board.parse)
        return board

    return setup


def test_list_board_stops_at_the_cap(site):
    board = site([[summary(1)], [summary(2)], [summary(3)]])
    listing = magpie.list_board(board, "gb", None, max_pages=2)
    assert board.read == [0, 50]
    assert not listing.done


def test_list_board_stops_at_the_watermark(site):
    board = site([[summary(1)], [summary(2)]])
    mark = datetime(2026, 11, 5, 10, tzinfo=UTC)
    assert magpie.list_board(board, "gb", mark, max_pages=5).done
    assert board.read == [0]


def test_an_empty_page_fails_the_listing(site):
    with pytest.raises(magpie.CrawlError):
        magpie.list_board(site([[summary(1)], []]), "gb", None, max_pages=5)


def _keyset():
    return {
        "id": 1,
        "profile_id": "gmk",
        "slug": "old",
        "profile_keyset_id": "gmk/old",
        "url": None,
        "status": "Confirmed",
    }


@pytest.fixture
def crawl_env(monkeypatch, site):
    """A crawl against a stubbed database; `written` holds what it wrote."""
    written = {"listing": [], "finish": []}

    def setup(pages, due=()):
        board = site(pages)
        monkeypatch.setattr(magpie, "create_supabase", lambda: None)
        monkeypatch.setattr(magpie, "Fetcher", lambda: board)
        monkeypatch.setattr(apply, "fetch_profiles", lambda c: PROFILES)
        monkeypatch.setattr(apply, "fetch_keysets", lambda c: [_keyset()])
        monkeypatch.setattr(apply, "last_watermarks", lambda c: {})
        monkeypatch.setattr(apply, "start_run", lambda c, pages, now: 9)
        monkeypatch.setattr(apply, "fetch_threads", lambda c: {})
        monkeypatch.setattr(
            apply,
            "save_listing",
            lambda c, b, plan, dry: written["listing"].append((b, plan)),
        )
        monkeypatch.setattr(apply, "due_threads", lambda c, limit, now: list(due))
        monkeypatch.setattr(
            apply, "finish_run", lambda c, run, values: written["finish"].append(values)
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
        raise apply.RunningError("crawl run 3 is still running")

    monkeypatch.setattr(apply, "start_run", running)
    assert magpie.main(["crawl"]) == 1
    assert written["listing"] == []


class RacedRuns:
    """crawl_runs where nothing is running at the check, but another run
    inserts before this one does."""

    def table(self, name):
        return self

    def schema(self, name):
        return self

    def update(self, values):
        return self

    def select(self, columns):
        return self

    def eq(self, column, value):
        return self

    def lt(self, column, value):
        return self

    def insert(self, row):
        self.inserting = True
        return self

    def execute(self):
        if getattr(self, "inserting", False):
            raise APIError({"code": "23505", "message": "one_running_idx"})
        return type("Response", (), {"data": []})


def test_a_run_started_between_check_and_insert_is_a_running_error():
    with pytest.raises(apply.RunningError):
        apply.start_run(RacedRuns(), 5, datetime(2026, 11, 21, tzinfo=UTC))


class Fetches:
    def __init__(self, result):
        self.result = result

    def get(self, url):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def crawl(monkeypatch, result, writes):
    monkeypatch.setattr(magpie, "parse_topic", lambda html: html)
    monkeypatch.setattr(
        apply,
        "update_thread",
        lambda c, t, values, dry: writes.append(("thread", values)),
    )
    monkeypatch.setattr(
        apply,
        "save_candidate",
        lambda c, row, dry: writes.append(("candidate", row)) or True,
    )
    return magpie.Crawl(
        None, Fetches(result), PROFILES, KeysetIndex([_keyset()]), False
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


# promotion against an in-memory Supabase


class Table:
    def __init__(self, db, name):
        self.db, self.name, self.op, self.filters = db, name, None, []

    def insert(self, rows):
        self.op, self.rows = "insert", rows if isinstance(rows, list) else [rows]
        return self

    def update(self, values):
        self.op, self.values = "update", values
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def execute(self):
        if {self.name, (self.name, self.op)} & self.db.failing:
            raise RuntimeError(f"{self.name} {self.op} refused")
        table = self.db.rows[self.name]
        hit = [r for r in table if all(r.get(c) == v for c, v in self.filters)]
        if self.op == "delete":
            self.db.rows[self.name] = [r for r in table if r not in hit]
        elif self.op == "update":
            for row in hit:
                row.update(self.values)
        else:
            hit = [{"id": len(table) + 1, **r} for r in self.rows]
            table += hit
        return type("Response", (), {"data": hit})


class FakeClient:
    def __init__(self, candidates, failing=()):
        self.failing = set(failing)
        self.rows = {"keysets": [], "keyset_kits": [], "candidates": candidates}

    def table(self, name):
        return Table(self, name)

    def schema(self, name):
        assert name == "ingest"
        return self


def payload(slug):
    return {
        "keyset": {"profile_id": "gmk", "name": slug, "slug": slug, "img": "i"},
        "kits": [{"kit_id": "base", "name": "Base", "img": "i"}],
    }


def waiting(*ids):
    return [{"id": i, "state": "new", "match_key": None} for i in ids]


def test_promotion_inserts_and_marks_the_candidate():
    client = FakeClient(waiting(1, 2))
    plan = PromotionPlan(
        promotions=[Promotion(1, 200, payload("a"))],
        rematches=[Rematch(2, 100, "gmk/a")],
    )
    assert apply.apply_promotions(client, plan, dry_run=False) == []
    (keyset,) = client.rows["keysets"]
    assert (keyset["profile_keyset_id"], keyset["review_status"]) == (
        "gmk/a",
        "Pending",
    )
    assert client.rows["keyset_kits"][0]["profile_keyset_id"] == "gmk/a"
    first, second = client.rows["candidates"]
    assert (first["state"], first["promoted_kind"], first["promoted_id"]) == (
        "promoted",
        "keyset",
        1,
    )
    assert (second["state"], second["match_key"]) == ("new", "gmk/a")


def test_a_keyset_whose_kits_fail_is_removed_and_its_candidates_wait():
    client = FakeClient(waiting(1, 2), failing={("keyset_kits", "insert")})
    plan = PromotionPlan(
        promotions=[Promotion(1, 200, payload("a"))],
        rematches=[Rematch(2, 100, "gmk/a")],
    )
    assert apply.apply_promotions(client, plan, dry_run=False) == [200]
    assert client.rows["keysets"] == []
    assert client.rows["candidates"] == waiting(1, 2)


def test_a_candidate_triaged_meanwhile_rolls_its_keyset_back():
    # staff dismissed candidate 1 after fetch_waiting read it
    client = FakeClient([{"id": 1, "state": "dismissed"}, *waiting(2)])
    plan = PromotionPlan(
        promotions=[Promotion(1, 200, payload("a"))],
        rematches=[Rematch(2, 100, "gmk/a")],
    )
    assert apply.apply_promotions(client, plan, dry_run=False) == []
    assert client.rows["keysets"] == []
    assert client.rows["keyset_kits"] == []
    assert client.rows["candidates"] == [{"id": 1, "state": "dismissed"}, *waiting(2)]


def test_a_failed_rollback_is_reported_and_the_run_goes_on():
    client = FakeClient(
        waiting(1, 2), failing={("keyset_kits", "insert"), ("keysets", "delete")}
    )
    plan = PromotionPlan(
        promotions=[Promotion(1, 200, payload("a")), Promotion(2, 300, payload("b"))]
    )
    assert apply.apply_promotions(client, plan, dry_run=False) == [200, 300]
    # both promotions were tried; their keysets are left for staff to remove
    assert [k["slug"] for k in client.rows["keysets"]] == ["a", "b"]
    assert client.rows["candidates"] == waiting(1, 2)


def test_a_dry_run_writes_nothing():
    client = FakeClient(waiting(1), failing={"keysets", "keyset_kits", "candidates"})
    plan = PromotionPlan(promotions=[Promotion(1, 200, payload("a"))])
    assert apply.apply_promotions(client, plan, dry_run=True) == []


def test_kit_rows_follow_the_keyset_key():
    assert [k["profile_keyset_id"] for k in kit_rows(payload("a"))] == ["gmk/a"]
