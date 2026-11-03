from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auth, main, models
from app.models import get_db


def test_projects_runs_and_benchmarks_are_scoped_to_cognito_subject(
    tmp_path, monkeypatch
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tenant-isolation.db'}",
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

    principal = {"sub": "tenant-a"}
    queued = []
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main.auth, "validate_settings", lambda: None)
    monkeypatch.setattr(main, "enqueue", lambda fn, *args: queued.append((fn, args)))
    main.app.dependency_overrides[auth.current_principal] = lambda: principal["sub"]
    main.app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(main.app) as client:
            project_response = client.post(
                "/projects",
                json={
                    "name": "tenant-a project",
                    "app_url": "http://sample-apps:8901",
                    "test_path": "/tests",
                },
            )
            assert project_response.status_code == 200
            project_id = project_response.json()["id"]

            run_response = client.post(f"/projects/{project_id}/runs")
            assert run_response.status_code == 200
            run_id = run_response.json()["id"]
            assert queued

            artifact_root = tmp_path / "artifact-store"
            artifact_file = artifact_root / "screenshots" / "tenant-a.png"
            artifact_file.parent.mkdir(parents=True)
            artifact_file.write_bytes(b"tenant-a-artifact")
            monkeypatch.setattr(
                main.store, "store_root", lambda: str(artifact_root)
            )
            with test_sessions() as db:
                result = models.TestResult(
                    run_id=run_id,
                    test_name="test_owned",
                    status="failed",
                )
                db.add(result)
                db.commit()
                attempt = models.Attempt(test_result_id=result.id, number=1)
                patch = models.Patch(test_result_id=result.id, diff="tenant-a-diff")
                db.add_all([attempt, patch])
                db.commit()
                artifact = models.Artifact(
                    attempt_id=attempt.id,
                    type="screenshot",
                    path="screenshots/tenant-a.png",
                )
                db.add(artifact)
                db.commit()
                patch_id = patch.id
                artifact_id = artifact.id

            assert client.get(f"/patches/{patch_id}/download").text == "tenant-a-diff"
            assert client.get(f"/artifacts/{artifact_id}").content == b"tenant-a-artifact"

            benchmark_response = client.post(
                "/benchmarks",
                json={"apps": ["todo"], "mutation_types": ["renamed_id"]},
            )
            assert benchmark_response.status_code == 200
            benchmark_id = benchmark_response.json()["id"]

            principal["sub"] = "tenant-b"
            assert client.get("/projects").json() == []
            assert client.get("/runs").json() == []
            assert client.get(f"/runs/{run_id}").status_code == 404
            assert client.get(f"/runs/{run_id}/events").status_code == 404
            assert client.get(f"/patches/{patch_id}/download").status_code == 404
            assert client.post(f"/patches/{patch_id}/reject").status_code == 404
            assert client.get(f"/artifacts/{artifact_id}").status_code == 404
            assert client.get(f"/benchmarks/{benchmark_id}").status_code == 404
            assert client.get("/benchmarks").json() == []
            assert client.post(f"/projects/{project_id}/runs").status_code == 404
    finally:
        main.app.dependency_overrides.clear()
        engine.dispose()
