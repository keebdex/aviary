"""Stand-ins for Keebdex and the `ingest` schema shared by magpie's tests."""

PROFILES = [{"profile_id": "gmk", "name": "CYL", "profile": "Cherry"}]


def keyset():
    return {
        "id": 1,
        "profile_id": "gmk",
        "slug": "old",
        "profile_keyset_id": "gmk/old",
        "url": None,
        "status": "Confirmed",
    }


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

    def select(self, columns):
        self.op = "select"
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
        elif self.op == "insert":
            if self.name in self.db.dropped:
                # a before-insert trigger returned null
                hit = []
            elif self.name == "candidates":
                hit = [self._supersede(table, row) for row in self.rows]
                hit = [row for row in hit if row is not None]
            else:
                hit = [{"id": len(table) + 1, **r} for r in self.rows]
                table += hit
        if (self.name, self.op) in self.db.lost:
            raise RuntimeError(f"{self.name} {self.op}: response lost")
        return type("Response", (), {"data": hit})

    @staticmethod
    def _supersede(table, row):
        """candidates_supersede: an extraction already made is not inserted
        again (a superseded one waits again); a new one supersedes the
        thread's waiting candidate."""
        key = ("thread_id", "module", "extractor_version", "content_hash")
        same = [r for r in table if all(r.get(k) == row.get(k) for k in key)]
        if same:
            if same[0].get("state") == "superseded":
                same[0]["state"] = "new"
            return None
        for other in table:
            if (
                other.get("thread_id") == row["thread_id"]
                and other.get("state") == "new"
            ):
                other["state"] = "superseded"
        inserted = {"id": len(table) + 1, "state": "new", **row}
        table.append(inserted)
        return inserted


class FakeClient:
    def __init__(self, candidates, failing=(), lost=(), dropped=()):
        # failing: refused before applying; lost: applied, then the response
        # never came back; dropped: inserts a trigger turned away
        self.failing, self.lost, self.dropped = set(failing), set(lost), set(dropped)
        self.rows = {"keysets": [], "keyset_kits": [], "candidates": candidates}

    def table(self, name):
        return Table(self, name)

    def schema(self, name):
        assert name == "ingest"
        return self
