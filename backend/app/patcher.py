"""Unified-diff build and apply helpers."""

from __future__ import annotations

import difflib
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath

_HUNK_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(ValueError):
    """Raised when a unified diff cannot be applied exactly."""


def build_diff(rel_path: str, old: str, new: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
    )
    output: list[str] = []
    for line in lines:
        if line.endswith("\n"):
            output.append(line)
        else:
            output.extend((line + "\n", "\\ No newline at end of file\n"))
    return "".join(output)


def _validated_path(header: str, prefix: str) -> str:
    if not header.startswith(prefix):
        raise PatchError(f"diff path must start with {prefix!r}: {header!r}")
    path = header[len(prefix) :].split("\t", 1)[0].strip().rstrip("\r\n")
    parsed = PurePosixPath(path)
    if (
        not path
        or "\\" in path
        or "\x00" in path
        or parsed.is_absolute()
        or any(part in ("", ".", "..") for part in parsed.parts)
    ):
        raise PatchError(f"unsafe diff path: {path!r}")
    return parsed.as_posix()


def _apply_hunks(source: str, hunks: list[tuple[int, int, int, list[str]]]) -> str:
    original = source.splitlines(keepends=True)
    result: list[str] = []
    source_pos = 0

    for old_start, old_count, new_count, body in hunks:
        target_pos = old_start - 1 if old_count else old_start
        if target_pos < source_pos or target_pos > len(original):
            raise PatchError("diff hunk position is outside the source file")
        result.extend(original[source_pos:target_pos])
        source_pos = target_pos
        old_seen = new_seen = 0
        normalized_body: list[str] = []
        for line in body:
            if line.startswith("\\ No newline at end of file"):
                if normalized_body:
                    normalized_body[-1] = normalized_body[-1].rstrip("\r\n")
            else:
                normalized_body.append(line)
        for line in normalized_body:
            if not line or line[0] not in " +-":
                raise PatchError(f"invalid unified diff line: {line!r}")
            value = line[1:]
            if line[0] in " -":
                if source_pos >= len(original) or original[source_pos] != value:
                    raise PatchError("diff context does not match the source file")
                source_pos += 1
                old_seen += 1
            if line[0] in " +":
                result.append(value)
                new_seen += 1
        if old_seen != old_count or new_seen != new_count:
            raise PatchError("diff hunk line counts do not match its header")

    result.extend(original[source_pos:])
    return "".join(result)


def apply_diff_to_files(
    files: dict[str, str], diff: str, strip_prefix: str = ""
) -> dict[str, str]:
    """Apply a unified diff to an in-memory mapping of relative file paths."""
    lines = diff.splitlines(keepends=True)
    if not lines:
        raise PatchError("diff is empty")
    output = dict(files)
    index = 0
    applied = 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            raise PatchError(f"expected unified diff file header at line {index + 1}")
        old_path = _validated_path(lines[index][4:].rstrip("\r\n"), "a/")
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise PatchError("unified diff is missing its new-file header")
        new_path = _validated_path(lines[index][4:].rstrip("\r\n"), "b/")
        if old_path != new_path:
            raise PatchError("renames and file additions/deletions are not supported")
        index += 1
        if strip_prefix:
            normalized_prefix = PurePosixPath(strip_prefix).as_posix().rstrip("/") + "/"
            if not old_path.startswith(normalized_prefix):
                raise PatchError(f"diff path is outside {strip_prefix!r}: {old_path}")
            relative_path = old_path[len(normalized_prefix) :]
        else:
            relative_path = old_path
        if relative_path not in output:
            raise PatchError(f"diff target is not an existing file: {old_path}")

        hunks: list[tuple[int, int, int, list[str]]] = []
        while index < len(lines) and not lines[index].startswith("--- "):
            match = _HUNK_RE.match(lines[index])
            if not match:
                raise PatchError(f"invalid unified diff hunk header: {lines[index].rstrip()!r}")
            old_start, old_count, _, new_count = match.groups()
            index += 1
            body: list[str] = []
            old_seen = new_seen = 0
            expected_old = int(old_count) if old_count is not None else 1
            expected_new = int(new_count) if new_count is not None else 1
            while (
                index < len(lines)
                and (
                    old_seen < expected_old
                    or new_seen < expected_new
                    or lines[index].startswith("\\ No newline at end of file")
                )
                and not _HUNK_RE.match(lines[index])
            ):
                line = lines[index]
                if not line.startswith("\\ No newline at end of file"):
                    if not line or line[0] not in " +-":
                        raise PatchError(f"invalid unified diff line: {line!r}")
                    if line[0] in " -":
                        old_seen += 1
                    if line[0] in " +":
                        new_seen += 1
                body.append(line)
                index += 1
            if old_seen != expected_old or new_seen != expected_new:
                raise PatchError("diff hunk line counts do not match its header")
            hunks.append(
                (int(old_start), expected_old, expected_new, body)
            )
        if not hunks:
            raise PatchError("unified diff contains no hunks")
        output[relative_path] = _apply_hunks(output[relative_path], hunks)
        applied += 1

    if not applied:
        raise PatchError("unified diff contains no changed files")
    return output


def apply_diff_to_copy(
    test_dir: str,
    diff: str,
    destination: str | None = None,
    strip_prefix: str = "",
) -> str:
    """Copy a test tree and apply every diff hunk to the copy."""
    source_root = Path(test_dir).resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"test directory not found: {test_dir}")
    files: dict[str, str] = {}
    for line in diff.splitlines():
        if line.startswith("--- a/"):
            path = _validated_path(line[4:], "a/")
            relative = path
            if strip_prefix:
                prefix = PurePosixPath(strip_prefix).as_posix().rstrip("/") + "/"
                if not path.startswith(prefix):
                    raise PatchError(f"diff path is outside {strip_prefix!r}: {path}")
                relative = path[len(prefix) :]
            source_path = source_root.joinpath(*PurePosixPath(relative).parts)
            if not source_path.is_file():
                raise PatchError(f"diff target is not an existing file: {path}")
            resolved_source = source_path.resolve()
            try:
                is_contained = (
                    os.path.commonpath((str(source_root), str(resolved_source)))
                    == str(source_root)
                )
            except ValueError:
                is_contained = False
            if not is_contained:
                raise PatchError(f"diff target escapes the source tree: {path}")
            files[relative] = resolved_source.read_text(encoding="utf-8")

    updated = apply_diff_to_files(files, diff, strip_prefix=strip_prefix)
    if destination is None:
        target_root = Path(tempfile.mkdtemp(prefix="healtest-"))
        publish_path = target_root
    else:
        publish_path = Path(destination).resolve()
        if publish_path.exists():
            raise FileExistsError(f"patch destination already exists: {publish_path}")
        publish_path.parent.mkdir(parents=True, exist_ok=True)
        target_root = Path(
            tempfile.mkdtemp(prefix=f".{publish_path.name}-", dir=publish_path.parent)
        )

    try:
        shutil.copytree(source_root, target_root, dirs_exist_ok=True)
        for relative, content in updated.items():
            target = target_root.joinpath(*PurePosixPath(relative).parts)
            if os.path.commonpath((str(target_root), str(target.resolve()))) != str(target_root):
                raise PatchError(f"diff target escapes the copy: {relative}")
            target.write_text(content, encoding="utf-8")
        if publish_path != target_root:
            os.replace(target_root, publish_path)
        return str(publish_path)
    except (OSError, PatchError, ValueError):
        if target_root.exists():
            shutil.rmtree(target_root)
        raise
