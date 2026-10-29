import os

from app import patcher


def test_build_diff_roundtrip(tmp_path):
    old = 'driver.find_element(By.ID, "submit").click()\nassert True\n'
    new = 'driver.find_element(By.ID, "submit-btn").click()\nassert True\n'
    diff = patcher.build_diff("tests/test_x.py", old, new)
    assert "submit-btn" in diff
    d = tmp_path / "tests"
    d.mkdir()
    (d / "test_x.py").write_text(old)
    out = patcher.apply_diff_to_copy(str(tmp_path), diff)
    applied = open(os.path.join(out, "tests", "test_x.py")).read()
    assert "submit-btn" in applied
