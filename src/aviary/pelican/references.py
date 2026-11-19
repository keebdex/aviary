"""Which Cloudflare images the database references.

Shared by prune (keep these) and restore (bring these back), and by sync,
which uploads into these columns. Add a column to IMAGE_COLUMNS whenever a
table starts storing Cloudflare images, or prune will delete its images.
"""

from supabase import Client

from aviary.common.cf_images import to_image_id
from aviary.common.db import fetch_all

# Every column that can hold a Cloudflare Images URL. Rows of any review
# status count: a Pending submission's image must survive until it's decided.
IMAGE_COLUMNS: dict[str, tuple[str, ...]] = {
    "artisan_colorways": ("img",),
    "keysets": ("img", "order_graph", "order_history"),
    "keyset_kits": ("img",),
    "keyboard_variants": ("img_front", "img_back"),
}


def referenced_images(client: Client, base_url: str) -> set[str]:
    referenced: set[str] = set()
    for table, columns in IMAGE_COLUMNS.items():
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
