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
    monkeypatch.setattr(bowerbird, "sync_websites", lambda run, makers: [])

    assert bowerbird.main(["scrape", "--dry-run"]) == 0
    assert http.is_closed
