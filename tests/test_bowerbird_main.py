import httpx

from aviary.bowerbird import __main__ as bowerbird


def test_main_closes_a_client_start_already_used(monkeypatch):
    """start() lists Cloudflare images before main() takes over the client,
    so main() must not enter it as a context manager (httpx refuses)."""
    http = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    http.get("https://example.com")
    run = bowerbird.Run(
        client=None, http=http, base_url="", existing_ids=set(), dry_run=True
    )
    monkeypatch.setattr(bowerbird, "start", lambda source, dry_run: run)
    monkeypatch.setattr(
        bowerbird, "sync_websites", lambda run, makers: bowerbird.Outcome()
    )

    assert bowerbird.main(["scrape", "--dry-run"]) == 0
    assert http.is_closed


def test_outcome_counts_only_makers_with_changes():
    outcome = bowerbird.Outcome()
    outcome.record("quiet", {"colorways_new": 0, "images": 0})
    outcome.record("busy", {"colorways_updated": 2, "images": 0})
    assert outcome.unchanged == 1
    assert list(outcome.changed) == ["busy"]


def test_summary_lists_changes_totals_and_failures():
    outcome = bowerbird.Outcome(
        changed={
            "b": {"colorways_new": 1, "images": 1},
            "a": {"colorways_updated": 2},
        },
        unchanged=5,
        failed=["broken"],
        disabled=["gone"],
    )
    text = bowerbird.summary("gdoc", outcome, dry_run=True)
    lines = text.splitlines()
    assert lines[0] == "## bowerbird gdoc (dry run: nothing written)"
    rows = [line for line in lines if line.startswith("| `")]
    assert rows == [
        "| `a` | 0 | 0 | 0 | 0 | 2 | 0 | 0 |",
        "| `b` | 0 | 0 | 0 | 1 | 0 | 0 | 1 |",
    ]
    assert (
        "| **Total** | **0** | **0** | **0** | **1** | **2** | **0** | **1** |" in lines
    )
    assert "Makers with no changes: 5" in lines
    assert "Failed and skipped: `broken`" in text
    assert "Disabled (every doc gone): `gone`" in text


def test_summary_without_changes_has_no_table():
    text = bowerbird.summary("scrape", bowerbird.Outcome(unchanged=2), dry_run=False)
    assert text == "## bowerbird scrape\n\nMakers with no changes: 2"
