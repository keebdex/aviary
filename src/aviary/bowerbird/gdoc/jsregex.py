"""JavaScript's global regular expressions, `lastIndex` included.

catalog-sync's parser keeps its regexes (flags `gim`) at module level and calls
`exec` on them. A global regex remembers where its last match ended
(`lastIndex`), the next `exec`, even on another string, starts there, and a
failed `exec` or a `String.replace` resets it to 0. The parser mostly calls exec
then replace, which resets it, but not everywhere, and there the left-over index
changes what the next cell or sculpt matches. Same input, same output.

Patterns compile with re.ASCII: JavaScript's \\d, \\w, \\b, and case folding
(without the `u` flag) are ASCII-only. Its \\s is not: use JS_SPACE.
"""

import re

# JavaScript's whitespace, for its regex class and String.prototype.trim (no
# `u` flag): Python's differs (it lacks U+FEFF and has U+001C to U+001F).
JS_WHITESPACE = "".join(
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
JS_SPACE = f"[{re.escape(JS_WHITESPACE)}]"
# JavaScript's `.` without the `s` flag: anything but a line terminator.
JS_DOT = r"[^\n\r\u2028\u2029]"


def js_trim(text: str) -> str:
    """String.prototype.trim."""
    return text.strip(JS_WHITESPACE)


class GlobalRegex:
    def __init__(self, pattern: str):
        self.pattern = re.compile(pattern, re.IGNORECASE | re.MULTILINE | re.ASCII)
        self.last_index = 0

    def exec(self, text: str) -> re.Match | None:
        if self.last_index > len(text):
            self.last_index = 0
            return None
        match = self.pattern.search(text, self.last_index)
        self.last_index = match.end() if match else 0
        return match

    def replace(self, text: str) -> str:
        """text.replace(regex, ''): every match removed, lastIndex back to 0."""
        self.last_index = 0
        return self.pattern.sub("", text)
