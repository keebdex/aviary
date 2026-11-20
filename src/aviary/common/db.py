"""The Supabase client every job uses, and paged reads."""

import os
import uuid
from collections.abc import Callable
from typing import Any

from postgrest import SyncPostgrestClient
from supabase import Client, ClientOptions, create_client

from aviary.common.config import require_env

# One id per process (the GitHub Actions run when there is one), so Keebdex
# groups a run's revisions into one entry.
RUN_ID = os.environ.get("GITHUB_RUN_ID") or str(uuid.uuid4())

# A database row as supabase-py returns it.
Row = dict[str, Any]

# PostgREST returns at most this many rows per request.
PAGE_SIZE = 1000

# Keebdex's PUBLISHED_FILTER: added by staff (null) or approved.
PUBLISHED_FILTER = "review_status.is.null,review_status.eq.Approved"


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


def fetch_all(
    client: Client | SyncPostgrestClient,
    table: str,
    columns: str = "*",
    where: Callable = lambda query: query,
    order: str = "id",
) -> list[Row]:
    """Every row matching `where`, in pages ordered by `order` (a column whose
    values are unique among those rows). `client` may also be a schema's
    client (`client.schema("ingest")`). Raises on any error (supabase-py
    does): a partial read must never look like "nothing there"."""
    rows: list[Row] = []
    while True:
        query = where(client.table(table).select(columns)).order(order)
        data = query.range(len(rows), len(rows) + PAGE_SIZE - 1).execute().data
        rows += data
        if len(data) < PAGE_SIZE:
            return rows
