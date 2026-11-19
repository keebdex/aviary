"""The sync planner: the rules catalog-sync's updateMakerDatabase follows."""

from aviary.bowerbird.plan import is_field_changed, plan_maker_sync


def sculpt(sculpt_id, row_id=None, **fields):
    row = {"maker_id": "m", "sculpt_id": sculpt_id, "name": sculpt_id.title()}
    if row_id is not None:
        row |= {"id": row_id, "overridden_fields": [], "source": "scraper"}
    return row | fields


def colorway(sculpt_id, colorway_id, row_id=None, **fields):
    row = {
        "maker_id": "m",
        "sculpt_id": sculpt_id,
        "colorway_id": colorway_id,
        "name": colorway_id,
    }
    if row_id is not None:
        row |= {"id": row_id, "overridden_fields": [], "source": "scraper"}
    return row | fields


def table(sculpt_id, *colorways, **fields):
    return sculpt(sculpt_id, **fields) | {"colorways": list(colorways)}


def plan(tables, sculpts=(), colorways=(), **options):
    return plan_maker_sync(
        tables, list(sculpts), list(colorways), source="scraper", **options
    )


def test_empty_input_plans_nothing():
    result = plan([], [sculpt("a", 1)], [colorway("a", "x", 10)])
    assert not result.changes_rows


def test_new_rows_are_inserted_with_the_source_and_without_remote_img():
    result = plan([table("a", colorway("a", "x", img="cf", remote_img="src"))])
    assert result.sculpt_inserts == [
        sculpt("a") | {"source": "scraper", "overridden_fields": []}
    ]
    assert result.colorway_inserts == [
        colorway("a", "x", img="cf") | {"source": "scraper", "overridden_fields": []}
    ]


def test_updates_skip_overridden_and_absent_fields():
    stored = colorway(
        "a", "x", 10, name="Old", qty=5, release="2020", order=1, img="old"
    ) | {"overridden_fields": ["name", "order"]}
    incoming = colorway("a", "x", name="New", qty=None, order=2)
    result = plan([table("a", incoming)], [sculpt("a", 1)], [stored])
    # name is overridden, order always syncs, qty None clears, release and img
    # are absent from the input
    assert result.colorway_updates == [(10, {"qty": None, "order": 2})]


def test_stems_compare_by_content():
    assert not is_field_changed(["b", "a"], ["a", "b"])
    assert not is_field_changed(None, [])
    assert is_field_changed(["a"], None)


def test_sculpt_fields_sync_unless_overridden():
    stored = sculpt("a", 1, cast="Resin", design="Old") | {
        "overridden_fields": ["design"]
    }
    result = plan(
        [table("a", cast="Clay", design="New", source_document_id="url")], [stored]
    )
    assert result.sculpt_updates == [(1, {"cast": "Clay", "source_document_id": "url"})]


def test_name_fallback_takes_only_orphaned_rows_once():
    stored = [
        colorway("a", "old-1", 10, name="Red"),
        colorway("a", "old-2", 11, name="Red"),
        colorway("a", "kept", 12, name="Blue"),
    ]
    incoming = [
        colorway("a", "new-1", name="Red"),
        colorway("a", "new-2", name="Red"),
        colorway("a", "kept", name="Blue"),
        colorway("a", "new-3", name="Blue"),
    ]
    result = plan([table("a", *incoming)], [sculpt("a", 1)], stored)
    assert result.colorway_updates == [
        (10, {"colorway_id": "new-1"}),
        (11, {"colorway_id": "new-2"}),
    ]
    # "kept" matched by id, so "new-3" cannot steal it by name
    assert [c["colorway_id"] for c in result.colorway_inserts] == ["new-3"]
    assert result.removed_colorway_ids == []


def test_name_match_keeps_the_id_when_the_image_is_locked():
    stored = colorway("a", "old", 10, name="Red") | {"overridden_fields": ["img"]}
    result = plan(
        [table("a", colorway("a", "new", name="Red"))], [sculpt("a", 1)], [stored]
    )
    assert result.colorway_updates == []
    assert result.colorways[0]["img_overridden"] is True


def test_rows_gone_from_the_source_are_removed():
    result = plan(
        [table("a", colorway("a", "x"))],
        [sculpt("a", 1), sculpt("b", 2)],
        [colorway("a", "x", 10), colorway("a", "y", 11), colorway("b", "z", 20)],
    )
    assert result.removed_colorway_ids == [11, 20]
    assert [s["id"] for s in result.removed_sculpts] == [2]


def test_preserve_missing_keeps_rows_of_unsynced_documents():
    stored_sculpts = [
        sculpt("a", 1, source_document_id="doc-1"),
        sculpt("b", 2, source_document_id="doc-2"),
        sculpt("c", 3, source_document_id="doc-1"),
    ]
    stored_colorways = [
        colorway("a", "x", 10, order=0, source_document_id="doc-1"),
        colorway("a", "gone", 11, order=1, source_document_id="doc-1"),
        colorway("b", "y", 20, order=0, source_document_id="doc-2"),
        colorway("b", "old", 21, order=1, deleted=True, source_document_id="doc-2"),
    ]
    incoming = [
        colorway("a", "x", order=7, source_document_id="doc-1"),
        colorway("a", "new", order=8, source_document_id="doc-1"),
    ]
    result = plan(
        [table("a", *incoming, source_document_id="doc-1")],
        stored_sculpts,
        stored_colorways,
        preserve_missing=True,
        available_document_ids={"doc-1"},
    )
    # doc-2 did not sync: sculpt b and its live colorway stay; c and "gone"
    # belong to doc-1, which did
    assert [s["id"] for s in result.removed_sculpts] == [3]
    assert result.removed_colorway_ids == [11, 21]
    # x keeps its stored order; the new colorway goes after a's 2 stored ones,
    # plus its index in the input
    assert result.colorway_updates == []
    assert result.colorway_inserts[0]["order"] == 2 + 1


def test_incoming_rows_follow_a_sculpt_renamed_in_keebdex():
    stored = sculpt("new-name", 1, source_slug="old-name")
    result = plan(
        [table("old-name", colorway("old-name", "x"))],
        [stored],
        [colorway("new-name", "x", 10)],
    )
    assert not result.sculpt_inserts
    assert not result.removed_sculpts
    assert result.colorway_inserts == []
    assert result.removed_colorway_ids == []
