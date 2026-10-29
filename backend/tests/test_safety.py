from app import safety

DIFF_OK = (
    "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n"
    "@@ -1 +1 @@\n-assert a == 1\n+assert a == 1\n"
)


def test_assertion_drop_rejected():
    old = "def t():\n    assert a == 1\n    assert b == 2\n"
    new = "def t():\n    assert a == 1\n"
    assert safety.check_assertions(old, new) is not None


def test_assertion_kept_ok():
    old = "def t():\n    assert a == 1\n"
    assert safety.check_assertions(old, old) is None


def test_non_test_file_rejected():
    diff = "--- a/app.py\n+++ b/app.py\n@@\n-x\n+y\n"
    assert safety.check_test_dir_only(diff) is not None
    assert safety.check_test_dir_only(DIFF_OK) is None


def test_oversize_rejected():
    assert safety.check_diff_size("x" * 10, limit=5) is not None
    assert safety.check_diff_size("x" * 10, limit=50) is None
