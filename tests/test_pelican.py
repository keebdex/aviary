"""pelican's jobs against in-memory stand-ins for Supabase, Cloudflare, and R2.

The image ids, URL parsing, and lock rules were also compared with
catalog-sync's JavaScript on random inputs (identical, except that a lock rule
with a null prefix now retains backups instead of being ignored).
"""

from datetime import UTC, datetime, timedelta

import pytest

from aviary.common import cf_images
from aviary.common.cf_images import SourceFile
from aviary.common.db import PUBLISHED_FILTER
from aviary.pelican import maintenance, sync
from aviary.pelican.references import IMAGE_COLUMNS

BASE = "https://imagedelivery.net/H"
OLD = "2020-01-01T00:00:00Z"
NEW = datetime.now(UTC).isoformat()


class Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.update_values = db, table, [], None
        self._negate = False

    def select(self, *_):
        return self

    def or_(self, condition):
        assert condition == PUBLISHED_FILTER
        return self._filter(lambda r: r.get("review_status") in (None, "Approved"))

    @property
    def not_(self):
        self._negate = True
        return self

    def is_(self, column, _value):
        negate, self._negate = self._negate, False
        return self._filter(lambda r: (r.get(column) is None) != negate)

    def like(self, column, pattern):
        negate, self._negate = self._negate, False
        prefix = pattern.removesuffix("*").replace("\\_", "_")
        return self._filter(lambda r: str(r.get(column)).startswith(prefix) != negate)

    def eq(self, column, value):
        return self._filter(lambda r: r.get(column) == value)

    def neq(self, column, value):
        return self._filter(lambda r: r.get(column) != value)

    def order(self, *_):
        return self

    def range(self, start, end):
        self.slice = (start, end + 1)
        return self

    def update(self, values):
        self.update_values = values
        return self

    def _filter(self, test):
        self.filters.append(test)
        return self

    def execute(self):
        rows = [
            r for r in self.db.get(self.table, []) if all(f(r) for f in self.filters)
        ]
        if self.update_values is not None:
            for row in rows:
                row.update(self.update_values)
            return type("R", (), {"data": rows})
        start, end = getattr(self, "slice", (0, None))
        return type("R", (), {"data": rows[start:end]})


class FakeClient:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return Query(self.db, name)


class FakeBucket:
    def __init__(self, objects=None):
        self.objects = objects or {}
        self.moved = []

    def get(self, key):
        return self.objects.get(key)

    def move(self, source, target):
        self.moved.append((source, target))


def cloudflare(monkeypatch, images):
    """Patch the Cloudflare client to a dict of id -> uploaded time."""
    calls = {"deleted": [], "uploaded": []}
    monkeypatch.setattr(
        cf_images,
        "list_images",
        lambda _: [cf_images.CloudflareImage(i, t) for i, t in images.items()],
    )
    monkeypatch.setattr(
        cf_images, "delete_image", lambda _, i: calls["deleted"].append(i) or True
    )
    monkeypatch.setattr(
        cf_images,
        "upload_file",
        lambda _, i, f: calls["uploaded"].append(i) or cf_images.UploadResult(True),
    )
    return calls


def keysets(*ids):
    return {
        "keysets": [{"id": n, "img": f"{BASE}/{i}/public"} for n, i in enumerate(ids)]
    }


def test_prune_deletes_old_unreferenced_images_only(monkeypatch):
    calls = cloudflare(monkeypatch, {"kept": OLD, "old": OLD, "recent": NEW})
    maintenance.prune(FakeClient(keysets("kept")), None, BASE, dry_run=False)
    assert calls["deleted"] == ["old"]


def test_prune_refuses_without_references_and_dry_run_deletes_nothing(monkeypatch):
    calls = cloudflare(monkeypatch, {"old": OLD})
    with pytest.raises(maintenance.Refused):
        maintenance.prune(FakeClient({}), None, BASE, dry_run=False)
    maintenance.prune(FakeClient(keysets("x")), None, BASE, dry_run=True)
    assert calls["deleted"] == []


def test_pending_suggestion_images_are_referenced(monkeypatch):
    calls = cloudflare(monkeypatch, {"suggested": OLD, "old": OLD})
    db = keysets("x") | {
        "edit_suggestions": [
            {
                "id": 1,
                "review_status": "Pending",
                "changes": {"img": f"{BASE}/suggested/public"},
            }
        ]
    }
    maintenance.prune(FakeClient(db), None, BASE, dry_run=False)
    assert calls["deleted"] == ["old"]


def test_restore_uploads_missing_images_from_their_backup(monkeypatch):
    calls = cloudflare(monkeypatch, {f"i{n}": OLD for n in range(20)})
    bucket = FakeBucket({"images/gone": SourceFile(b"x", "image/png")})
    db = keysets("gone", "no-backup", *[f"i{n}" for n in range(20)])
    failed = maintenance.restore(
        FakeClient(db), None, bucket, BASE, dry_run=False, force=False
    )
    assert (calls["uploaded"], failed) == (["gone"], 0)


def test_restore_refuses_when_too_many_are_missing(monkeypatch):
    cloudflare(monkeypatch, {"a": OLD})
    db = keysets("a", "b", "c")
    with pytest.raises(maintenance.Refused):
        maintenance.restore(
            FakeClient(db), None, FakeBucket(), BASE, dry_run=False, force=False
        )


def test_trash_respects_locks_and_the_ratio():
    now = datetime.now(UTC)
    images = [cf_images.CloudflareImage(f"i{n}", OLD) for n in range(30)]
    backed_up = {f"images/i{n}": now for n in range(30)} | {
        "images/gone-old": now - timedelta(days=60),
        "images/gone-new": now,
    }
    rules = [
        {
            "enabled": True,
            "prefix": "images/",
            "condition": {"type": "Age", "maxAgeSeconds": 86400 * 30},
        }
    ]
    bucket = FakeBucket()
    maintenance._trash_orphaned(
        bucket, images, backed_up, rules, force=False, dry_run=False
    )
    assert bucket.moved == [("images/gone-old", "trash/images/gone-old")]

    with pytest.raises(maintenance.Refused):
        maintenance._trash_orphaned(
            bucket, images[:2], backed_up, [], force=False, dry_run=False
        )


def test_sync_plans_published_external_images_only():
    db = {
        "keysets": [
            {
                "id": 1,
                "profile_keyset_id": "gmk/a",
                "img": "https://x/a.png",
                "order_graph": f"{BASE}/g/public",
                "order_history": None,
            },
            {
                "id": 2,
                "profile_keyset_id": "gmk/b",
                "img": "https://x/b.png",
                "review_status": "Pending",
            },
        ],
        "keyset_kits": [
            {
                "id": 10,
                "profile_keyset_id": "gmk/a",
                "kit_id": None,
                "name": "Base Kit",
                "img": "https://x/k.png",
            },
            {
                "id": 11,
                "profile_keyset_id": "gmk/b",
                "kit_id": "base",
                "name": "Base",
                "img": "https://x/k2.png",
            },
            {
                "id": 12,
                "profile_keyset_id": "gmk/a",
                "kit_id": "x",
                "name": "X",
                "img": "",
            },
        ],
    }
    keysets_, kits, variants, colorways = sync.plan_uploads(FakeClient(db), BASE)
    assert [u.image_id for u in keysets_] == [sync.keyset_image_id(db["keysets"][0])]
    assert [(u.image_id, u.cleared) for u in kits] == [
        (f"keyset/gmk/a/kit/10/base-kit/{sync.crc32_hex('https://x/k.png')}", "")
    ]
    assert variants == colorways == []


def test_sync_writes_only_columns_prune_knows():
    row = {
        "id": 1,
        "profile_keyset_id": "gmk/a",
        "brand_keyboard_slug": "b/k",
        "maker_sculpt_id": "m/s",
        "maker_id": "m",
        "sculpt_id": "s",
        "colorway_id": "c",
        "release_id": 1,
        "variant_name": "V",
        "kit_id": "k",
        "name": "K",
    } | {
        column: "https://x/y.png"
        for column in ("img", "order_graph", "order_history", "img_front", "img_back")
    }
    db = {
        table: [dict(row)]
        for table in (
            "keysets",
            "keyset_kits",
            "keyboards",
            "keyboard_variants",
            "artisan_sculpts",
            "artisan_colorways",
        )
    }
    uploads = [u for group in sync.plan_uploads(FakeClient(db), BASE) for u in group]
    assert uploads
    for upload in uploads:
        assert upload.column in IMAGE_COLUMNS[upload.table]
