"""FastAPI service: REST + SSE (PRD section 8.2)."""

from __future__ import annotations

import json
import os

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from . import jobs, models
from .models import get_db, init_db
from .queue import enqueue
from .schemas import PatchOut, ProjectCreate, ProjectOut, RunOut

app = FastAPI(title="HealTest API", version="0.0.1")


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _auth(authorization: str | None = Header(default=None)) -> None:
    token = os.environ.get("HEALTEST_API_TOKEN", "")
    if not token:
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")


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
    )


@app.post("/projects/{project_id}/runs", dependencies=[Depends(_auth)])
def start_run(project_id: int, db: Session = Depends(get_db)) -> RunOut:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    run = models.Run(project_id=project_id, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    # Phase 1: real execution via the worker (RQ job, or inline when Redis
    # is unavailable). The job runs pytest, persists results + artifacts,
    # and drives the heal loop per failure.
    enqueue(jobs.execute_run, run.id)
    db.refresh(run)
    return _run_out(run)


@app.get("/runs", dependencies=[Depends(_auth)])
def list_runs(db: Session = Depends(get_db)) -> list[RunOut]:
    return [_run_out(r) for r in db.query(models.Run).all()]


@app.get("/runs/{run_id}", dependencies=[Depends(_auth)])
def run_detail(run_id: int, db: Session = Depends(get_db)) -> dict:
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
                    "artifacts": [{"type": art.type, "path": art.path} for art in artifacts],
                }
            )
        out.append(
            {
                "id": res.id, "test_name": res.test_name, "status": res.status,
                "final_state": res.final_state, "error_text": res.error_text,
                "attempts": attempt_out,
                "patches": [{"id": p.id, "status": p.status, "rationale": p.rationale} for p in patches],
            }
        )
    run_out = _run_out(run).model_dump()
    run_out["error"] = run.error
    return {"run": run_out, "results": out}


@app.get("/runs/{run_id}/events")
def run_events(run_id: int):
    def gen():
        yield f"data: {json.dumps({'run_id': run_id, 'event': 'snapshot', 'state': 'ok'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


def _patch_or_404(db: Session, patch_id: int) -> models.Patch:
    patch = db.get(models.Patch, patch_id)
    if not patch:
        raise HTTPException(status_code=404, detail="patch not found")
    return patch


@app.post("/patches/{patch_id}/approve", dependencies=[Depends(_auth)])
def approve_patch(patch_id: int, db: Session = Depends(get_db)) -> PatchOut:
    patch = _patch_or_404(db, patch_id)
    patch.status = "approved"
    db.commit()
    return PatchOut(id=patch.id, test_result_id=patch.test_result_id, status=patch.status, rationale=patch.rationale)


@app.post("/patches/{patch_id}/reject", dependencies=[Depends(_auth)])
def reject_patch(patch_id: int, db: Session = Depends(get_db)) -> PatchOut:
    patch = _patch_or_404(db, patch_id)
    patch.status = "rejected"
    db.commit()
    return PatchOut(id=patch.id, test_result_id=patch.test_result_id, status=patch.status, rationale=patch.rationale)


@app.get("/patches/{patch_id}/download", dependencies=[Depends(_auth)])
def download_patch(patch_id: int, db: Session = Depends(get_db)):
    patch = _patch_or_404(db, patch_id)
    return Response(content=patch.diff, media_type="text/plain")


@app.post("/benchmarks", dependencies=[Depends(_auth)])
def start_benchmark(db: Session = Depends(get_db)) -> dict:
    row = models.Benchmark(config="{}")
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "status": "stub — runner lands in Phase 6"}


@app.get("/benchmarks/{benchmark_id}", dependencies=[Depends(_auth)])
def benchmark_detail(benchmark_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(models.Benchmark, benchmark_id)
    if not row:
        raise HTTPException(status_code=404, detail="benchmark not found")
    return {"id": row.id, "heal_rate": row.heal_rate, "avg_attempts": row.avg_attempts,
            "median_seconds": row.median_seconds, "unsafe_fixes": row.unsafe_fixes}
