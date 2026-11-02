import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import jobs, models, runner


def test_execute_run_verifies_patch_and_persists_three_stage_result(
    tmp_path, monkeypatch
):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(engine)
    db = Session(engine)
    test_dir = tmp_path / "project" / "tests"
    test_dir.mkdir(parents=True)
    test_code = (
        'driver.find_element(By.ID, "submit").click()\n'
        'assert result == "ok"\n'
    )
    (test_dir / "test_form.py").write_text(test_code, encoding="utf-8")
    project = models.Project(
        name="sample",
        app_url="http://sample-apps:8901",
        test_path=str(test_dir),
        retry_limit=3,
    )
    db.add(project)
    db.commit()
    run = models.Run(project_id=project.id, status="running")
    db.add(run)
    db.commit()

    capture = tmp_path / "capture"
    capture.mkdir()
    files = {
        "error_trace": "error.txt",
        "failing_location": "location.txt",
        "page_url": "url.txt",
        "screenshot": "shot.png",
        "dom": "dom.html",
        "console": "console.log",
        "elements": "elements.json",
        "manifest": "manifest.json",
    }
    for kind, filename in files.items():
        path = capture / filename
        if kind == "dom":
            path.write_text('<button id="submit-btn">Submit</button>', encoding="utf-8")
        else:
            path.write_text(kind, encoding="utf-8")
    (capture / "manifest.json").write_text(
        json.dumps({"files": files, "capture_errors": []}),
        encoding="utf-8",
    )
    failure = runner.TestFailure(
        test_name="tests/test_form.py::test_submit",
        error_text="NoSuchElementException: submit",
        failing_file="/job/tests/test_form.py",
        failing_line="1",
        artifact_dir=str(capture),
        manifest={"files": files, "capture_errors": []},
    )
    calls = []

    def run_tests(test_path, app_url, artifact_dir=None, timeout_s=None, test_target=None):
        calls.append((test_path, test_target))
        if len(calls) == 1:
            return runner.RunSummary(total=1, failed=[failure], mode="local")
        patched = open(test_path + "/test_form.py", encoding="utf-8").read()
        assert 'By.ID, "submit-btn"' in patched
        assert app_url == "http://sample-apps:8901"
        assert test_target == "test_form.py::test_submit"
        return runner.RunSummary(total=1, passed=1, mode="local")

    monkeypatch.setattr(jobs, "SessionLocal", lambda: db)
    monkeypatch.setattr(runner, "run_tests", run_tests)
    monkeypatch.setenv("HEALTEST_MOCK_MODE", "1")
    monkeypatch.setenv("HEALTEST_DATA_DIR", str(tmp_path / "store"))

    summary = jobs.execute_run(run.id)

    assert summary["status"] == "healed"
    assert summary["healed"] == 1
    assert len(calls) == 3
    saved_run = db.get(models.Run, run.id)
    result = db.query(models.TestResult).filter_by(run_id=run.id).one()
    attempt = db.query(models.Attempt).filter_by(test_result_id=result.id).one()
    patch = db.query(models.Patch).filter_by(test_result_id=result.id).one()
    assert saved_run.status == "healed"
    assert result.final_state == "healed"
    assert result.flaky == 0
    assert attempt.outcome == "healed"
    assert patch.status == "proposed"
    assert "submit-btn" in patch.diff
    assert db.query(models.Artifact).filter_by(attempt_id=attempt.id).count() == len(files)
    db.close()
    engine.dispose()


def test_patch_verification_reports_inconsistent_double_run_as_flaky(
    tmp_path, monkeypatch
):
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    original = 'def test_result():\n    assert "before" == "after"\n'
    updated = original.replace('"before"', '"after"')
    (test_dir / "test_plain.py").write_text(original, encoding="utf-8")
    diff = jobs.patcher.build_diff("tests/test_plain.py", original, updated)
    calls = []

    def run_tests(*args, **kwargs):
        calls.append(kwargs["artifact_dir"])
        if len(calls) == 1:
            return runner.RunSummary(total=1, passed=1, mode="local")
        return runner.RunSummary(
            total=1,
            failed=[
                runner.TestFailure(
                    test_name="tests/test_plain.py::test_result",
                    error_text="intermittent failure",
                )
            ],
            mode="local",
        )

    monkeypatch.setattr(runner, "run_tests", run_tests)
    outcome = jobs._verify_patch(
        str(test_dir), "test_plain.py::test_result", "http://localhost", diff, 100
    )

    assert isinstance(outcome, str)
    assert outcome.startswith("flaky verification:")
    assert len(calls) == 2
    assert calls[0] != calls[1]


def test_real_local_pytest_rerun_uses_patched_temporary_copy(tmp_path, monkeypatch):
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    original = 'def test_result():\n    assert "before" == "after"\n'
    updated = original.replace('"before"', '"after"')
    (test_dir / "test_plain.py").write_text(original, encoding="utf-8")
    diff = jobs.patcher.build_diff("tests/test_plain.py", original, updated)
    monkeypatch.setenv("HEALTEST_SANDBOX", "local")

    outcome = jobs._verify_patch(
        str(test_dir),
        "test_plain.py::test_result",
        "http://localhost",
        diff,
        run_id=99,
    )

    assert outcome is True
    assert (test_dir / "test_plain.py").read_text(encoding="utf-8") == original
