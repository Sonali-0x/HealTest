import os

from app import patcher
import pytest


def test_build_diff_roundtrip(tmp_path):
    old = 'driver.find_element(By.ID, "submit").click()\nassert True\n'
    new = 'driver.find_element(By.ID, "submit-btn").click()\nassert True\n'
    diff = patcher.build_diff("tests/test_x.py", old, new)
    assert "submit-btn" in diff
    (tmp_path / "test_x.py").write_text(old)
    out = patcher.apply_diff_to_copy(str(tmp_path), diff, strip_prefix="tests")
    applied = open(os.path.join(out, "test_x.py")).read()
    assert "submit-btn" in applied
    assert (tmp_path / "test_x.py").read_text() == old


def test_apply_multiple_hunks_and_files():
    first = "one\nold\nthree\nfour\nold2\nsix\n"
    second = "x = 1\nassert x == 1\n"
    diff = (
        "--- a/tests/test_a.py\n+++ b/tests/test_a.py\n"
        "@@ -2 +2 @@\n-old\n+new\n"
        "@@ -5 +5 @@\n-old2\n+new2\n"
        "--- a/tests/test_b.py\n+++ b/tests/test_b.py\n"
        "@@ -1,2 +1,2 @@\n-x = 1\n+x = 2\n assert x == 1\n"
    )
    updated = patcher.apply_diff_to_files(
        {"test_a.py": first, "test_b.py": second},
        diff,
        strip_prefix="tests",
    )
    assert updated["test_a.py"] == "one\nnew\nthree\nfour\nnew2\nsix\n"
    assert updated["test_b.py"] == "x = 2\nassert x == 1\n"


@pytest.mark.parametrize(
    "diff",
    [
        "--- a/tests/../outside.py\n+++ b/tests/../outside.py\n@@ -1 +1 @@\n-x\n+y\n",
        "--- a/tests/test_x.py\n+++ b/tests/other.py\n@@ -1 +1 @@\n-x\n+y\n",
        "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-x\n+y\n",
    ],
)
def test_apply_rejects_unsafe_paths(diff):
    with pytest.raises(patcher.PatchError):
        patcher.apply_diff_to_files({"test_x.py": "x\n"}, diff, strip_prefix="tests")


def test_apply_rejects_mismatched_context():
    diff = "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n@@ -1 +1 @@\n-old\n+new\n"
    with pytest.raises(patcher.PatchError, match="does not match"):
        patcher.apply_diff_to_files(
            {"test_x.py": "different\n"}, diff, strip_prefix="tests"
        )


def test_build_and_apply_diff_without_final_newline():
    diff = patcher.build_diff("tests/test_x.py", "old", "new")

    assert "\\ No newline at end of file" in diff
    assert patcher.apply_diff_to_files(
        {"test_x.py": "old"}, diff, strip_prefix="tests"
    )["test_x.py"] == "new"
