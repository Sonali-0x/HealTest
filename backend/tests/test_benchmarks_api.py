from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main, models
from app.models import get_db
from app.queue import QueueUnavailable


def test_benchmark_api_queues_run_and_returns_persisted_report(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bench-api.db'}",
        connect_args={"check_same_thread": False},
    )
    models.Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_db():
        db = test_sessions()
        try:
            yield db
        finally:
            db.close()

    queued = []
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main.auth, "validate_settings", lambda: None)
    monkeypatch.setattr(main, "enqueue", lambda fn, *args: queued.append((fn, args)))
    main.app.dependency_overrides[main.auth.current_principal] = lambda: "tenant-a"
    main.app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(main.app) as client:
            created = client.post(
                "/benchmarks",
                json={"seed": 19, "apps": ["todo"], "mutation_types": ["renamed_id"]},
            )
            assert created.status_code == 200
            payload = created.json()
            assert payload == {"id": 1, "status": "queued", "seed": 19}
            assert len(queued) == 1
            assert queued[0][0] is main.jobs.execute_benchmark

            listing = client.get("/benchmarks")
            assert listing.status_code == 200
            assert listing.json()[0]["id"] == payload["id"]

            detail = client.get("/benchmarks/1")
            assert detail.status_code == 200
            assert detail.json()["report"] == {}
            assert detail.json()["seed"] == 19
    finally:
        main.app.dependency_overrides.clear()
        engine.dispose()


def test_benchmark_api_rejects_unknown_mutation(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bench-invalid.db'}",
        connect_args={"check_same_thread": False},
    )
    models.Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_db():
        db = test_sessions()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main.auth, "validate_settings", lambda: None)
    monkeypatch.setattr(main, "enqueue", lambda *args: None)
    main.app.dependency_overrides[main.auth.current_principal] = lambda: "tenant-a"
    main.app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(main.app) as client:
            response = client.post(
                "/benchmarks",
                json={"mutation_types": ["arbitrary-input"]},
            )
            assert response.status_code == 422
            assert response.json()["detail"].startswith("mutation_types")
    finally:
        main.app.dependency_overrides.clear()
        engine.dispose()


def test_benchmark_queue_failure_is_reported_and_persisted(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bench-queue-error.db'}",
        connect_args={"check_same_thread": False},
    )
    models.Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_db():
        db = test_sessions()
        try:
            yield db
        finally:
            db.close()

    def fail_enqueue(*args, **kwargs):
        raise QueueUnavailable("configured Redis queue is unavailable")

    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main.auth, "validate_settings", lambda: None)
    monkeypatch.setattr(main, "enqueue", fail_enqueue)
    main.app.dependency_overrides[main.auth.current_principal] = lambda: "tenant-a"
    main.app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(main.app) as client:
            response = client.post(
                "/benchmarks",
                json={"apps": ["todo"], "mutation_types": ["renamed_id"]},
            )
            assert response.status_code == 503

        with test_sessions() as db:
            benchmark = db.query(models.Benchmark).one()
            assert benchmark.status == "failed"
            assert "Redis queue is unavailable" in benchmark.error
            assert benchmark.finished_at is not None
    finally:
        main.app.dependency_overrides.clear()
        engine.dispose()
