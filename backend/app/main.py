"""FastAPI service: REST + SSE (PRD section 8.2)."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from . import jobs, models, patcher, store
from .hardening import JobRateLimiter, RequestBodyLimitMiddleware
from .models import get_db, init_db
from .queue import QueueUnavailable, enqueue, job_timeout_seconds
from .schemas import BenchmarkCreate, PatchOut, ProjectCreate, ProjectOut, RunOut

log = logging.getLogger("healtest.api")


def _positive_setting(name: str, default: int) -> int:
    raw_value = os.environ.get(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


app = FastAPI(title="HealTest API", version="0.0.1")
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=_positive_setting("HEALTEST_MAX_REQUEST_BYTES", 1_048_576),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get(
            "HEALTEST_CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)
_job_rate_limiter = JobRateLimiter(
    max_requests=_positive_setting("HEALTEST_JOB_RATE_LIMIT", 10),
    window_seconds=_positive_setting("HEALTEST_JOB_RATE_WINDOW_S", 60),
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _auth(authorization: str | None = Header(default=None)) -> None:
    token = os.environ.get("HEALTEST_API_TOKEN", "")
    if not token:
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _expire_stale_jobs(db: Session) -> None:
    stale_after = job_timeout_seconds() + _positive_setting(
        "HEALTEST_STALE_JOB_GRACE_S", 60
    )
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after)
    message = f"job exceeded its {job_timeout_seconds()}s execution limit"
    changed = False
    for row in db.query(models.Run).filter(
        models.Run.status.in_(("queued", "running")),
        models.Run.started_at < cutoff,
    ):
        row.status = "error"
        row.error = message
        row.finished_at = datetime.now(timezone.utc)
        changed = True
    for row in db.query(models.Benchmark).filter(
        models.Benchmark.status.in_(("queued", "running")),
        models.Benchmark.started_at < cutoff,
    ):
        row.status = "failed"
        row.error = message
        row.finished_at = datetime.now(timezone.utc)
        changed = True
    if changed:
        db.commit()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/projects", dependencies=[Depends(_auth)])
def create_project(body: ProjectCreate, db: Session = Depends(get_db)) -> ProjectOut:
    row = models.Project(
        name=body.name, app_url=body.app_url, test_path=body.test_path, retry_limit=body.retry_limit
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return ProjectOut(
        id=row.id, name=row.name, app_url=row.app_url, test_path=row.test_path, retry_limit=row.retry_limit
    )


@app.get("/projects", dependencies=[Depends(_auth)])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectOut]:
    rows = db.query(models.Project).all()
    return [
        ProjectOut(id=r.id, name=r.name, app_url=r.app_url, test_path=r.test_path, retry_limit=r.retry_limit)
        for r in rows
    ]


def _run_out(row: models.Run) -> RunOut:
    return RunOut(
        id=row.id, project_id=row.project_id, status=row.status,
        total_tests=row.total_tests, failed_tests=row.failed_tests,
        flaky_tests=row.flaky_tests,
    )


@app.post(
    "/projects/{project_id}/runs",
    dependencies=[Depends(_auth), Depends(_job_rate_limiter)],
)
def start_run(project_id: int, db: Session = Depends(get_db)) -> RunOut:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    run = models.Run(project_id=project_id, status="queued")
    db.add(run)
    db.commit()
    db.refresh(run)
    # Phase 1: real execution via the worker (RQ job, or inline when Redis
    # is unavailable). The job runs pytest, persists results + artifacts,
    # and drives the heal loop per failure.
    try:
        enqueue(jobs.execute_run, run.id)
    except QueueUnavailable as exc:
        log.error("Run %d could not be queued: %s", run.id, exc)
        run.status = "error"
        run.error = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    db.refresh(run)
    return _run_out(run)


@app.get("/runs", dependencies=[Depends(_auth)])
def list_runs(db: Session = Depends(get_db)) -> list[RunOut]:
    _expire_stale_jobs(db)
    return [_run_out(r) for r in db.query(models.Run).all()]


@app.get("/runs/{run_id}", dependencies=[Depends(_auth)])
def run_detail(run_id: int, db: Session = Depends(get_db)) -> dict:
    _expire_stale_jobs(db)
    run = db.get(models.Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    results = db.query(models.TestResult).filter_by(run_id=run_id).all()
    out = []
    for res in results:
        attempts = db.query(models.Attempt).filter_by(test_result_id=res.id).all()
        patches = db.query(models.Patch).filter_by(test_result_id=res.id).all()
        attempt_out = []
        for a in attempts:
            artifacts = db.query(models.Artifact).filter_by(attempt_id=a.id).all()
            attempt_out.append(
                {
                    "number": a.number, "failure_class": a.failure_class,
                    "confidence": a.confidence, "outcome": a.outcome,
                    "explanation": a.explanation, "model_tokens": a.model_tokens,
                    "duration_ms": a.duration_ms,
                    "artifacts": [
                        {
                            "id": art.id,
                            "type": art.type,
                            "path": art.path,
                            "url": f"/artifacts/{art.id}",
                        }
                        for art in artifacts
                    ],
                }
            )
        out.append(
            {
                "id": res.id, "test_name": res.test_name, "status": res.status,
                "final_state": res.final_state, "flaky": bool(res.flaky),
                "error_text": res.error_text,
                "attempts": attempt_out,
                "patches": [
                    {
                        "id": p.id,
                        "status": p.status,
                        "rationale": p.rationale,
                        "diff": p.diff,
                        "approved_copy_path": p.approved_copy_path,
                    }
                    for p in patches
                ],
            }
        )
    run_out = _run_out(run).model_dump()
    run_out["error"] = run.error
    return {"run": run_out, "results": out}


@app.get("/runs/{run_id}/events", dependencies=[Depends(_auth)])
def run_events(run_id: int, db: Session = Depends(get_db)):
    _expire_stale_jobs(db)
    if db.get(models.Run, run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")

    def gen():
        previous: str | None = None
        for _ in range(60):
            with models.SessionLocal() as stream_db:
                _expire_stale_jobs(stream_db)
                run = stream_db.get(models.Run, run_id)
                if run is None:
                    payload = {"run_id": run_id, "event": "error", "error": "run not found"}
                else:
                    payload = {
                        "run_id": run.id,
                        "event": "snapshot",
                        "status": run.status,
                        "total_tests": run.total_tests,
                        "failed_tests": run.failed_tests,
                        "flaky_tests": run.flaky_tests,
                        "error": run.error,
                    }
            encoded = json.dumps(payload)
            if encoded != previous:
                yield f"data: {encoded}\n\n"
                previous = encoded
            if payload.get("status", "error") not in {"queued", "running"}:
                break
            time.sleep(1)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/artifacts/{artifact_id}", dependencies=[Depends(_auth)])
def download_artifact(artifact_id: int, db: Session = Depends(get_db)):
    artifact = db.get(models.Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    root = os.path.realpath(store.store_root())
    path = os.path.realpath(store.resolve(artifact.path))
    try:
        contained = os.path.commonpath((root, path)) == root
    except ValueError:
        contained = False
    if not contained or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="artifact file not found")
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=os.path.basename(path))


def _patch_or_404(db: Session, patch_id: int) -> models.Patch:
    patch = db.get(models.Patch, patch_id)
    if not patch:
        raise HTTPException(status_code=404, detail="patch not found")
    return patch


@app.post("/patches/{patch_id}/approve", dependencies=[Depends(_auth)])
def approve_patch(patch_id: int, db: Session = Depends(get_db)) -> PatchOut:
    patch = _patch_or_404(db, patch_id)
    if patch.status == "approved" and patch.approved_copy_path:
        if not os.path.isdir(store.resolve(patch.approved_copy_path)):
            raise HTTPException(
                status_code=409,
                detail="the approved test copy is missing from the artifact store",
            )
        return PatchOut(
            id=patch.id,
            test_result_id=patch.test_result_id,
            status=patch.status,
            rationale=patch.rationale,
            approved_copy_path=patch.approved_copy_path,
        )
    if patch.status != "proposed":
        raise HTTPException(status_code=409, detail=f"cannot approve a {patch.status} patch")
    result = db.get(models.TestResult, patch.test_result_id)
    run = db.get(models.Run, result.run_id) if result else None
    project = db.get(models.Project, run.project_id) if run else None
    if project is None:
        raise HTTPException(status_code=409, detail="patch project is unavailable")
    destination = os.path.join(store.store_root(), "approved", f"patch-{patch.id}")
    try:
        copy_path = patcher.apply_diff_to_copy(
            project.test_path,
            patch.diff,
            destination=destination,
            strip_prefix="tests",
        )
    except (patcher.PatchError, OSError) as exc:
        raise HTTPException(status_code=422, detail=f"could not apply patch to a test copy: {exc}") from exc
    patch.status = "approved"
    patch.approved_copy_path = store.relpath(copy_path)
    db.commit()
    return PatchOut(
        id=patch.id,
        test_result_id=patch.test_result_id,
        status=patch.status,
        rationale=patch.rationale,
        approved_copy_path=patch.approved_copy_path,
    )


@app.post("/patches/{patch_id}/reject", dependencies=[Depends(_auth)])
def reject_patch(patch_id: int, db: Session = Depends(get_db)) -> PatchOut:
    patch = _patch_or_404(db, patch_id)
    if patch.status == "rejected":
        return PatchOut(id=patch.id, test_result_id=patch.test_result_id, status=patch.status, rationale=patch.rationale)
    if patch.status != "proposed":
        raise HTTPException(status_code=409, detail=f"cannot reject a {patch.status} patch")
    patch.status = "rejected"
    db.commit()
    return PatchOut(id=patch.id, test_result_id=patch.test_result_id, status=patch.status, rationale=patch.rationale)


@app.get("/patches/{patch_id}/download", dependencies=[Depends(_auth)])
def download_patch(patch_id: int, db: Session = Depends(get_db)):
    patch = _patch_or_404(db, patch_id)
    return Response(
        content=patch.diff,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="patch-{patch.id}.diff"'},
    )


@app.get("/benchmarks", dependencies=[Depends(_auth)])
def list_benchmarks(db: Session = Depends(get_db)) -> list[dict]:
    _expire_stale_jobs(db)
    rows = db.query(models.Benchmark).order_by(models.Benchmark.id.desc()).all()
    return [
        {
            "id": row.id,
            "status": row.status,
            "seed": row.seed,
            "started_at": row.started_at.isoformat(),
            "heal_rate": row.heal_rate,
        }
        for row in rows
    ]


@app.post(
    "/benchmarks",
    dependencies=[Depends(_auth), Depends(_job_rate_limiter)],
)
def start_benchmark(body: BenchmarkCreate, db: Session = Depends(get_db)) -> dict:
    allowed_apps = {"todo", "shop", "multistep-form"}
    allowed_mutations = {
        "renamed_id",
        "button_text",
        "moved_element",
        "new_wrapper",
        "cookie_modal",
        "extra_flow_step",
        "button_to_link",
    }
    if not body.apps or len(set(body.apps)) != len(body.apps) or not set(body.apps) <= allowed_apps:
        raise HTTPException(status_code=422, detail="apps must be a non-empty unique subset of the sample apps")
    if (
        not body.mutation_types
        or len(set(body.mutation_types)) != len(body.mutation_types)
        or not set(body.mutation_types) <= allowed_mutations
    ):
        raise HTTPException(
            status_code=422,
            detail="mutation_types must be a non-empty unique supported selection",
        )
    config = body.model_dump()
    row = models.Benchmark(
        config=json.dumps(config, sort_keys=True),
        status="queued",
        seed=body.seed,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        enqueue(jobs.execute_benchmark, row.id, config)
    except QueueUnavailable as exc:
        log.error("Benchmark %d could not be queued: %s", row.id, exc)
        row.status = "failed"
        row.error = str(exc)
        row.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    db.refresh(row)
    return {"id": row.id, "status": row.status, "seed": row.seed}


@app.get("/benchmarks/{benchmark_id}", dependencies=[Depends(_auth)])
def benchmark_detail(benchmark_id: int, db: Session = Depends(get_db)) -> dict:
    _expire_stale_jobs(db)
    row = db.get(models.Benchmark, benchmark_id)
    if not row:
        raise HTTPException(status_code=404, detail="benchmark not found")
    return {
        "id": row.id,
        "status": row.status,
        "seed": row.seed,
        "started_at": row.started_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "error": row.error,
        "heal_rate": row.heal_rate,
        "avg_attempts": row.avg_attempts,
        "median_seconds": row.median_seconds,
        "unsafe_fixes": row.unsafe_fixes,
        "report": json.loads(row.report_json or "{}"),
    }
