"""Background job: execute a HealTest run.

Entry point for the RQ worker (``rq worker --path /srv`` imports this as
``app.jobs``) and for inline dev execution via :func:`queue.enqueue`.

Flow: load run + project → run pytest via :mod:`app.runner` → persist one
``TestResult`` per failure → run the (mock, until Phase 3) heal loop →
attach collected artifacts to the attempt → finalize the run.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from . import orchestrator, runner, store
from .models import Artifact, Attempt, Patch, Project, Run, SessionLocal, TestResult
from .store import patch_path, save_text

TEST_CODE_CAP = 8_000
DOM_CAP = 100_000


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


def execute_run(run_id: int) -> dict:
    """Execute run ``run_id`` synchronously; returns a JSON-able summary."""
    db = SessionLocal()
    try:
        run = db.get(Run, run_id)
        if run is None:
            return {"error": f"run {run_id} not found"}
        project = db.get(Project, run.project_id)
        if project is None:
            return {"error": f"project {run.project_id} not found"}

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
            test_code = ""
            if failure.failing_file:
                candidates = (
                    [failure.failing_file]
                    if os.path.isabs(failure.failing_file)
                    else [
                        os.path.join(project.test_path, failure.failing_file),
                        failure.failing_file,
                    ]
                )
                for candidate in candidates:
                    test_code = _read_capped(candidate, TEST_CODE_CAP)
                    if test_code:
                        break

            started = time.monotonic()
            loop = orchestrator.run_once(failure.error_text, dom, test_code)
            duration_ms = int((time.monotonic() - started) * 1000)

            attempt = Attempt(
                test_result_id=result.id,
                number=1,
                failure_class=loop.diagnosis.failure_class if loop.diagnosis else "",
                confidence=loop.diagnosis.confidence if loop.diagnosis else 0.0,
                explanation=loop.diagnosis.explanation if loop.diagnosis else "",
                duration_ms=duration_ms,
                outcome="healed" if loop.final_state == "DONE" else "escalated",
            )
            db.add(attempt)
            db.commit()
            db.refresh(attempt)

            if failure.artifact_dir:
                for kind, filename in (failure.manifest.get("files") or {}).items():
                    if not filename:
                        continue
                    src = os.path.join(failure.artifact_dir, filename)
                    try:
                        stored = store.ingest_file(src, kind, attempt.id)
                    except (OSError, KeyError):
                        continue
                    db.add(Artifact(attempt_id=attempt.id, type=kind, path=stored))
                db.commit()

            if loop.fix:
                patch = Patch(
                    test_result_id=result.id, diff=loop.fix.diff, rationale=loop.fix.rationale
                )
                db.add(patch)
                db.commit()
                db.refresh(patch)
                save_text(patch_path(patch.id), loop.fix.diff)

            if loop.final_state == "DONE":
                healed += 1
                result.final_state = "healed"
                result.status = "healed"
            else:
                result.final_state = "needs human review"
            db.commit()

        if not summary.failed:
            run.status = "passed"
        else:
            run.status = "healed" if healed == len(summary.failed) else "needs review"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return {
            "run_id": run_id,
            "status": run.status,
            "total": summary.total,
            "failed": len(summary.failed),
            "healed": healed,
            "mode": summary.mode,
            "duration_s": round(summary.duration_s, 1),
        }
    finally:
        db.close()
