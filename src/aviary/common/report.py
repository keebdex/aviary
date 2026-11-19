"""Logging and the GitHub Actions job summary."""

import logging
import os


def configure_logging() -> None:
    """Log INFO and above to stderr, one line per message."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def write_summary(markdown: str) -> None:
    """Append Markdown to the job summary when running in GitHub Actions."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as summary:
        summary.write(markdown.rstrip() + "\n")
