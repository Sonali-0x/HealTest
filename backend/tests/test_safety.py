import pytest

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


def test_assertion_cannot_be_weakened_without_changing_the_count():
    old = "def t():\n    assert result == 'success'\n"
    new = "def t():\n    assert result\n"
    assert safety.check_assertions(old, new) is not None


def test_unittest_assertions_are_preserved_exactly():
    old = "self.assertEqual(response.status_code, 200)\n"
    new = "self.assertEqual(response.status_code, 400)\n"
    assert safety.check_assertions(old, new) is not None


def test_non_test_file_rejected():
    diff = "--- a/app.py\n+++ b/app.py\n@@\n-x\n+y\n"
    assert safety.check_test_dir_only(diff) is not None
    assert safety.check_test_dir_only(DIFF_OK) is None


@pytest.mark.parametrize(
    "path",
    ["tests/../app.py", "../tests/test_x.py", "/tests/test_x.py", "tests\\..\\app.py"],
)
def test_unsafe_diff_paths_are_rejected(path):
    diff = f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-x\n+y\n"
    assert safety.check_test_dir_only(diff) is not None


def test_oversize_rejected():
    assert safety.check_diff_size("x" * 10, limit=5) is not None
    assert safety.check_diff_size("x" * 10, limit=50) is None


def test_new_locator_must_match_exactly_one_element():
    old = 'driver.find_element(By.ID, "submit").click()\nassert True\n'
    new = 'driver.find_element(By.ID, "submit-btn").click()\nassert True\n'
    dom = '<button id="submit-btn">Submit</button>'

    assert safety.check_locator_uniqueness(old, new, dom) is None
    assert "matches 0 elements" in safety.check_locator_uniqueness(old, new, "<html/>")
    assert "matches 2 elements" in safety.check_locator_uniqueness(
        old, new, dom + dom
    )


def test_new_css_locator_is_checked_against_saved_dom():
    old = 'driver.find_element(By.CSS_SELECTOR, "#old").click()\n'
    new = 'driver.find_element(By.CSS_SELECTOR, "button.submit").click()\n'

    assert safety.check_locator_uniqueness(
        old, new, '<button class="submit">Continue</button>'
    ) is None
    assert "matches 0 elements" in safety.check_locator_uniqueness(
        old, new, "<button>Continue</button>"
    )


def test_new_dynamic_locator_fails_closed():
    old = 'driver.find_element(By.ID, "old").click()\n'
    new = "driver.find_element(By.ID, locator).click()\n"

    assert "dynamic locator" in safety.check_locator_uniqueness(
        old, new, '<button id="submit-btn"></button>'
    )


def test_check_all_applies_diff_and_checks_assertions_and_locator():
    old = 'driver.find_element(By.ID, "submit").click()\nassert result == "ok"\n'
    new = 'driver.find_element(By.ID, "submit-btn").click()\nassert result == "ok"\n'
    diff = "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n" + (
        "@@ -1,2 +1,2 @@\n"
        '-driver.find_element(By.ID, "submit").click()\n'
        '+driver.find_element(By.ID, "submit-btn").click()\n'
        ' assert result == "ok"\n'
    )

    assert safety.check_all(
        diff,
        {"test_x.py": old},
        dom='<button id="submit-btn">Submit</button>',
    ) == []
