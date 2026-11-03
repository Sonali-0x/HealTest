"""FastAPI service: REST + SSE (PRD section 8.2)."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from redis.exceptions import RedisError

from . import auth, jobs, maintenance, models, patcher, store
from .hardening import JobRateLimiter, RequestBodyLimitMiddleware
from .models import get_db, init_db
from .queue import QueueUnavailable, enqueue
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
    redis_url=os.environ.get("REDIS_URL") or None,
)


@app.on_event("startup")
def _startup() -> None:
    auth.validate_settings()
    if os.environ.get("HEALTEST_ENV", "").strip().lower() == "production":
        database_url = os.environ.get("DATABASE_URL", "").strip()
        if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise RuntimeError("production requires a PostgreSQL DATABASE_URL")
        sslmode = parse_qs(urlparse(database_url).query).get("sslmode", [""])[0]
        if sslmode not in {"require", "verify-ca", "verify-full"}:
            raise RuntimeError("production PostgreSQL connections require TLS via sslmode")
        if not os.environ.get("REDIS_URL", "").strip().startswith("rediss://"):
            raise RuntimeError("production requires a TLS-protected rediss:// REDIS_URL")
        if os.environ.get("HEALTEST_MOCK_MODE", "1").strip() != "0":
            raise RuntimeError("HEALTEST_MOCK_MODE=0 is required in production")
        if not os.environ.get("HEALTEST_DATA_DIR", "").strip():
            raise RuntimeError("HEALTEST_DATA_DIR must point to persistent artifact storage")
        origins = [
            origin.strip()
            for origin in os.environ.get("HEALTEST_CORS_ORIGINS", "").split(",")
            if origin.strip()
        ]
        if not origins or any(
            origin == "*" or urlparse(origin).scheme != "https"
            for origin in origins
        ):
            raise RuntimeError("production CORS origins must be explicit HTTPS origins")
        if os.environ.get("HEALTEST_AUTO_MIGRATE", "").strip() != "0":
            raise RuntimeError(
                "run `python -m app.maintenance migrate` before deployment "
                "and set HEALTEST_AUTO_MIGRATE=0"
            )
    else:
        init_db()


def _limit_job_submission(
    owner_sub: str = Depends(auth.current_principal),
) -> None:
    _job_rate_limiter.check(owner_sub)


def _owned_project(db: Session, project_id: int, owner_sub: str) -> models.Project:
    project = (
        db.query(models.Project)
        .filter_by(id=project_id, owner_sub=owner_sub)
        .first()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def _owned_run(db: Session, run_id: int, owner_sub: str) -> models.Run:
    run = (
        db.query(models.Run)
        .join(models.Project, models.Run.project_id == models.Project.id)
        .filter(models.Run.id == run_id, models.Project.owner_sub == owner_sub)
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


def _owned_patch(db: Session, patch_id: int, owner_sub: str) -> models.Patch:
    patch = (
        db.query(models.Patch)
        .join(models.TestResult, models.Patch.test_result_id == models.TestResult.id)
        .join(models.Run, models.TestResult.run_id == models.Run.id)
        .join(models.Project, models.Run.project_id == models.Project.id)
        .filter(models.Patch.id == patch_id, models.Project.owner_sub == owner_sub)
        .first()
    )
    if patch is None:
        raise HTTPException(status_code=404, detail="patch not found")
    return patch


def _expire_stale_jobs(db: Session) -> None:
    maintenance.expire_stale_jobs(db)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def readiness() -> dict[str, str]:
    try:
        with models.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        redis_url = os.environ.get("REDIS_URL", "").strip()
        if redis_url:
            import redis

            redis.Redis.from_url(redis_url, socket_connect_timeout=2).ping()
        elif os.environ.get("HEALTEST_ENV", "").strip().lower() == "production":
            raise RuntimeError("REDIS_URL must be configured in production")
    except (SQLAlchemyError, RedisError, RuntimeError) as exc:
        log.warning("Readiness check failed: %s", exc)
        raise HTTPException(status_code=503, detail="service dependencies unavailable") from exc
    return {"status": "ready"}


@app.post("/projects")
def create_project(
    body: ProjectCreate,
    owner_sub: str = Depends(auth.current_principal),
    db: Session = Depends(get_db),
) -> ProjectOut:
    row = models.Project(
        owner_sub=owner_sub,
        name=body.name, app_url=body.app_url, test_path=body.test_path, retry_limit=body.retry_limit
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return ProjectOut(
        id=row.id, name=row.name, app_url=row.app_url, test_path=row.test_path, retry_limit=row.retry_limit
    )


@app.get("/projects")
def list_projects(
    owner_sub: str = Depends(auth.current_principal),
    db: Session = Depends(get_db),
) -> list[ProjectOut]:
    rows = db.query(models.Project).filter_by(owner_sub=owner_sub).all()
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
    dependencies=[Depends(_limit_job_submission)],
)
def start_run(
    project_id: int,
    owner_sub: str = Depends(auth.current_principal),
    db: Session = Depends(get_db),
) -> RunOut:
    project = _owned_project(db, project_id, owner_sub)
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


@app.get("/runs")
def list_runs(
    owner_sub: str = Depends(auth.current_principal),
    db: Session = Depends(get_db),
) -> list[RunOut]:
    _expire_stale_jobs(db)
    runs = (
        db.query(models.Run)
        .join(models.Project, models.Run.project_id == models.Project.id)
        .filter(models.Project.owner_sub == owner_sub)
        .all()
    )
    return [_run_out(run) for run in runs]


@app.get("/runs/{run_id}")
def run_detail(
    run_id: int,
    owner_sub: str = Depends(auth.current_principal),
    db: Session = Depends(get_db),
) -> dict:
    _expire_stale_jobs(db)
    run = _owned_run(db, run_id, owner_sub)
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


@app.get("/runs/{run_id}/events")
def run_events(
    run_id: int,
    owner_sub: str = Depends(auth.current_principal),
    db: Session = Depends(get_db),
):
    _expire_stale_jobs(db)
    _owned_run(db, run_id, owner_sub)

    def gen():
        previous: str | None = None
        for _ in range(60):
            with models.SessionLocal() as stream_db:
                _expire_stale_jobs(stream_db)
                run = (
                    stream_db.query(models.Run)
                    .join(models.Project, models.Run.project_id == models.Project.id)
                    .filter(
                        models.Run.id == run_id,
                        models.Project.owner_sub == owner_sub,
                    )
                    .first()
                )
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


@app.get("/artifacts/{artifact_id}")
def download_artifact(
    artifact_id: int,
    owner_sub: str = Depends(auth.current_principal),
    db: Session = Depends(get_db),
):
    artifact = (
        db.query(models.Artifact)
        .join(models.Attempt, models.Artifact.attempt_id == models.Attempt.id)
        .join(models.TestResult, models.Attempt.test_result_id == models.TestResult.id)
        .join(models.Run, models.TestResult.run_id == models.Run.id)
        .join(models.Project, models.Run.project_id == models.Project.id)
        .filter(
            models.Artifact.id == artifact_id,
            models.Project.owner_sub == owner_sub,
        )
        .first()
    )
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


@app.post("/patches/{patch_id}/approve")
def approve_patch(
    patch_id: int,
    owner_sub: str = Depends(auth.current_principal),
    db: Session = Depends(get_db),
) -> PatchOut:
    patch = _owned_patch(db, patch_id, owner_sub)
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


@app.post("/patches/{patch_id}/reject")
def reject_patch(
    patch_id: int,
    owner_sub: str = Depends(auth.current_principal),
    db: Session = Depends(get_db),
) -> PatchOut:
    patch = _owned_patch(db, patch_id, owner_sub)
    if patch.status == "rejected":
        return PatchOut(id=patch.id, test_result_id=patch.test_result_id, status=patch.status, rationale=patch.rationale)
    if patch.status != "proposed":
        raise HTTPException(status_code=409, detail=f"cannot reject a {patch.status} patch")
    patch.status = "rejected"
    db.commit()
    return PatchOut(id=patch.id, test_result_id=patch.test_result_id, status=patch.status, rationale=patch.rationale)


@app.get("/patches/{patch_id}/download")
def download_patch(
    patch_id: int,
    owner_sub: str = Depends(auth.current_principal),
    db: Session = Depends(get_db),
):
    patch = _owned_patch(db, patch_id, owner_sub)
    return Response(
        content=patch.diff,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="patch-{patch.id}.diff"'},
    )


@app.get("/benchmarks")
def list_benchmarks(
    owner_sub: str = Depends(auth.current_principal),
    db: Session = Depends(get_db),
) -> list[dict]:
    _expire_stale_jobs(db)
    rows = (
        db.query(models.Benchmark)
        .filter_by(owner_sub=owner_sub)
        .order_by(models.Benchmark.id.desc())
        .all()
    )
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
    dependencies=[Depends(_limit_job_submission)],
)
def start_benchmark(
    body: BenchmarkCreate,
    owner_sub: str = Depends(auth.current_principal),
    db: Session = Depends(get_db),
) -> dict:
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
        owner_sub=owner_sub,
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


@app.get("/benchmarks/{benchmark_id}")
def benchmark_detail(
    benchmark_id: int,
    owner_sub: str = Depends(auth.current_principal),
    db: Session = Depends(get_db),
) -> dict:
    _expire_stale_jobs(db)
    row = (
        db.query(models.Benchmark)
        .filter_by(id=benchmark_id, owner_sub=owner_sub)
        .first()
    )
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
