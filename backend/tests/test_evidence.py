from app import evidence


def test_trim_dom_strips_scripts_and_styles():
    html = "<html><head><style>.x{color:red}</style></head><body><script>alert(1)</script><p>hi</p></body></html>"
    out = evidence.trim_dom(html)
    assert "alert(1)" not in out
    assert "color:red" not in out
    assert "hi" in out


def test_trim_dom_caps_size():
    html = "<p>" + "x" * 50_000 + "</p>"
    assert len(evidence.trim_dom(html)) == evidence.MAX_DOM_CHARS


def test_trim_dom_focus_window():
    html = "<p>" + "a" * 20_000 + "NEEDLE" + "b" * 20_000 + "</p>"
    out = evidence.trim_dom(html, focus="NEEDLE")
    assert "NEEDLE" in out
    assert len(out) == evidence.MAX_DOM_CHARS


def test_failing_step():
    tb = 'test_x.py:12: in test_y\nE   NoSuchElementException: #submit\nE   extra line'
    assert evidence.failing_step(tb) == "NoSuchElementException: #submit"
    assert evidence.failing_step("no error lines") == ""


def test_parse_failing_location_pytest_format():
    tb = "tests/test_form.py:42: in test_submit\nE   assert False"
    assert evidence.parse_failing_location(tb) == ("tests/test_form.py", "42")


def test_parse_failing_location_cpython_format():
    tb = '  File "/app/tests/test_shop.py", line 17, in test_add\n    assert x'
    assert evidence.parse_failing_location(tb) == ("/app/tests/test_shop.py", "17")


def test_parse_failing_location_prefers_test_frame():
    tb = (
        '  File "/usr/lib/python/site-packages/selenium/webdriver.py", line 900, in find\n'
        '  File "/job/tests/test_todo.py", line 25, in test_add\n'
    )
    assert evidence.parse_failing_location(tb) == ("/job/tests/test_todo.py", "25")


def test_parse_failing_location_empty():
    assert evidence.parse_failing_location("") == ("", "")
    assert evidence.parse_failing_location("all green") == ("", "")


def test_extract_locator_hint():
    err = "selenium.common.exceptions.NoSuchElementException: Message: no such element: #submit-btn"
    assert "#submit-btn" in evidence.extract_locator_hint(err)
    assert evidence.extract_locator_hint('failed on (By.ID, "login")') == 'By.ID, "login"'
    assert evidence.extract_locator_hint("assert False") == ""
