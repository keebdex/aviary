"""Release dates written in makers' docs.

catalog-sync parsed them with date-fns, which also took forms nobody should
write (a one-letter month, a year of 1 to 3 digits). Only the usual forms are
converted here; anything else is kept as written, with a warning when it looks
like a date. Month names are English, so run with an English or C locale (the
default on GitHub Actions).
"""

import logging
from datetime import datetime

log = logging.getLogger(__name__)

MONTH_YEAR_FORMATS = ("%B %Y", "%b %Y")  # "January 2023", "Jan 2023"
FULL_DATE_FORMATS = ("%Y/%m/%d", "%Y/%d/%m")  # month first, else day first
MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)  # fmt: skip


def _convert(text: str, formats: tuple[str, ...], output: str) -> str | None:
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).strftime(output)
        except ValueError:
            pass
    return None


def month_year(text: str) -> str:
    """A sculpt or colorway release: "January 2023" or "Jan 2023" becomes
    "Jan 2023". Anything else ("2023", "Summer 2023") stays as written, with a
    warning when it starts like a month ("J 2023", "Sept 2023")."""
    converted = _convert(text, MONTH_YEAR_FORMATS, "%b %Y")
    if converted is not None:
        return converted
    first = text.split(" ", 1)[0].rstrip(".").lower()
    if first.isalpha() and any(month.startswith(first) for month in MONTHS):
        log.warning("Release %r is not like 'January 2023'; kept as written", text)
    return text


def full_date(text: str) -> str:
    """jelly-key's release, "2023/5/17" or "2023/17/5", as "17 May 2023".
    Anything else (a 2-digit year, dashes) stays as written, with a warning."""
    converted = _convert(text, FULL_DATE_FORMATS, "%d %b %Y")
    if converted is None:
        log.warning("Release %r is not YYYY/M/D or YYYY/D/M; kept as written", text)
        return text
    return converted
