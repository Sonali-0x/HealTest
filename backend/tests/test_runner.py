import io
import json
import tarfile

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


def test_auto_mode_never_falls_back_to_local(tmp_path, monkeypatch):
    monkeypatch.delenv("HEALTEST_SANDBOX", raising=False)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        runner,
        "_run_local",
        lambda *args, **kwargs: pytest.fail("auto mode must not execute locally"),
    )
    suite = tmp_path / "tests"
    suite.mkdir()
    (suite / "test_sample.py").write_text("def test_sample(): pass\n", encoding="utf-8")

    assert runner.resolve_mode() == "docker"
    with pytest.raises(runner.RunnerUnavailable, match="refusing to run tests"):
        runner.run_tests(str(suite), "http://sample-apps:8901")


def test_run_tests_validates_target_is_inside_suite(tmp_path):
    suite = tmp_path / "tests"
    suite.mkdir()
    (suite / "test_ok.py").write_text("def test_ok(): pass\n")
    (tmp_path / "outside.py").write_text("def test_outside(): pass\n")

    with pytest.raises(runner.RunnerUnavailable, match="inside the test directory"):
        runner.run_tests(str(suite), "http://localhost", test_target="../outside.py")


def test_run_tests_runs_only_the_requested_test_file(tmp_path, monkeypatch):
    suite = tmp_path / "tests"
    suite.mkdir()
    target = suite / "test_ok.py"
    target.write_text("def test_ok(): pass\n")
    captured = {}

    def fake_run_local(test_dir, app_url, artifact_dir, report_log, timeout_s, summary, test_target=None):
        captured.update(test_dir=test_dir, test_target=test_target)

    monkeypatch.setattr(runner, "resolve_mode", lambda: "local")
    monkeypatch.setattr(runner, "_run_local", fake_run_local)
    monkeypatch.setattr(runner, "parse_report_log", lambda path: (1, 1, []))

    result = runner.run_tests(str(suite), "http://localhost", test_target="test_ok.py")

    assert result.total == 1
    assert captured["test_dir"] == str(suite)
    assert captured["test_target"] == "test_ok.py"


def test_plugin_dir_from_env(tmp_path, monkeypatch):
    (tmp_path / "conftest_plugin.py").write_text("# stub")
    monkeypatch.setenv("HEALTEST_WORKER_DIR", str(tmp_path))
    assert runner.plugin_dir() == str(tmp_path)


def test_docker_target_requires_explicit_host_allowlist(monkeypatch):
    monkeypatch.setenv("HEALTEST_SANDBOX_ALLOWED_HOSTS", "sample-apps, internal-app")
    runner._validate_sandbox_target("http://sample-apps:8901/todo/")
    with pytest.raises(runner.RunnerUnavailable, match="ALLOWED_HOSTS"):
        runner._validate_sandbox_target("http://other.example/")


def test_docker_endpoint_requires_remote_tls(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    monkeypatch.setenv("DOCKER_CERT_PATH", str(tmp_path))

    with pytest.raises(runner.RunnerUnavailable, match="remote tcp"):
        runner._validate_docker_endpoint()

    monkeypatch.setenv("DOCKER_HOST", "tcp://sandbox-daemon.example:2376")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "0")
    with pytest.raises(runner.RunnerUnavailable, match="TLS verification"):
        runner._validate_docker_endpoint()

    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    with pytest.raises(runner.RunnerUnavailable, match="ca.pem"):
        runner._validate_docker_endpoint()

    for name in ("ca.pem", "cert.pem", "key.pem"):
        (tmp_path / name).write_text("test certificate material", encoding="utf-8")
    runner._validate_docker_endpoint()


def _configure_remote_docker(monkeypatch, cert_dir):
    for name in ("ca.pem", "cert.pem", "key.pem"):
        (cert_dir / name).write_text("test certificate material", encoding="utf-8")
    monkeypatch.setenv("DOCKER_HOST", "tcp://sandbox-daemon.example:2376")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    monkeypatch.setenv("DOCKER_CERT_PATH", str(cert_dir))


def test_docker_runner_uses_ephemeral_isolated_container(tmp_path, monkeypatch):
    calls = []

    def fake_docker_run(args, timeout_s=None, input_data=None):
        calls.append((args, timeout_s, input_data))
        output = "0\n" if args[0] == "exec" and "pytest" in args else ""
        return runner.subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    def fake_docker_run_bytes(args, timeout_s=None):
        calls.append((args, timeout_s, None))
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w"):
            pass
        return runner.subprocess.CompletedProcess(
            args, 0, stdout=archive.getvalue(), stderr=b""
        )

    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(runner, "_docker_run", fake_docker_run)
    monkeypatch.setattr(runner, "_docker_run_bytes", fake_docker_run_bytes)
    monkeypatch.setenv("HEALTEST_SANDBOX_ALLOWED_HOSTS", "sample-apps")
    monkeypatch.setenv("HEALTEST_SANDBOX_NETWORK", "healtest-sandbox")
    monkeypatch.setenv("HEALTEST_SANDBOX_CPUS", "1.5")
    monkeypatch.setenv("HEALTEST_SANDBOX_MEMORY", "768m")
    cert_dir = tmp_path / "docker-certs"
    cert_dir.mkdir()
    _configure_remote_docker(monkeypatch, cert_dir)
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text("def test_sample(): pass\n")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    summary = runner.RunSummary()

    runner._run_docker(
        str(tests), "http://sample-apps:8901/todo/", str(artifacts),
        30, summary,
    )

    create_args = calls[0][0]
    assert create_args[0] == "create"
    assert "--read-only" in create_args
    assert "--cap-drop=ALL" in create_args
    assert "--cpus=1.5" in create_args
    assert "--memory=768m" in create_args
    assert "--network=healtest-sandbox" in create_args
    assert not any(arg == "-v" for arg in create_args)
    assert create_args[-2:] == ["sleep", "infinity"]
    assert [call[0][0] for call in calls] == [
        "create", "start", "exec", "exec", "exec", "exec", "rm"
    ]
    assert calls[3][0][1] == "-i"
    assert calls[3][2]
    with tarfile.open(fileobj=io.BytesIO(calls[3][2]), mode="r:") as archive:
        assert archive.getnames() == ["test_sample.py"]
    assert calls[4][1] == 30
    assert calls[5][0][1] == calls[0][0][2]
    assert "followlinks=False" in calls[5][0][-1]
    assert summary.errors == []
    assert not summary.timed_out


def test_docker_runner_rejects_non_allowlisted_target(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setenv("HEALTEST_SANDBOX_ALLOWED_HOSTS", "sample-apps")
    with pytest.raises(runner.RunnerUnavailable, match="ALLOWED_HOSTS"):
        runner._run_docker(
            str(tmp_path), "http://external.example/", str(tmp_path), 30,
            runner.RunSummary(),
        )


def test_docker_runner_kills_timed_out_container(tmp_path, monkeypatch):
    calls = []

    def fake_docker_run(args, timeout_s=None, input_data=None):
        calls.append(args[0])
        if args[0] == "exec" and "pytest" in args:
            raise runner.subprocess.TimeoutExpired(["docker", *args], timeout_s)
        return runner.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(runner, "_docker_run", fake_docker_run)
    monkeypatch.setenv("HEALTEST_SANDBOX_ALLOWED_HOSTS", "sample-apps")
    cert_dir = tmp_path / "docker-certs"
    cert_dir.mkdir()
    _configure_remote_docker(monkeypatch, cert_dir)
    summary = runner.RunSummary()

    runner._run_docker(
        str(tmp_path), "http://sample-apps:8901/", str(tmp_path), 2, summary
    )

    assert summary.timed_out
    assert summary.errors == ["sandbox exceeded wall-clock limit of 2s"]
    assert calls == ["create", "start", "exec", "exec", "exec", "kill", "rm"]


def test_extract_artifact_archive_rejects_parent_paths(tmp_path):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        entry = tarfile.TarInfo("../outside.txt")
        entry.size = 1
        tar.addfile(entry, io.BytesIO(b"x"))

    with pytest.raises(runner.RunnerUnavailable, match="unsafe path"):
        runner._extract_artifact_archive(archive.getvalue(), str(tmp_path / "artifacts"))

    assert not (tmp_path / "outside.txt").exists()
