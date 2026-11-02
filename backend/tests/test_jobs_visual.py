import json
import io
import os

from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import jobs, models, patcher, runner, store
from app.ai_gateway import client
from app.ai_gateway.schemas import Diagnosis, Fix
from app.vision import VisualHit


def _png(size: tuple[int, int], box: tuple[int, int, int, int] | None = None) -> bytes:
    image = Image.new("L", size, 0)
    if box:
        ImageDraw.Draw(image).rectangle(
            (box[0], box[1], box[2] - 1, box[3] - 1),
            fill=255,
        )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_execute_run_passes_visual_hit_to_fix_and_persists_mask(
    tmp_path, monkeypatch
):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(engine)
    db = Session(engine)
    try:
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
        )
        db.add(project)
        db.commit()
        run = models.Run(project_id=project.id, status="running")
        db.add(run)
        db.commit()

        capture = tmp_path / "capture"
        capture.mkdir()
        mask_png = _png((100, 80), (20, 20, 50, 50))
        screenshot_path = capture / "screenshot.png"
        screenshot_path.write_bytes(_png((100, 80)))
        (capture / "dom.html").write_text(
            '<button id="submit-btn">Submit</button>',
            encoding="utf-8",
        )
        elements = [
            {"tag": "button", "id": "submit-btn", "text": "Submit", "x": 20, "y": 20, "w": 30, "h": 30}
        ]
        files = {
            "error_trace": "error_trace.txt",
            "failing_location": "failing_location.txt",
            "page_url": "page_url.txt",
            "screenshot": "screenshot.png",
            "dom": "dom.html",
            "console": "console.log",
            "elements": "elements.json",
            "manifest": "manifest.json",
        }
        for kind, filename in files.items():
            path = capture / filename
            if kind in {"screenshot", "dom"}:
                continue
            if kind == "elements":
                path.write_text(json.dumps(elements), encoding="utf-8")
            elif kind == "manifest":
                path.write_text(
                    json.dumps({"files": files, "capture_errors": []}),
                    encoding="utf-8",
                )
            else:
                path.write_text(kind, encoding="utf-8")
        failure = runner.TestFailure(
            test_name="tests/test_form.py::test_submit",
            error_text="NoSuchElementException: submit",
            failing_file="/job/tests/test_form.py",
            artifact_dir=str(capture),
            manifest={"files": files, "capture_errors": []},
        )
        calls = []

        def run_tests(test_path, app_url, artifact_dir=None, timeout_s=None, test_target=None):
            calls.append(test_target)
            if len(calls) == 1:
                return runner.RunSummary(total=1, failed=[failure], mode="local")
            assert 'By.ID, "submit-btn"' in open(
                os.path.join(test_path, "test_form.py"),
                encoding="utf-8",
            ).read()
            return runner.RunSummary(total=1, passed=1, mode="local")

        diagnosis = Diagnosis(
            failure_class="element moved",
            confidence=0.93,
            explanation="The target moved.",
            target_description="blue Submit button",
            needs_visual=True,
        )
        fix = Fix(
            diff=patcher.build_diff(
                "tests/test_form.py",
                test_code,
                test_code.replace('"submit"', '"submit-btn"'),
            ),
            rationale="Use the visually identified button.",
        )
        hit = VisualHit(
            dom_index=0,
            iou=1.0,
            element=elements[0],
            bbox=(20, 20, 50, 50),
            mask_png=mask_png,
        )
        locate_calls = []
        fix_candidates = []

        def locate(path, target, captured_elements):
            locate_calls.append((path, target, captured_elements))
            return hit

        def fix_with_usage(*args, element="", **kwargs):
            fix_candidates.append(json.loads(element))
            return fix, 9

        monkeypatch.setattr(jobs, "SessionLocal", lambda: db)
        monkeypatch.setattr(runner, "run_tests", run_tests)
        monkeypatch.setattr(jobs.vision, "locate", locate)
        monkeypatch.setattr(client, "diagnose_with_usage", lambda *args: (diagnosis, 4))
        monkeypatch.setattr(client, "fix_with_usage", fix_with_usage)
        monkeypatch.setenv("HEALTEST_DATA_DIR", str(tmp_path / "store"))

        summary = jobs.execute_run(run.id)

        assert summary["status"] == "healed"
        assert locate_calls == [(str(screenshot_path), "blue Submit button", elements)]
        assert fix_candidates[0]["element"]["id"] == "submit-btn"
        assert fix_candidates[0]["intersection_over_union"] == 1.0
        assert calls == [
            None,
            "test_form.py::test_submit",
            "test_form.py::test_submit",
        ]
        attempt = db.query(models.Attempt).one()
        mask_artifact = db.query(models.Artifact).filter_by(
            attempt_id=attempt.id,
            type="mask",
        ).one()
        assert open(store.resolve(mask_artifact.path), "rb").read() == mask_png
    finally:
        db.close()
        engine.dispose()
