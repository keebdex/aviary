"""Running network work a few at a time."""

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor

# Enough to overlap round trips without hammering an API.
CONCURRENCY = 5


def run_each[T](items: Iterable[T], work: Callable[[T], bool]) -> int:
    """Run `work` on every item, CONCURRENCY at a time; how many returned
    False. httpx clients, supabase-py, and boto3 clients are thread-safe."""
    with ThreadPoolExecutor(CONCURRENCY) as pool:
        return sum(1 for ok in pool.map(work, items) if not ok)
