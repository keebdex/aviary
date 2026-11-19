"""JavaScript's global regular expressions, `lastIndex` included.

catalog-sync's parser keeps its regexes (flags `gim`) at module level and calls
`exec` on them. A global regex remembers where its last match ended
(`lastIndex`), the next `exec`, even on another string, starts there, and a
failed `exec` or a `String.replace` resets it to 0. The parser mostly calls exec
then replace, which resets it, but not everywhere, and there the left-over index
changes what the next cell or sculpt matches. Same input, same output.

Patterns compile with re.ASCII: JavaScript's \\d, \\w, \\b, and case folding
(without the `u` flag) are ASCII-only. Its \\s is not: spell it as JS_SPACE.
"""

import re

JS_SPACE = r"[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]"
# JavaScript's `.` without the `s` flag: anything but a line terminator.
JS_DOT = r"[^\n\r\u2028\u2029]"


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
