from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import jobs, main, models, runner
from app.hardening import JobRateLimiter, RequestBodyLimitMiddleware


def test_request_body_middleware_rejects_oversized_body():
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=8)

    @app.post("/")
    def receive_body():
        return {"ok": True}

    with TestClient(app) as client:
        response = client.post("/", content=b"0123456789")

    assert response.status_code == 413
    assert response.text == "request body too large"


def test_request_body_middleware_preserves_streaming_responses():
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=8)

    @app.get("/events")
    def events():
        return StreamingResponse(
            iter(["data: first\n\n", "data: second\n\n"]),
            media_type="text/event-stream",
        )

    with TestClient(app) as client:
        response = client.get("/events")

    assert response.text == "data: first\n\ndata: second\n\n"


def test_job_rate_limiter_returns_retry_after():
    now = [100.0]
    limiter = JobRateLimiter(max_requests=1, window_seconds=30, clock=lambda: now[0])
    app = FastAPI()

    @app.post("/", dependencies=[Depends(limiter)])
    def submit_job():
        return {"queued": True}

    with TestClient(app) as client:
        assert client.post("/").status_code == 200
        limited = client.post("/")
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "30"
        now[0] += 31
        assert client.post("/").status_code == 200


def test_stale_runs_and_benchmarks_are_finalized(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'stale.db'}",
        connect_args={"check_same_thread": False},
    )
    models.Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    project = models.Project(
        name="sample",
        app_url="http://localhost",
        test_path=str(tmp_path),
    )
    db.add(project)
    db.commit()
    run = models.Run(
        project_id=project.id,
        status="running",
        started_at=now - timedelta(hours=1),
    )
    benchmark = models.Benchmark(
        status="queued",
        started_at=now - timedelta(hours=1),
    )
    db.add_all([run, benchmark])
    db.commit()
    monkeypatch.setenv("HEALTEST_JOB_TIMEOUT_S", "30")
    monkeypatch.setenv("HEALTEST_STALE_JOB_GRACE_S", "1")

    main._expire_stale_jobs(db)

    db.refresh(run)
    db.refresh(benchmark)
    assert run.status == "error"
    assert "execution limit" in run.error
    assert run.finished_at is not None
    assert benchmark.status == "failed"
    assert benchmark.finished_at is not None
    db.close()
    engine.dispose()


def test_unexpected_run_exception_is_persisted_as_error(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    models.Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    project = models.Project(
        name="sample",
        app_url="http://localhost",
        test_path=str(tmp_path),
    )
    db.add(project)
    db.commit()
    run = models.Run(project_id=project.id, status="queued")
    db.add(run)
    db.commit()
    run_id = run.id
    monkeypatch.setattr(jobs, "SessionLocal", lambda: db)

    def fail_run(*args, **kwargs):
        raise RuntimeError("unexpected worker failure")

    monkeypatch.setattr(runner, "run_tests", fail_run)
    with pytest.raises(RuntimeError, match="unexpected worker failure"):
        jobs.execute_run(run_id)

    assert db.get(models.Run, run_id).status == "error"
    assert "unexpected worker failure" in db.get(models.Run, run_id).error
    db.close()
    engine.dispose()
