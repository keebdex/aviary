"""The HTTP client jobs use to read websites."""

import httpx

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def create_http_client() -> httpx.Client:
    """Create a client that follows redirects and gives up after 30 seconds."""
    return httpx.Client(
        headers={"User-Agent": BROWSER_USER_AGENT},
        follow_redirects=True,
        timeout=30,
    )
