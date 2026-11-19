"""Which Cloudflare images the database references.

Shared by prune (keep these) and restore (bring these back). Add a column to
IMAGE_COLUMNS whenever a table starts storing Cloudflare images, or prune will
delete its images.
"""

from collections.abc import Callable

from supabase import Client

Row = dict
PAGE_SIZE = 1000

# Every column that can hold a Cloudflare Images URL. Rows of any review
# status count: a Pending submission's image must survive until it's decided.
IMAGE_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("artisan_colorways", ("img",)),
    ("keysets", ("img", "order_graph", "order_history")),
    ("keyset_kits", ("img",)),
    ("keyboard_variants", ("img_front", "img_back")),
)


def to_image_id(url: object, base_url: str) -> str | None:
    """`<base_url>/<id>/<variant>` -> `<id>` (ids may contain slashes); None
    for anything else."""
    if not isinstance(url, str) or not url.startswith(f"{base_url}/"):
        return None
    path = url[len(base_url) + 1 :]
    # like JavaScript's slice(0, lastIndexOf('/')): no slash drops the last
    # character, as catalog-sync did
    return path[: path.rfind("/")]


def fetch_all(
    client: Client,
    table: str,
    columns: str,
    where: Callable = lambda query: query,
) -> list[Row]:
    """Every row, in pages. Raises on any error (supabase-py does): a failed
    read must never look like "nothing to do" or "nothing referenced"."""
    rows: list[Row] = []
    while True:
        query = where(client.table(table).select(columns))
        data = (
            query.order("id").range(len(rows), len(rows) + PAGE_SIZE - 1).execute().data
        )
        rows += data
        if len(data) < PAGE_SIZE:
            return rows


def referenced_images(client: Client, base_url: str) -> set[str]:
    referenced: set[str] = set()
    for table, columns in IMAGE_COLUMNS:
        for row in fetch_all(client, table, ", ".join(("id", *columns))):
            for column in columns:
                if image_id := to_image_id(row.get(column), base_url):
                    referenced.add(image_id)

    # An image added through an edit suggestion (e.g. Add Photo) is only in
    # the suggestion's `changes` ({ column: value }) until staff approve it.
    pending = fetch_all(
        client,
        "edit_suggestions",
        "id, changes",
        lambda query: query.eq("review_status", "Pending"),
    )
    for suggestion in pending:
        for value in (suggestion.get("changes") or {}).values():
            if image_id := to_image_id(value, base_url):
                referenced.add(image_id)
    return referenced
