"""Safety checks (PRD FR-9). Full assertion parsing hardens in Phase 3."""

from __future__ import annotations

import os
import re

MAX_DIFF_BYTES = 20_000

_ASSERT_RE = re.compile(r"(^|\b)(assert|assert[A-Z]\w*|expect\(|should\b)")


def _changed_files(diff: str) -> list[str]:
    files = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[len("+++ b/") :])
    return files


def check_test_dir_only(diff: str, test_root: str = "tests") -> str | None:
    for path in _changed_files(diff):
        norm = os.path.normpath(path).replace("\\", "/")
        if norm != test_root and not norm.startswith(test_root.rstrip("/") + "/"):
            return f"diff touches non-test file: {path}"
    return None


def check_diff_size(diff: str, limit: int = MAX_DIFF_BYTES) -> str | None:
    if len(diff.encode("utf-8")) > limit:
        return f"diff exceeds {limit} bytes"
    return None


def check_assertions(old_code: str, new_code: str) -> str | None:
    old_count = len(_ASSERT_RE.findall(old_code))
    new_count = len(_ASSERT_RE.findall(new_code))
    if new_count < old_count:
        return f"assertion count dropped {old_count} -> {new_count}"
    return None


def check_all(diff: str, old_files: dict[str, str], new_files: dict[str, str]) -> list[str]:
    """Return a list of rejection reasons; empty means the patch is safe."""
    reasons = []
    for check in (check_test_dir_only(diff), check_diff_size(diff)):
        if check:
            reasons.append(check)
    for path, old in old_files.items():
        reason = check_assertions(old, new_files.get(path, ""))
        if reason:
            reasons.append(f"{path}: {reason}")
    return reasons
