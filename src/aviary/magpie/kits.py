"""Keebdex `kit_categories` a Geekhack post can label an image with."""

import re

# Slugs of `kit_categories` detected from post text.
DETECTED_KITS = ("base", "novelties", "spacebars")
KIT_NAMES = {"base": "Base", "novelties": "Novelties", "spacebars": "Spacebars"}

# The whole line has to be the label ("Base Kit", "Novelties:", "Space bars -
# $30"): a sentence that merely mentions a kit ("Convex DSA Space Bars") must
# not switch the kit.
_LABEL_RE = re.compile(
    r"""^\W*
    (?:(?:the|extra|additional)\s+)?
    (?:(?P<base>base)|(?P<novelties>novelt(?:y|ies))|(?P<spacebars>space\s?-?bars?))
    (?:\s+(?:kit|kits|set|sets|pack))?
    [\s:\-\u2013\u2014]*(?:\$?\d[\d.,]*)?\W*$""",
    re.IGNORECASE | re.VERBOSE,
)


def classify_kit_label(text: str) -> str | None:
    """The kit slug a line of post text announces, or None."""
    match = _LABEL_RE.match(text.strip())
    if match is None:
        return None
    return next(slug for slug in DETECTED_KITS if match.group(slug))
