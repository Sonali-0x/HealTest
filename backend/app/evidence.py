"""Evidence helpers: DOM trimming, traceback parsing, locator hints."""

from __future__ import annotations

import os
import re

MAX_DOM_CHARS = 12_000


def trim_dom(html: str, focus: str = "") -> str:
    """Strip scripts/styles and cap size; keep region around focus when given."""
    text = re.sub(r"(?is)<script.*?</script>", "", html)
    text = re.sub(r"(?is)<style.*?</style>", "", text)
    if focus and focus in text:
        idx = text.index(focus)
        start = max(0, idx - MAX_DOM_CHARS // 2)
        return text[start : start + MAX_DOM_CHARS]
    return text[:MAX_DOM_CHARS]


def failing_step(error_text: str) -> str:
    """Best-effort first failing line from a pytest traceback."""
    for line in error_text.splitlines():
        if line.strip().startswith("E "):
            return line.strip()[2:].strip()
    return ""


_LOCATION_RES = (
    # pytest short summary: tests/test_x.py:12: NoSuchElementException
    re.compile(r"(?m)^([^\s:]+\.py):(\d+):"),
    # cpython traceback: File "/path/test_x.py", line 12, in test_y
    re.compile(r"File \"([^\"]+\.py)\", line (\d+)"),
)


def parse_failing_location(longrepr_text: str) -> tuple[str, str]:
    """Extract the most likely failing test frame as (file, line).

    Prefers frames inside test files; falls back to the last frame found.
    Returns ("", "") when nothing matches.
    """
    candidates: list[tuple[str, str]] = []
    for rx in _LOCATION_RES:
        for match in rx.finditer(longrepr_text or ""):
            candidates.append((match.group(1), match.group(2)))
    if not candidates:
        return "", ""
    for path, line in reversed(candidates):
        norm = path.replace("\\", "/")
        base = os.path.basename(norm)
        if "/tests/" in norm or base.startswith("test_"):
            return path, line
    return candidates[-1]


_LOCATOR_RES = (
    re.compile(r"NoSuchElementException[^\n]*?:\s*(?P<hint>[^\n]{1,200})"),
    re.compile(r"Unable to locate element:\s*(?P<hint>[^\n]{1,200})"),
    re.compile(r"(?P<hint>By\.\w+,\s*[\"'][^\"']+[\"'])"),
)


def extract_locator_hint(error_text: str) -> str:
    """Best-effort failing locator/method from an error message."""
    for rx in _LOCATOR_RES:
        match = rx.search(error_text or "")
        if match:
            return match.group("hint").strip()
    return ""
