"""Guess from its title whether a topic sells a keyset or a keyboard.

The rules are deliberately cautious: when the title points both ways, or
nowhere, the answer is "unknown" rather than a coin flip, and only "keyset"
topics are proposed to Keebdex.
"""

import re
from dataclasses import dataclass
from typing import Literal

from aviary.magpie.geekhack import extract_prefix, strip_prefix

SuggestedKind = Literal["keyset", "keyboard", "other", "unknown"]

# Keycap profiles and manufacturers keycap topics start with ("GMK Dasher",
# "MW Jirai").
KEYCAP_CODES = frozenset(
    {
        "GMK",
        "DCS",
        "DSS",
        "DSA",
        "SA",
        "MT3",
        "MTNU",
        "KAT",
        "KAM",
        "XDA",
        "MDA",
        "CHERRY",
        "MW",
        "SWG",
        "KKB",
        "EPBT",
        "PBTFANS",
        "JTK",
        "DMK",
    }
)

_KEYBOARD_RE = re.compile(
    r"\bkeyboards?\b|(?<!\d)\d{2,3}\s?%|\btkl\b|\bnumpad\b|\bergo\b|\bfull[- ]?size\b"
    r"|\bmodel f\b|\bbeam spring\b|\bpcb\b|\bhhkb\b|\balice\b|\barisu\b",
    re.IGNORECASE,
)
_KEYCAP_RE = re.compile(
    r"\bkey ?caps?\b|\bkey ?sets?\b|\bnovelt(?:y|ies)\b|\bdoubleshot\b|\bdye[- ]?sub\b"
    r"|\bpbt\b|\babs\b",
    re.IGNORECASE,
)
_FIRST_WORD_RE = re.compile(r"[A-Za-z0-9]+")
# Topics that announce a set; anything else is rules, chatter, and the like.
# A pre-order sells like a group buy; its status follows the board.
SET_PREFIXES = frozenset({"GB", "IC", "PRE-ORDER", "PREORDER", "PRE ORDER"})


@dataclass(frozen=True)
class Suggestion:
    kind: SuggestedKind
    reason: str
    profile_code: str | None = None


def suggest(title: str) -> Suggestion:
    if extract_prefix(title).upper() not in SET_PREFIXES:
        return Suggestion("other", "no [GB], [IC] or [Pre-order] prefix")
    name = strip_prefix(title)
    first = _FIRST_WORD_RE.match(name)
    if first and first.group().upper() in KEYCAP_CODES:
        code = first.group().upper()
        # Checked before keywords: "DSS Drank - 40% Monokit" is keycaps for a
        # 40% board.
        return Suggestion("keyset", f"starts with keycap code {code}", code)
    keyboard = _KEYBOARD_RE.search(name)
    keycap = _KEYCAP_RE.search(name)
    if keyboard and keycap:
        return Suggestion(
            "unknown", f'mentions both "{keycap.group()}" and "{keyboard.group()}"'
        )
    if keyboard:
        return Suggestion("keyboard", f'mentions "{keyboard.group()}"')
    if keycap:
        return Suggestion("keyset", f'mentions "{keycap.group()}"')
    return Suggestion("unknown", "no keycap or keyboard hint in the title")
