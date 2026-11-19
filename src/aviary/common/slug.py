"""URL slugs for synced records.

Slugs are python-slugify's, with `&`, `$`, `%`, `<`, `>`, and `|` spelled out
and underscores kept, after dropping the punctuation Keebdex's `urlSlugify`
drops (`*+~.()'"!:@,/`), so ordinary names get the same slug in both.

Unusual characters differ: catalog-sync used npm slugify, which drops what is
missing from its character map (CJK, the multiplication sign) and keeps curly
quotes as straight ones, where python-slugify transliterates or removes them.
LEGACY_SLUGS keeps the slugs catalog-sync already gave such names, so their
sculpts and colorways keep their keys; new names follow this function. Add an
entry only for a name catalog-sync synced (docs/slugs.md says how to check).
"""

import re

from slugify import slugify

_URL_STRIP = re.compile(r"""[*+~.()'"!:@,/]""")
_REPLACEMENTS = [
    ["&", "and"],
    ["$", "dollar"],
    ["%", "percent"],
    ["<", "less"],
    [">", "greater"],
    ["|", "or"],
]
_NOT_ALLOWED = r"[^-a-z0-9_]+"

# name (trimmed) -> slug catalog-sync gave it: 15 sculpts (404artisans,
# asianjoyco) and alpha-keycaps colorways whose ids hash the slug
LEGACY_SLUGS = {
    "#094 \xd7 Christmas": "094-christmas",
    "Almond Cookie |\u674f\u4ec1\u9905": "almond-cookie-or",
    "Black Sesame Roll |\u829d\u9ebb\u5377": "black-sesame-roll-or",
    "BoloBao |\u83e0\u863f\u5305": "bolobao-or",
    "Char Siu Bao | BBQ Pork Bun | \u53c9\u71d2\u5305": "char-siu-bao-or-bbq-pork-bun-or",  # noqa: E501
    "DK\u2019s Banana": "dk's-banana",
    "Dan Tat | Egg Tart | \u86cb\u5854": "dan-tat-or-egg-tart-or",
    "Dementor\u2019s Kiss": "dementor's-kiss",
    "Dim Sum Teapot | \u8336\u58f6": "dim-sum-teapot-or",
    "D\u2019ohnut": "d'ohnut",
    "D\u2019va": "d'va",
    "Feng Zhao | Chicken Feet |\u51e4\u722a": "feng-zhao-or-chicken-feet-or",
    "God\u2019s Nectar": "god's-nectar",
    "Har Gow | Shrimp Dumpling | \u867e\u997a": "har-gow-or-shrimp-dumpling-or",
    "Hell\u2019s Pigeon": "hell's-pigeon",
    "Jiaozi | Chinese Dumpling | \u997a\u5b50": "jiaozi-or-chinese-dumpling-or",
    "Mantou | Steamed Bun | \u9992\u5934": "mantou-or-steamed-bun-or",
    "Mooncake | \u6708\u997c": "mooncake-or",
    "Munkii\u2019s Dream": "munkii's-dream",
    "Munkii\u2019s Prayer": "munkii's-prayer",
    "Na\u2019vi": "na'vi",
    "OG D\u2019Or": "og-d'or",
    "Raison D\u2019Etre": "raison-d'etre",
    "Shumai | \u71d2\u8ce3": "shumai-or",
    "Volker\u2019s Blessing": "volker's-blessing",
    "XLB | Soup Dumpling | \u5c0f\u7b3c\u5305": "xlb-or-soup-dumpling-or",
    "Zongzi |\u7cbd\u5b50": "zongzi-or",
    "\u201cArizona Meetup\u201d": '"arizona-meetup"',
    "\u201cShe\u2019ll say yes, Amen\u201d": '"she\'ll-say-yes-amen"',
}


def url_slugify(text: str) -> str:
    """The slug used in URLs, page keys, and colorway ids."""
    legacy = LEGACY_SLUGS.get(text.strip())
    if legacy is not None:
        return legacy
    return slugify(
        _URL_STRIP.sub("", text),
        replacements=_REPLACEMENTS,
        regex_pattern=_NOT_ALLOWED,
    )
