"""Removing colorways and sculpts against an in-memory stand-in for Supabase."""

import pytest

from aviary.bowerbird.apply import apply_plan
from aviary.bowerbird.plan import plan_maker_sync


class Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op = db, table, [], "select"

    def select(self, *_):
        return self

    def in_(self, column, values):
        self.filters.append(lambda row: row.get(column) in values)
        return self

    def update(self, values):
        self.op, self.values = "update", values
        return self

    def delete(self):
        self.op = "delete"
        return self

    def insert(self, rows):
        self.op, self.rows = "insert", rows
        return self

    def execute(self):
        rows = [r for r in self.db[self.table] if all(f(r) for f in self.filters)]
        if self.op == "update":
            for row in rows:
                row.update(self.values)
        elif self.op == "delete":
            self.db[self.table] = [r for r in self.db[self.table] if r not in rows]
        elif self.op == "insert":
            self.db[self.table] += self.rows
        return type("Response", (), {"data": [dict(r) for r in rows]})


class FakeClient:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return Query(self.db, name)


def row(table_id, sculpt_id, source="scraper", **fields):
    return {
        "id": table_id,
        "maker_id": "m",
        "sculpt_id": sculpt_id,
        "maker_sculpt_id": f"m/{sculpt_id}",
        "source": source,
        "overridden_fields": [],
        "deleted": False,
    } | fields


@pytest.fixture
def db():
    return {
        "artisan_sculpts": [row(i, s) for i, s in enumerate("abcd", start=1)],
        "artisan_colorways": [
            row(11, "a", colorway_id="a1"),
            row(21, "b", colorway_id="b1"),
            row(22, "b", source="keebdex", colorway_id="b2"),
            row(31, "c", colorway_id="c1"),
            row(41, "d", colorway_id="d1"),
        ],
        "user_collection_items": [{"artisan_item_id": 31}],
    }


def run(db, dry_run=False):
    own = [r for r in db["artisan_colorways"] if r["source"] == "scraper"]
    plan = plan_maker_sync(
        [{"maker_id": "m", "sculpt_id": "a", "colorways": [own[0] | {}]}],
        [dict(r) for r in db["artisan_sculpts"]],
        [dict(r) for r in own],
        source="scraper",
    )
    apply_plan(FakeClient(db), plan, dry_run=dry_run)


def state(db, table):
    return {r["id"]: r["deleted"] for r in db[table]}


def test_removed_sculpts_are_deleted_soft_deleted_or_kept(db):
    run(db)
    # b keeps b2 from the app; c keeps c1 for a collection; d has nothing left
    assert state(db, "artisan_sculpts") == {1: False, 2: False, 3: True}
    assert state(db, "artisan_colorways") == {11: False, 22: False, 31: True}


def test_dry_run_writes_nothing(db):
    before = {table: [dict(r) for r in rows] for table, rows in db.items()}
    run(db, dry_run=True)
    assert db == before
