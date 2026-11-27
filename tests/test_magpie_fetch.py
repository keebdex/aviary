"""The polite Geekhack fetcher against a fake site."""

import httpx
import pytest

from aviary.magpie.fetch import MAX_ATTEMPTS, Fetcher, FetchError

ROBOTS = "User-agent: *\nDisallow:\n"
TOPIC = "https://geekhack.org/index.php?topic=1.0"


def fetcher(handler, sleeps=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    sleep = sleeps.append if sleeps is not None else lambda _: None
    return Fetcher(client=client, delay=0, backoff=1, sleep=sleep)


def site(responses):
    """Robots allow everything; topic requests get `responses` in turn."""
    calls = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        calls.append(request)
        return responses[len(calls) - 1]

    return handler, calls


def test_pages_without_a_charset_are_read_as_cp1252():
    handler, _ = site([httpx.Response(200, content=b"caf\xe9 \x93")])
    assert fetcher(handler).get(TOPIC) == "caf\xe9 \u201c"


def test_server_errors_are_retried_with_backoff():
    sleeps = []
    handler, calls = site(
        [httpx.Response(503), httpx.Response(429), httpx.Response(200)]
    )
    assert fetcher(handler, sleeps).get(TOPIC) == ""
    assert len(calls) == 3
    assert sleeps == [1, 2]


def test_retries_give_up_after_max_attempts():
    handler, calls = site([httpx.Response(500)] * MAX_ATTEMPTS)
    with pytest.raises(FetchError, match="500"):
        fetcher(handler).get(TOPIC)
    assert len(calls) == MAX_ATTEMPTS


def test_client_errors_are_not_retried():
    handler, calls = site([httpx.Response(404)])
    with pytest.raises(FetchError, match="404"):
        fetcher(handler).get(TOPIC)
    assert len(calls) == 1


def test_robots_txt_is_respected():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /index.php\n")
        raise AssertionError("fetched a disallowed page")

    with pytest.raises(FetchError, match="robots"):
        fetcher(handler).get(TOPIC)


def robots_site(robots_responses):
    """robots.txt answers `robots_responses` in turn; pages answer 200."""
    robots_calls = []

    def handler(request):
        if request.url.path == "/robots.txt":
            robots_calls.append(request)
            return robots_responses[len(robots_calls) - 1]
        return httpx.Response(200, text="page")

    return handler, robots_calls


def test_a_missing_robots_txt_means_no_rules():
    handler, _ = robots_site([httpx.Response(404)])
    assert fetcher(handler).get(TOPIC) == "page"


def test_a_robots_txt_server_error_is_retried():
    handler, robots_calls = robots_site(
        [httpx.Response(503), httpx.Response(200, text=ROBOTS)]
    )
    assert fetcher(handler).get(TOPIC) == "page"
    assert len(robots_calls) == 2


def test_an_unreachable_robots_txt_fetches_nothing():
    handler, robots_calls = robots_site([httpx.Response(503)] * MAX_ATTEMPTS)
    with pytest.raises(FetchError, match=r"robots\.txt unreachable"):
        fetcher(handler).get(TOPIC)
    assert len(robots_calls) == MAX_ATTEMPTS


def test_requests_wait_for_the_delay():
    sleeps = []
    handler, _ = site([httpx.Response(200), httpx.Response(200)])
    polite = Fetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        delay=60,
        sleep=sleeps.append,
    )
    polite.get(TOPIC)
    polite.get(TOPIC)
    # robots.txt went first; both pages waited for the one before
    assert len(sleeps) == 2
    assert all(s > 59 for s in sleeps)


def test_first_request_does_not_wait_on_a_fresh_machine(monkeypatch):
    """monotonic() counts from boot: a CI runner may be up for under a minute."""
    monkeypatch.setattr("aviary.magpie.fetch.time.monotonic", lambda: 5.0)
    sleeps = []
    handler, _ = site([httpx.Response(200)])
    Fetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        delay=60,
        sleep=sleeps.append,
    ).get(TOPIC)
    # robots.txt went first without waiting; only the page waited for it
    assert sleeps == [60.0]
