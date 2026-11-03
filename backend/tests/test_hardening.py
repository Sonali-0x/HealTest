from datetime import datetime, timedelta, timezone
import pytest
from fastapi import Depends, FastAPI, HTTPException
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


def test_job_rate_limiter_uses_shared_redis(monkeypatch):
    calls = []

    class FakeRedisClient:
        def eval(self, *args):
            calls.append(args)
            return (1, 0)

    class FakeRedis:
        @staticmethod
        def from_url(url, **kwargs):
            assert url == "redis://redis.example/0"
            assert kwargs == {"socket_connect_timeout": 2, "socket_timeout": 2}
            return FakeRedisClient()

    monkeypatch.setattr("redis.Redis.from_url", FakeRedis.from_url)
    limiter = JobRateLimiter(
        max_requests=2,
        window_seconds=30,
        redis_url="redis://redis.example/0",
    )

    limiter.check("cognito-subject")

    assert len(calls) == 1
    assert calls[0][1] == 1
    assert calls[0][2].startswith("healtest:job-rate:")
    assert calls[0][3:5] == (2, 30)


def test_job_rate_limiter_fails_closed_in_production_without_redis(monkeypatch):
    monkeypatch.setenv("HEALTEST_ENV", "production")
    limiter = JobRateLimiter(max_requests=2, window_seconds=30)

    with pytest.raises(HTTPException) as exc_info:
        limiter.check("tenant")

    assert getattr(exc_info.value, "status_code", None) == 503


@pytest.mark.parametrize(
    "database_url,should_start",
    [
        ("postgresql+psycopg://user:pass@db/healtest?sslmode=verify-full", True),
        ("postgresql+psycopg://user:pass@db/healtest", False),
        ("sqlite:///./data/healtest.db", False),
    ],
)
def test_production_requires_postgres_tls(monkeypatch, database_url, should_start):
    monkeypatch.setattr(main.auth, "validate_settings", lambda: None)
    monkeypatch.setenv("HEALTEST_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("REDIS_URL", "rediss://redis.example/0")
    monkeypatch.setenv("HEALTEST_MOCK_MODE", "0")
    monkeypatch.setenv("HEALTEST_DATA_DIR", "/mnt/healtest-data")
    monkeypatch.setenv("HEALTEST_CORS_ORIGINS", "https://healtest.example")
    monkeypatch.setenv("HEALTEST_AUTO_MIGRATE", "0")

    if should_start:
        main._startup()
    else:
        with pytest.raises(RuntimeError, match="PostgreSQL|TLS"):
            main._startup()


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
