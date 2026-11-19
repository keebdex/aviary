"""The Supabase client every job uses."""

import os
import uuid

from supabase import Client, ClientOptions, create_client

from aviary.common.config import require_env

# One id per process (the GitHub Actions run when there is one), so Keebdex
# groups a run's revisions into one entry.
RUN_ID = os.environ.get("GITHUB_RUN_ID") or str(uuid.uuid4())


def create_supabase(source: str | None = None) -> Client:
    """Create a service-role client.

    Keebdex records changes to its catalog tables as revisions and, for the
    service role, trusts the `x-keebdex-source` and `x-keebdex-run` headers to
    tell which job made them. Pass `source` only for jobs that write those
    tables; a new source must also be whitelisted in Keebdex's
    `record_revision()` trigger.
    """
    headers = {"x-keebdex-source": source, "x-keebdex-run": RUN_ID} if source else {}
    return create_client(
        require_env("SUPABASE_URL"),
        require_env("SUPABASE_KEY"),
        options=ClientOptions(
            headers=headers, auto_refresh_token=False, persist_session=False
        ),
    )
