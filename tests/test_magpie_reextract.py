"""`magpie reextract` against an in-memory candidates table."""

import pytest
from magpie_fakes import PROFILES, FakeClient, keyset

from aviary.magpie import __main__ as magpie
from aviary.magpie import ingest, keebdex, reextract
from aviary.magpie.plan import KeysetIndex
from aviary.magpie.proposal import EXTRACTOR_VERSION

STORED_POST = (
    '<div class="post"><div class="inner">'
    '<img src="https://i.example/hero.png"></div></div>'
)


def stored_thread(thread_id, title="[GB] GMK CYL Selene", **values):
    return {
        "id": thread_id,
        "external_id": str(100 + thread_id),
        "board": "gb",
        "title": title,
        "author": "someone",
        "posted_at": "2026-11-14T23:03:52+00:00",
        "content_hash": f"hash{thread_id}",
        **values,
    }


@pytest.fixture
def reextract_env(monkeypatch):
    """`magpie reextract` against an in-memory candidates table; `bodies`
    are the stored first posts by thread id."""

    def setup(threads, bodies, running=None):
        client = FakeClient([])
        monkeypatch.setattr(reextract, "create_supabase", lambda: client)
        monkeypatch.setattr(keebdex, "fetch_profiles", lambda c: PROFILES)
        monkeypatch.setattr(keebdex, "fetch_keysets", lambda c: [keyset()])
        monkeypatch.setattr(ingest, "running_run", lambda c, now: running)
        monkeypatch.setattr(ingest, "fetch_stored_threads", lambda c: threads)
        monkeypatch.setattr(
            ingest, "stored_bodies", lambda c, ids: {i: bodies[i] for i in ids}
        )
        return client

    return setup


def test_reextract_adds_a_candidate_with_the_current_extractor(reextract_env):
    client = reextract_env(
        [stored_thread(1), stored_thread(2, "[GB] Rules of the board")],
        {1: STORED_POST, 2: STORED_POST},
    )
    assert magpie.main(["reextract"]) == 0
    (candidate,) = client.rows["candidates"]
    assert candidate["thread_id"] == 1
    assert candidate["extractor_version"] == EXTRACTOR_VERSION
    assert candidate["content_hash"] == "hash1"
    assert candidate["payload"]["keyset"]["name"] == "Selene"


def test_reextract_run_again_adds_nothing(reextract_env):
    client = reextract_env([stored_thread(1)], {1: STORED_POST})
    assert magpie.main(["reextract"]) == 0
    assert magpie.main(["reextract"]) == 0
    assert len(client.rows["candidates"]) == 1


def test_reextract_counts_posts_it_cannot_parse():
    counters = reextract.ReextractCounters()
    reextract.reextract_thread(
        FakeClient([]),
        stored_thread(1),
        "<p>no post here</p>",
        PROFILES,
        KeysetIndex([]),
        counters,
        dry_run=False,
    )
    assert counters.parse_errors == [("101", "no post in the stored body")]
    summary = reextract.reextract_summary(False, counters)
    assert "Posts that could not be parsed: 1" in summary


def test_reextract_waits_for_a_running_crawl(reextract_env):
    client = reextract_env([stored_thread(1)], {1: STORED_POST}, running=7)
    assert magpie.main(["reextract"]) == 1
    assert client.rows["candidates"] == []


def test_a_reextract_dry_run_writes_nothing_and_tells_what_it_would_add(
    reextract_env,
):
    client = reextract_env([stored_thread(1)], {1: STORED_POST})
    counters = reextract.ReextractCounters()
    reextract.reextract_thread(
        client,
        stored_thread(1),
        STORED_POST,
        PROFILES,
        KeysetIndex([]),
        counters,
        dry_run=True,
    )
    assert client.rows["candidates"] == []
    assert counters.added == 1
