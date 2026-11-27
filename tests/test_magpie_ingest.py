"""magpie's `ingest` writes against stand-ins for Supabase."""

from datetime import UTC, datetime

import pytest
from magpie_fakes import FakeClient
from postgrest import APIError

from aviary.magpie import ingest


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
    with pytest.raises(ingest.RunningError):
        ingest.start_run(RacedRuns(), 5, datetime(2026, 11, 21, tzinfo=UTC))


# candidates and the supersede trigger


def candidate_row(**values):
    return {
        "thread_id": 3,
        "module": "Keyset",
        "extractor_version": "magpie-1",
        "content_hash": "h",
        **values,
    }


def test_a_new_extraction_is_added():
    client = FakeClient([])
    assert ingest.save_candidate(client, candidate_row(), False) == ingest.ADDED


def test_a_superseded_extraction_brought_back_by_a_revert_is_restored():
    client = FakeClient([candidate_row(state="superseded")], dropped={"candidates"})
    assert ingest.save_candidate(client, candidate_row(), False) == ingest.RESTORED


@pytest.mark.parametrize("state", ["new", "dismissed", "promoted"])
def test_an_extraction_already_made_is_not_counted(state):
    client = FakeClient([candidate_row(state=state)], dropped={"candidates"})
    assert ingest.save_candidate(client, candidate_row(), False) is None
