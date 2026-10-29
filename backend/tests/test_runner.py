import json

import pytest

from app import runner


def _write_report_log(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def _report(nodeid, when, outcome, longrepr=None):
    entry = {"nodeid": nodeid, "when": when, "outcome": outcome}
    if longrepr is not None:
        entry["longrepr"] = longrepr
    return entry


def test_parse_report_log_counts_and_failures(tmp_path):
    log = tmp_path / "report.jsonl"
    _write_report_log(str(log), [
        _report("tests/test_a.py::test_1", "setup", "passed"),
        _report("tests/test_a.py::test_1", "call", "passed"),
        _report("tests/test_a.py::test_2", "call", "failed", "tests/test_a.py:9: E assert False"),
        _report("tests/test_a.py::test_3", "call", "skipped"),
        {"$report_type": "CollectReport", "outcome": "passed"},
        _report("tests/test_a.py::test_2", "teardown", "passed"),
    ])
    total, passed, failures = runner.parse_report_log(str(log))
    assert total == 3
    assert passed == 1
    assert len(failures) == 1
    assert failures[0]["nodeid"] == "tests/test_a.py::test_2"
    assert "assert False" in failures[0]["longrepr"]


def test_parse_report_log_missing_and_malformed(tmp_path):
    assert runner.parse_report_log(str(tmp_path / "nope.jsonl")) == (0, 0, [])
    log = tmp_path / "bad.jsonl"
    log.write_text("not json\n" + json.dumps(_report("t", "call", "passed")) + "\n")
    assert runner.parse_report_log(str(log)) == (1, 1, [])


def test_parse_report_log_legacy_shape(tmp_path):
    log = tmp_path / "report.jsonl"
    _write_report_log(str(log), [
        {"$report_type": "TestReport", "nodeid": "t", "when": "call",
         "outcome": "failed", "longrepr": "boom"},
    ])
    total, passed, failures = runner.parse_report_log(str(log))
    assert (total, passed) == (1, 0)
    assert failures[0]["longrepr"] == "boom"


def test_parse_report_log_dict_longrepr(tmp_path):
    log = tmp_path / "report.jsonl"
    _write_report_log(str(log), [
        _report("tests/test_a.py::test_x", "call", "failed", {"chain": ["boom"]}),
    ])
    total, passed, failures = runner.parse_report_log(str(log))
    assert total == 1 and passed == 0
    assert "boom" in failures[0]["longrepr"]


def test_safe_name():
    assert runner.safe_name("tests/test_a.py::test_x[param-1]") == "tests_test_a.py_test_x_param-1"
    assert runner.safe_name("!!!") == "test"


def test_build_failures_attaches_manifest(tmp_path):
    raw = [{"nodeid": "tests/test_a.py::test_x", "longrepr": "tests/test_a.py:5: E boom"}]
    outdir = tmp_path / runner.safe_name(raw[0]["nodeid"])
    outdir.mkdir()
    (outdir / "dom.html").write_text("<html/>")
    (outdir / "manifest.json").write_text(json.dumps({"test_name": raw[0]["nodeid"], "url": "http://x/"}))
    failures = runner._build_failures(raw, str(tmp_path))
    assert len(failures) == 1
    assert failures[0].failing_file == "tests/test_a.py"
    assert failures[0].failing_line == "5"
    assert failures[0].artifact_dir == str(outdir)
    assert failures[0].manifest["url"] == "http://x/"


def test_run_tests_missing_dir():
    with pytest.raises(runner.RunnerUnavailable):
        runner.run_tests("/nonexistent-dir-xyz", "http://localhost/")


def test_resolve_mode_explicit(monkeypatch):
    monkeypatch.setenv("HEALTEST_SANDBOX", "local")
    assert runner.resolve_mode() == "local"
    monkeypatch.setenv("HEALTEST_SANDBOX", "bogus")
    with pytest.raises(runner.RunnerUnavailable):
        runner.resolve_mode()


def test_plugin_dir_from_env(tmp_path, monkeypatch):
    (tmp_path / "conftest_plugin.py").write_text("# stub")
    monkeypatch.setenv("HEALTEST_WORKER_DIR", str(tmp_path))
    assert runner.plugin_dir() == str(tmp_path)
