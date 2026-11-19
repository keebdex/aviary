"""URL slugs that match Keebdex's and catalog-sync's exactly.

Keebdex builds page keys from slugs (sculpt_id, keyset and keyboard slugs), and
the same name must give the same slug whichever side builds it, or a synced
record and an app-made one would get two pages. Both sides use `urlSlugify`
(strip some punctuation, then npm `slugify` 1.6.9 with `lower: true`); this is a
line-by-line port of it. `slugify_charmap.json` is that package's character map
(MIT, https://github.com/simov/slugify), and tests/test_slug.py checks the port
against values from the JavaScript version. Python slug libraries transliterate
what npm slugify drops (CJK, the multiplication sign, superscripts), so none
of them can stand in for it.
"""

import json
import re
import unicodedata
from functools import cache
from importlib import resources

# urlSlugify drops these before slugify sees the text.
_URL_STRIP = re.compile(r"""[*+~.()'"!:@,/]""")

# JavaScript's \s and String.prototype.trim (no `u` flag), which differ from
# Python's: they have U+FEFF and lack U+001C to U+001F.
_JS_SPACES = "".join(
    chr(code)
    for code in (
        *range(0x09, 0x0E),  # tab, line feed, vertical tab, form feed, return
        0x20,
        0xA0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
        0xFEFF,
    )
)
_JS_SPACE_CLASS = re.escape(_JS_SPACES)

# slugify's default `remove`: anything but JavaScript's \w (ASCII only),
# whitespace, and a few symbols.
_NOT_ALLOWED = re.compile(rf"""[^A-Za-z0-9_{_JS_SPACE_CLASS}$*+~.()'"!\-:@]+""")
_SPACE_RUNS = re.compile(rf"[{_JS_SPACE_CLASS}]+")


@cache
def _charmap() -> dict[str, str]:
    data = resources.files("aviary.common").joinpath("slugify_charmap.json")
    return json.loads(data.read_text(encoding="utf-8"))


def slugify(text: str) -> str:
    """npm slugify(text, { lower: true })."""
    charmap = _charmap()
    mapped = []
    # JavaScript splits by UTF-16 code unit; characters outside the BMP are in
    # no map and removed below either way, so code points give the same result.
    for char in unicodedata.normalize("NFC", text):
        replacement = charmap.get(char, char)
        mapped.append(" " if replacement == "-" else replacement)
    slug = _NOT_ALLOWED.sub("", "".join(mapped)).strip(_JS_SPACES)
    return _SPACE_RUNS.sub("-", slug).lower()


def url_slugify(text: str) -> str:
    """Keebdex's urlSlugify: the slug used in URLs and page keys."""
    return slugify(_URL_STRIP.sub("", text))
