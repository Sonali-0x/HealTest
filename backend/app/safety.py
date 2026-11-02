"""Safety checks for generated test patches."""

from __future__ import annotations

import ast
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import PurePosixPath

from . import patcher

MAX_DIFF_BYTES = 20_000
_HUNK_HEADER = re.compile(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_XPATH_ATTR_RE = re.compile(r"^//(?P<tag>\*|[A-Za-z][\w-]*)\[@(?P<attr>id|name)=(['\"])(?P<value>.*?)\3\]$")


def _diff_paths(diff: str) -> list[str]:
    paths: list[str] = []
    lines = diff.splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            return paths + [lines[index]]
        old_path = lines[index][4:].split("\t", 1)[0]
        if old_path == "/dev/null" or not old_path.startswith("a/"):
            return paths + [old_path]
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            return paths + [old_path]
        new_path = lines[index][4:].split("\t", 1)[0]
        if new_path == "/dev/null" or not new_path.startswith("b/"):
            return paths + [new_path]
        if old_path[2:] != new_path[2:]:
            return paths + [new_path]
        paths.append(new_path[2:])
        index += 1
        while index < len(lines) and not lines[index].startswith("--- "):
            match = _HUNK_HEADER.fullmatch(lines[index])
            if not match:
                return paths + [lines[index]]
            old_count = int(match.group(1)) if match.group(1) is not None else 1
            new_count = int(match.group(2)) if match.group(2) is not None else 1
            old_seen = new_seen = 0
            index += 1
            while index < len(lines) and (old_seen < old_count or new_seen < new_count):
                line = lines[index]
                if line.startswith("\\ No newline at end of file"):
                    index += 1
                    continue
                if not line or line[0] not in " +-":
                    return paths + [line]
                if line[0] in " -":
                    old_seen += 1
                if line[0] in " +":
                    new_seen += 1
                index += 1
    return paths


def check_test_dir_only(diff: str, test_root: str = "tests") -> str | None:
    paths = _diff_paths(diff)
    if not paths:
        return "diff contains no valid file changes"
    root = PurePosixPath(test_root.replace("\\", "/")).as_posix().rstrip("/")
    for path in paths:
        normalized = path.replace("\\", "/")
        parsed = PurePosixPath(normalized)
        if (
            parsed.is_absolute()
            or "\\" in path
            or any(part in ("", ".", "..") for part in parsed.parts)
            or (normalized != root and not normalized.startswith(root + "/"))
        ):
            return f"diff touches non-test or unsafe file: {path}"
    return None


def check_diff_size(diff: str, limit: int = MAX_DIFF_BYTES) -> str | None:
    if len(diff.encode("utf-8")) > limit:
        return f"diff exceeds {limit} bytes"
    return None


def _assertions(code: str) -> Counter[str]:
    tree = ast.parse(code)
    items: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            items[ast.dump(node, include_attributes=False)] += 1
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else ""
            )
            if name.startswith("assert") or name in {"expect", "should"}:
                items[ast.dump(node, include_attributes=False)] += 1
    return items


def check_assertions(old_code: str, new_code: str) -> str | None:
    try:
        old_assertions = _assertions(old_code)
        new_assertions = _assertions(new_code)
    except SyntaxError as exc:
        return f"test code is not valid Python: {exc.msg}"
    removed = old_assertions - new_assertions
    if removed:
        return f"patch removes or weakens an existing assertion ({sum(removed.values())} changed)"
    return None


class _ElementParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict[str, object]] = []
        self._stack: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element: dict[str, object] = {
            "tag": tag.casefold(),
            "attrs": {name.casefold(): value or "" for name, value in attrs},
            "text": "",
        }
        self.elements.append(element)
        if tag.casefold() not in {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        }:
            self._stack.append(element)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index]["tag"] == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        for element in self._stack:
            element["text"] = str(element["text"]) + data


def _css_matches(selector: str, element: dict[str, object]) -> bool | None:
    attrs = element["attrs"]
    assert isinstance(attrs, dict)
    tag = str(element["tag"])
    remainder = selector
    tag_match = re.match(r"^[A-Za-z][\w-]*", remainder)
    if tag_match:
        if tag != tag_match.group(0).casefold():
            return False
        remainder = remainder[tag_match.end() :]
    for token in re.findall(r"#[\w-]+|\.[\w-]+|\[[\w:-]+(?:=['\"][^'\"]*['\"])?\]", remainder):
        if token.startswith("#"):
            if attrs.get("id") != token[1:]:
                return False
        elif token.startswith("."):
            if token[1:] not in str(attrs.get("class", "")).split():
                return False
        else:
            match = re.fullmatch(r"\[([\w:-]+)(?:=['\"]([^'\"]*)['\"])?\]", token)
            if not match:
                return None
            name, value = match.groups()
            if name not in attrs or (value is not None and attrs[name] != value):
                return False
    consumed = re.sub(
        r"#[\w-]+|\.[\w-]+|\[[\w:-]+(?:=['\"][^'\"]*['\"])?\]",
        "",
        remainder,
    )
    return not consumed


def _locator_count(by: str, value: str, elements: list[dict[str, object]]) -> int | None:
    by = by.casefold()
    if by in {"id", "name", "tag name", "class name"}:
        attr = {"id": "id", "name": "name", "class name": "class"}.get(by)
        if by == "tag name":
            return sum(element["tag"] == value.casefold() for element in elements)
        if by == "class name":
            return sum(value in str(element["attrs"].get(attr, "")).split() for element in elements)
        return sum(element["attrs"].get(attr) == value for element in elements)
    if by in {"css selector", "css"}:
        matches = [_css_matches(value, element) for element in elements]
        if any(match is None for match in matches):
            return None
        return sum(match is True for match in matches)
    if by == "xpath":
        match = _XPATH_ATTR_RE.fullmatch(value)
        if match:
            tag, attr, _, expected = match.groups()
            return sum(
                (tag == "*" or element["tag"] == tag.casefold())
                and element["attrs"].get(attr) == expected
                for element in elements
            )
        return None
    if by in {"link text", "partial link text"}:
        anchors = [item for item in elements if item["tag"] == "a"]
        if by == "link text":
            return sum(" ".join(str(item["text"]).split()) == value for item in anchors)
        return sum(value in " ".join(str(item["text"]).split()) for item in anchors)
    return None


def _locator_calls(code: str) -> dict[str, tuple[str, str] | None]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    result: dict[str, tuple[str, str] | None] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"find_element", "find_elements"} or len(node.args) < 2:
            continue
        key = ast.dump(node, include_attributes=False)
        by_node, value_node = node.args[:2]
        if isinstance(by_node, ast.Attribute):
            by = by_node.attr.replace("_", " ").casefold()
        else:
            try:
                by = str(ast.literal_eval(by_node)).replace("_", " ").casefold()
            except (ValueError, TypeError):
                result[key] = None
                continue
        try:
            value = ast.literal_eval(value_node)
        except (ValueError, TypeError):
            result[key] = None
            continue
        if isinstance(value, str):
            result[key] = (by, value)
        else:
            result[key] = None
    return result


def check_locator_uniqueness(
    old_code: str, new_code: str, dom: str
) -> str | None:
    old_calls = _locator_calls(old_code)
    new_calls = _locator_calls(new_code)
    added_calls = [
        new_calls[key] for key in new_calls.keys() - old_calls.keys()
    ]
    if not added_calls:
        return None
    if any(locator is None for locator in added_calls):
        return "cannot verify a newly introduced dynamic locator against the saved DOM"
    parser = _ElementParser()
    try:
        parser.feed(dom)
        parser.close()
    except (AssertionError, ValueError):
        return "saved DOM could not be parsed to check a new locator"
    for locator in added_calls:
        assert locator is not None
        by, value = locator
        count = _locator_count(by, value, parser.elements)
        if count is None:
            return f"cannot verify new {by} locator against the saved DOM"
        if count != 1:
            return f"new {by} locator {value!r} matches {count} elements in the saved DOM"
    return None


def check_all(
    diff: str,
    old_files: dict[str, str],
    new_files: dict[str, str] | None = None,
    dom: str = "",
) -> list[str]:
    """Return all safety rejection reasons; an empty list means checks passed."""
    reasons = [
        reason
        for reason in (check_test_dir_only(diff), check_diff_size(diff))
        if reason
    ]
    try:
        applied = patcher.apply_diff_to_files(old_files, diff, strip_prefix="tests")
    except patcher.PatchError as exc:
        return reasons + [f"invalid patch: {exc}"]
    if new_files is not None and applied != new_files:
        reasons.append("supplied patched files do not match the unified diff")
    for path, old in old_files.items():
        new = applied.get(path, old)
        assertion_error = check_assertions(old, new)
        if assertion_error:
            reasons.append(f"{path}: {assertion_error}")
        locator_error = check_locator_uniqueness(old, new, dom)
        if locator_error:
            reasons.append(f"{path}: {locator_error}")
    return reasons
