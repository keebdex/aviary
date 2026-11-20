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
