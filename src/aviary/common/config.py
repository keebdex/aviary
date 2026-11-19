"""Settings read from the environment.

Locally they come from `.env` (`uv run --env-file .env ...`), in GitHub
Actions from the repository secrets.
"""

import os


class MissingSettingError(RuntimeError):
    """A required environment variable is not set."""


def require_env(name: str) -> str:
    """Return the environment variable `name`, or raise when it is empty."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingSettingError(f"{name} is not set")
    return value
