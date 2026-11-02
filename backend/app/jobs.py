"""Background job: execute a HealTest run.

Entry point for the RQ worker (``rq worker --path /srv`` imports this as
``app.jobs``) and for inline dev execution via :func:`queue.enqueue`.

Flow: load run + project → run pytest via :mod:`app.runner` → persist one
``TestResult`` per failure → run the (mock, until Phase 3) heal loop →
attach collected artifacts to the attempt → finalize the run.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import orchestrator, patcher, runner, store, vision
from .models import Artifact, Attempt, Patch, Project, Run, SessionLocal, TestResult
from .store import patch_path, save_text

TEST_CODE_CAP = 8_000
DOM_CAP = 100_000
REQUIRED_EVIDENCE = {
    "error_trace",
    "failing_location",
    "page_url",
    "screenshot",
    "dom",
    "console",
    "elements",
}
log = logging.getLogger("healtest.jobs")


def execute_benchmark(benchmark_id: int, config: dict) -> dict:
    """Run a benchmark from the API queue or inline development mode."""
    repository_root = Path(__file__).resolve().parents[2]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    from benchmark.runner import execute_benchmark_job

    return execute_benchmark_job(benchmark_id, config)


def _read_capped(path: str, cap: int) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(cap)
    except OSError:
        return ""


def _raw_dir(run_id: int) -> str:
    out = os.path.join(store.store_root(), "raw", f"run-{run_id}")
    os.makedirs(out, exist_ok=True)
    return out


def _persist_evidence(db, failure, attempt_id: int) -> list[str]:
    if not failure.artifact_dir:
        return ["evidence plugin did not produce an artifact directory"]

    files = failure.manifest.get("files") or {}
    errors = list(failure.manifest.get("capture_errors") or [])
    for kind, filename in files.items():
        if not filename:
            continue
        src = os.path.join(failure.artifact_dir, filename)
        try:
            stored = store.ingest_file(src, kind, attempt_id)
        except (OSError, KeyError) as exc:
            errors.append(f"could not persist {kind} evidence: {exc}")
            continue
        db.add(Artifact(attempt_id=attempt_id, type=kind, path=stored))

    missing = REQUIRED_EVIDENCE.difference(
        kind for kind, filename in files.items() if filename
    )
    if missing:
        errors.append(f"required evidence missing: {', '.join(sorted(missing))}")
    return errors


def _failure_test_file(test_dir: str, failing_file: str) -> tuple[str, str] | None:
    if not failing_file:
        return None
    root = os.path.realpath(test_dir)
    reported = failing_file.replace("\\", "/")
    if reported.startswith("/job/tests/"):
        relative = reported[len("/job/tests/") :]
    else:
        if os.path.isabs(failing_file):
            candidate = os.path.realpath(failing_file)
            try:
                relative = os.path.relpath(candidate, root)
            except ValueError:
                return None
            if relative.startswith(".." + os.sep) or relative == "..":
                return None
        else:
            relative = reported
            if not os.path.isfile(os.path.join(root, relative)):
                relative = os.path.basename(reported)
    relative = os.path.normpath(relative)
    if (
        os.path.isabs(relative)
        or relative in ("", ".", "..")
        or relative.startswith(".." + os.sep)
    ):
        return None
    absolute = os.path.realpath(os.path.join(root, relative))
    try:
        if os.path.commonpath((root, absolute)) != root or not os.path.isfile(absolute):
            return None
    except ValueError:
        return None
    return absolute, relative.replace("\\", "/")


def _verify_patch(
    test_dir: str,
    test_target: str,
    app_url: str,
    diff: str,
    run_id: int,
) -> bool | str:
    with tempfile.TemporaryDirectory(prefix=f"healtest-verify-{run_id}-") as work:
        source_root = os.path.join(work, "source")
        os.makedirs(os.path.join(source_root, "tests"))
        shutil.copytree(
            test_dir,
            os.path.join(source_root, "tests"),
            dirs_exist_ok=True,
        )
        try:
            patched_root = patcher.apply_diff_to_copy(source_root, diff)
        except (patcher.PatchError, OSError) as exc:
            return f"verification could not run: {exc}"

        outcomes: list[tuple[bool, str]] = []
        for attempt in range(1, 3):
            try:
                summary = runner.run_tests(
                    os.path.join(patched_root, "tests"),
                    app_url,
                    artifact_dir=os.path.join(work, f"artifacts-{attempt}"),
                    test_target=test_target,
                )
            except (runner.RunnerUnavailable, OSError) as exc:
                outcomes.append((False, f"verification could not run: {exc}"))
                continue
            passed = summary.total == 1 and summary.passed == 1 and not summary.failed
            details = "; ".join(summary.errors) or f"{summary.passed}/{summary.total} tests passed"
            outcomes.append((passed, details))


        if outcomes[0][0] and outcomes[1][0]:
            return True
        if outcomes[0][0] != outcomes[1][0]:
            return (
                "flaky verification: "
                f"attempt 1 {'passed' if outcomes[0][0] else 'failed'} ({outcomes[0][1]}); "
                f"attempt 2 {'passed' if outcomes[1][0] else 'failed'} ({outcomes[1][1]})"
            )
        return "patched test did not pass twice: " + " | ".join(
            outcome[1] for outcome in outcomes
        )


def execute_run(run_id: int) -> dict:
    """Execute run ``run_id`` synchronously; returns a JSON-able summary."""
    db = SessionLocal()
    try:
        run = db.get(Run, run_id)
        if run is None:
            return {"error": f"run {run_id} not found"}
        project = db.get(Project, run.project_id)
        if project is None:
            run.status = "error"
            run.error = f"project {run.project_id} not found"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {"run_id": run_id, "status": "error", "error": run.error}

        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        db.commit()
        artifact_dir = _raw_dir(run_id)
        try:
            summary = runner.run_tests(project.test_path, project.app_url, artifact_dir)
        except runner.RunnerUnavailable as exc:
            run.status = "error"
            run.error = str(exc)
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {"run_id": run_id, "status": "error", "error": str(exc)}

        run.total_tests = summary.total
        run.failed_tests = len(summary.failed)
        if summary.total == 0:
            run.status = "error"
            run.error = "; ".join(summary.errors) or "pytest produced no results"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {"run_id": run_id, "status": "error", "error": run.error}

        healed = 0
        flaky = 0
        evidence_complete = True
        for failure in summary.failed:
            result = TestResult(
                run_id=run.id,
                test_name=failure.test_name,
                status="failed",
                error_text=failure.error_text,
            )
            db.add(result)
            db.commit()
            db.refresh(result)

            dom = ""
            if failure.artifact_dir:
                dom = _read_capped(os.path.join(failure.artifact_dir, "dom.html"), DOM_CAP)
            reported_file = failure.failing_file
            if not reported_file and "::" in failure.test_name:
                reported_file = failure.test_name.split("::", 1)[0]
            test_file_info = _failure_test_file(project.test_path, reported_file)
            test_code = (
                _read_capped(test_file_info[0], TEST_CODE_CAP)
                if test_file_info
                else ""
            )

            started = time.monotonic()
            verifier = None
            visual_locator = None
            diff_file = "tests/test_x.py"
            if test_file_info:
                relative_file = test_file_info[1]
                diff_file = f"tests/{relative_file}"
                test_target = relative_file
                if "::" in failure.test_name:
                    test_target += "::" + failure.test_name.split("::", 1)[1]
                verifier = lambda diff: _verify_patch(
                    project.test_path,
                    test_target,
                    project.app_url,
                    diff,
                    run_id,
                )
            if failure.artifact_dir:
                screenshot_path = os.path.join(failure.artifact_dir, "screenshot.png")
                elements_path = os.path.join(failure.artifact_dir, "elements.json")

                def locate_visual(
                    target_description: str,
                    image_path: str = screenshot_path,
                    element_path: str = elements_path,
                ) -> vision.VisualHit | None:
                    try:
                        with open(element_path, encoding="utf-8") as file:
                            elements = json.load(file)
                    except (OSError, ValueError) as exc:
                        raise vision.VisionError(
                            f"could not read captured DOM element bounds: {exc}"
                        ) from exc
                    if not isinstance(elements, list):
                        raise vision.VisionError(
                            "captured DOM element bounds are not a JSON list"
                        )
                    return vision.locate(image_path, target_description, elements)

                visual_locator = locate_visual
            loop = orchestrator.run_once(
                failure.error_text,
                dom,
                test_code,
                verify=verifier,
                test_file=diff_file,
                max_attempts=min(project.retry_limit, orchestrator.MAX_ATTEMPTS),
                visual_locator=visual_locator,
            )
            duration_ms = int((time.monotonic() - started) * 1000)

            attempt_records = loop.attempts or [
                {
                    "number": 1,
                    "diagnosis": loop.diagnosis,
                    "model_tokens": loop.model_tokens,
                    "fix": None,
                    "safety_reasons": loop.safety_reasons,
                    "verification_error": loop.error_message,
                    "outcome": "escalated",
                }
            ]
            evidence_errors: list[str] = []
            for index, attempt_data in enumerate(attempt_records):
                diagnosis = attempt_data["diagnosis"]
                fix = attempt_data["fix"]
                details = list(attempt_data["safety_reasons"])
                if attempt_data.get("visual_error"):
                    details.append(attempt_data["visual_error"])
                if attempt_data["verification_error"]:
                    details.append(attempt_data["verification_error"])
                attempt = Attempt(
                    test_result_id=result.id,
                    number=attempt_data["number"],
                    failure_class=diagnosis.failure_class if diagnosis else "",
                    confidence=diagnosis.confidence if diagnosis else 0.0,
                    explanation="\n".join(
                        filter(
                            None,
                            (
                                diagnosis.explanation if diagnosis else loop.error_message,
                                *details,
                            ),
                        )
                    ),
                    used_visual=int(diagnosis.needs_visual) if diagnosis else 0,
                    model_tokens=attempt_data["model_tokens"],
                    duration_ms=duration_ms // len(attempt_records),
                    outcome=attempt_data["outcome"],
                )
                db.add(attempt)
                db.commit()
                db.refresh(attempt)

                if index == 0:
                    evidence_errors = _persist_evidence(db, failure, attempt.id)
                    if evidence_errors:
                        evidence_complete = False
                        run.error = "\n".join(
                            filter(
                                None,
                                (
                                    run.error,
                                    f"Evidence capture incomplete for {failure.test_name}: "
                                    + "; ".join(evidence_errors),
                                ),
                            )
                        )

                visual_hit = attempt_data.get("visual_hit")
                if visual_hit is not None:
                    mask_path = store.save_bytes(
                        store.artifact_path("mask", attempt.id),
                        visual_hit.mask_png,
                    )
                    db.add(
                        Artifact(
                            attempt_id=attempt.id,
                            type="mask",
                            path=store.relpath(mask_path),
                        )
                    )
                    db.commit()

                if fix:
                    patch = Patch(
                        test_result_id=result.id,
                        diff=fix.diff,
                        rationale=fix.rationale,
                        status=(
                            "rejected"
                            if attempt_data["safety_reasons"]
                            else "proposed"
                        ),
                    )
                    db.add(patch)
                    db.commit()
                    db.refresh(patch)
                    save_text(patch_path(patch.id), fix.diff)

            if loop.final_state == "DONE" and not evidence_errors:
                healed += 1
                result.final_state = "healed"
                result.status = "healed"
            elif any(attempt["outcome"] == "flaky" for attempt in attempt_records):
                flaky += 1
                result.final_state = "flaky"
                result.status = "flaky"
                result.flaky = 1
            elif loop.diagnosis and loop.diagnosis.failure_class == "suspected real bug":
                result.final_state = "suspected real bug"
            else:
                result.final_state = "needs human review"
            db.commit()

        if not summary.failed:
            run.status = "passed"
        elif not evidence_complete:
            run.status = "needs review"
        else:
            run.status = "healed" if healed == len(summary.failed) else "needs review"
        run.finished_at = datetime.now(timezone.utc)
        run.flaky_tests = flaky
        db.commit()
        return {
            "run_id": run_id,
            "status": run.status,
            "total": summary.total,
            "failed": len(summary.failed),
            "healed": healed,
            "flaky": flaky,
            "mode": summary.mode,
            "duration_s": round(summary.duration_s, 1),
        }
    except Exception as exc:
        db.rollback()
        log.exception("Run %d failed unexpectedly", run_id)
        run = db.get(Run, run_id)
        if run is not None and run.status not in {"healed", "passed", "needs review", "error"}:
            run.status = "error"
            run.error = f"unexpected run failure: {exc}"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
        raise
    finally:
        db.close()
