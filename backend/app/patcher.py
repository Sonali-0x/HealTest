"""Unified-diff build/apply helpers (minimal Phase 0 version)."""

from __future__ import annotations

import difflib
import os
import shutil
import tempfile


def build_diff(rel_path: str, old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
        )
    )


def apply_diff_to_copy(test_dir: str, diff: str) -> str:
    """Copy test_dir to temp and apply a simple one-hunk-per-file diff.

    Supports the minimal single-line replacements the mock gateway emits.
    Full multi-hunk application lands in Phase 3.
    """
    tmp = tempfile.mkdtemp(prefix="healtest-")
    for name in os.listdir(test_dir):
        src = os.path.join(test_dir, name)
        dst = os.path.join(tmp, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    current: str | None = None
    old_line: str | None = None
    new_line: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[len("+++ b/") :]
        elif line.startswith("-") and not line.startswith("---"):
            old_line = line[1:] + "\n"
        elif line.startswith("+") and not line.startswith("+++"):
            new_line = line[1:] + "\n"
            if current and old_line is not None and new_line is not None:
                target = os.path.join(tmp, current)
                with open(target, encoding="utf-8") as fh:
                    content = fh.read()
                content = content.replace(old_line, new_line, 1)
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write(content)
                old_line = new_line = None
    return tmp
