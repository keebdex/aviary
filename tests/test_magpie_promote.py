"""`magpie promote` against an in-memory Supabase."""

from magpie_fakes import FakeClient

from aviary.magpie import keebdex, promote
from aviary.magpie.keebdex import PromotionResult
from aviary.magpie.plan import Promotion, PromotionPlan, Rematch
from aviary.magpie.proposal import kit_rows


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
    assert keebdex.apply_promotions(client, plan, dry_run=False) == PromotionResult()
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
    result = keebdex.apply_promotions(client, plan, dry_run=False)
    assert result == PromotionResult(failed=[200])
    assert client.rows["keysets"] == []
    assert client.rows["candidates"] == waiting(1, 2)


def test_a_candidate_triaged_meanwhile_rolls_its_keyset_back():
    # staff dismissed candidate 1 after fetch_waiting read it
    client = FakeClient([{"id": 1, "state": "dismissed"}, *waiting(2)])
    plan = PromotionPlan(
        promotions=[Promotion(1, 200, payload("a"))],
        rematches=[Rematch(2, 100, "gmk/a")],
    )
    result = keebdex.apply_promotions(client, plan, dry_run=False)
    assert result == PromotionResult(triaged=[200])
    assert client.rows["keysets"] == []
    assert client.rows["keyset_kits"] == []
    assert client.rows["candidates"] == [{"id": 1, "state": "dismissed"}, *waiting(2)]


def test_a_triaged_candidate_whose_keyset_stays_is_only_failed():
    client = FakeClient(
        [{"id": 1, "state": "dismissed"}], failing={("keysets", "delete")}
    )
    plan = PromotionPlan(promotions=[Promotion(1, 200, payload("a"))])
    result = keebdex.apply_promotions(client, plan, dry_run=False)
    # the keyset is still in Keebdex: not "removed again"
    assert result == PromotionResult(failed=[200])
    assert [k["slug"] for k in client.rows["keysets"]] == ["a"]


def test_a_failed_rollback_is_reported_and_the_run_goes_on():
    client = FakeClient(
        waiting(1, 2), failing={("keyset_kits", "insert"), ("keysets", "delete")}
    )
    plan = PromotionPlan(
        promotions=[Promotion(1, 200, payload("a")), Promotion(2, 300, payload("b"))]
    )
    result = keebdex.apply_promotions(client, plan, dry_run=False)
    assert result == PromotionResult(failed=[200, 300])
    # both promotions were tried; their keysets are left for staff to remove
    assert [k["slug"] for k in client.rows["keysets"]] == ["a", "b"]
    assert client.rows["candidates"] == waiting(1, 2)


def test_a_claim_whose_response_was_lost_keeps_itskeyset():
    # the database promoted the candidate, but the response never came back
    client = FakeClient(waiting(1), lost={("candidates", "update")})
    plan = PromotionPlan(promotions=[Promotion(1, 200, payload("a"))])
    assert keebdex.apply_promotions(client, plan, dry_run=False) == PromotionResult()
    (keyset,) = client.rows["keysets"]
    (candidate,) = client.rows["candidates"]
    assert (candidate["state"], candidate["promoted_id"]) == ("promoted", keyset["id"])
    assert len(client.rows["keyset_kits"]) == 1


def test_a_claim_that_did_not_apply_rolls_back_and_the_candidate_waits():
    client = FakeClient(waiting(1), failing={("candidates", "update")})
    plan = PromotionPlan(promotions=[Promotion(1, 200, payload("a"))])
    result = keebdex.apply_promotions(client, plan, dry_run=False)
    assert result == PromotionResult(failed=[200])
    assert client.rows["keysets"] == []
    assert client.rows["keyset_kits"] == []
    assert client.rows["candidates"] == waiting(1)


def test_a_claim_that_cannot_be_read_back_keeps_itskeyset():
    client = FakeClient(
        waiting(1), failing={("candidates", "update"), ("candidates", "select")}
    )
    plan = PromotionPlan(promotions=[Promotion(1, 200, payload("a"))])
    result = keebdex.apply_promotions(client, plan, dry_run=False)
    assert result == PromotionResult(failed=[200])
    # never delete what a candidate may point at
    assert [k["slug"] for k in client.rows["keysets"]] == ["a"]


def test_the_promote_summary_lists_triaged_topics_apart_from_inserted():
    plan = PromotionPlan(
        promotions=[Promotion(1, 200, payload("a")), Promotion(2, 300, payload("b"))]
    )
    summary = promote.promote_summary(plan, PromotionResult(triaged=[300]), False)
    assert "- Inserted (Pending): 1" in summary
    assert "- Triaged by staff meanwhile (keyset removed again): 1" in summary
    assert "topic=300" in summary.split("### Triaged by staff meanwhile")[1]
    assert "`gmk/b`" not in summary


def test_a_dry_run_writes_nothing():
    client = FakeClient(waiting(1), failing={"keysets", "keyset_kits", "candidates"})
    plan = PromotionPlan(promotions=[Promotion(1, 200, payload("a"))])
    assert keebdex.apply_promotions(client, plan, dry_run=True) == PromotionResult()


def test_kit_rows_follow_the_keyset_key():
    assert [k["profile_keyset_id"] for k in kit_rows(payload("a"))] == ["gmk/a"]
