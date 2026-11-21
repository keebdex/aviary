"""Reading Geekhack politely: robots.txt, one request at a time with a delay
between them, and retries with backoff on 429 and server errors."""

import logging
import time
from collections.abc import Callable
from urllib.robotparser import RobotFileParser

import httpx

from aviary.common.http import create_http_client
from aviary.magpie.geekhack import BASE_URL

log = logging.getLogger(__name__)

# A generic identifier: no personal contact details are sent to the site.
USER_AGENT = "aviary-magpie/0.1 (+https://keebdex.org)"
REQUEST_DELAY_SECONDS = 1.0
MAX_ATTEMPTS = 4
MAX_BACKOFF_SECONDS = 30.0
# The forum declares ISO-8859-1 only in a <meta> tag; browsers read that as
# cp1252.
ENCODING = "cp1252"


class FetchError(Exception):
    """A page could not be fetched; the caller may skip it. `status` is the
    HTTP status when the site answered."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class _Retryable(FetchError):
    pass


class Fetcher:
    def __init__(
        self,
        client: httpx.Client | None = None,
        delay: float = REQUEST_DELAY_SECONDS,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if client is None:
            client = create_http_client()
            client.headers["User-Agent"] = USER_AGENT
        self._client = client
        self._delay = delay
        self._backoff = backoff
        self._sleep = sleep
        self._last_request: float | None = None
        self._robots: RobotFileParser | None = None

    def close(self) -> None:
        self._client.close()

    def get(self, url: str) -> str:
        if not self._allowed(url):
            raise FetchError(f"blocked by robots.txt: {url}")
        return self._with_retries(url)

    def _with_retries(self, url: str) -> str:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self._request(url)
            except _Retryable:
                if attempt == MAX_ATTEMPTS:
                    raise
                self._sleep(
                    min(self._backoff * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS)
                )
        raise AssertionError("unreachable")

    def _allowed(self, url: str) -> bool:
        """RFC 9309: a robots.txt the site answers 4xx for means no rules;
        one unreachable after retries (5xx, 429, network) means full
        disallow, so it raises and the run reads nothing. Not cached then:
        the next request asks again."""
        if self._robots is None:
            robots = RobotFileParser()
            try:
                text = self._with_retries(f"{BASE_URL}/robots.txt")
            except _Retryable as error:
                raise FetchError(
                    f"robots.txt unreachable, not crawling: {error}", error.status
                ) from error
            except FetchError as error:
                log.warning("robots.txt unavailable (%s); no restrictions", error)
                text = ""
            robots.parse(text.splitlines())
            self._robots = robots
        return self._robots.can_fetch(USER_AGENT, url)

    def _request(self, url: str) -> str:
        # monotonic() counts from boot, so the first request must not compare
        # against 0: on a machine up for less than the delay it would wait.
        if self._last_request is not None:
            wait = self._delay - (time.monotonic() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = time.monotonic()
        try:
            response = self._client.get(url)
        except httpx.TransportError as error:
            raise _Retryable(f"{url}: {error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise _Retryable(
                f"{url}: HTTP {response.status_code}", response.status_code
            )
        if response.status_code != 200:
            raise FetchError(
                f"{url}: HTTP {response.status_code}", response.status_code
            )
        if response.charset_encoding is None:
            response.encoding = ENCODING
        return response.text
