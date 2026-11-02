import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import models
from benchmark import runner


def test_benchmark_job_persists_report_and_arm_rows(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'benchmark-job.db'}",
        connect_args={"check_same_thread": False},
    )
    models.Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(models, "SessionLocal", test_sessions)
    monkeypatch.setattr(
        runner,
        "run",
        lambda config: {
            "arms": {
                "no_healing": {"heal_rate": 0.0, "avg_attempts": 0.0, "median_seconds": 1.0, "unsafe_fixes": 0},
                "heuristic": {"heal_rate": 1.0, "avg_attempts": 1.0, "median_seconds": 2.0, "unsafe_fixes": 0},
                "healtest": {"heal_rate": 1.0, "avg_attempts": 2.0, "median_seconds": 3.0, "unsafe_fixes": 1},
            },
            "cases": [
                {
                    "app": "todo",
                    "mutation_type": "renamed_id",
                    "arm": "no_healing",
                    "healed": False,
                    "attempts": 0,
                    "seconds": 1.0,
                    "tokens": 0,
                    "unsafe_fixes": 0,
                    "error": "broken locator",
                    "run_id": None,
                },
                {
                    "app": "todo",
                    "mutation_type": "renamed_id",
                    "arm": "heuristic",
                    "healed": True,
                    "attempts": 1,
                    "seconds": 2.0,
                    "tokens": 0,
                    "unsafe_fixes": 0,
                    "error": "",
                    "run_id": None,
                },
                {
                    "app": "todo",
                    "mutation_type": "renamed_id",
                    "arm": "healtest",
                    "healed": True,
                    "attempts": 2,
                    "seconds": 3.0,
                    "tokens": 90,
                    "unsafe_fixes": 1,
                    "error": "",
                    "run_id": 7,
                },
            ],
        },
    )
    db = test_sessions()
    benchmark = models.Benchmark(status="queued", seed=42, config="{}")
    db.add(benchmark)
    db.commit()
    benchmark_id = benchmark.id
    db.close()

    try:
        assert runner.execute_benchmark_job(benchmark_id, {"seed": 42}) == {
            "id": benchmark_id,
            "status": "completed",
        }
        db = test_sessions()
        stored = db.get(models.Benchmark, benchmark_id)
        rows = db.query(models.BenchmarkCase).filter_by(benchmark_id=benchmark_id).all()

        assert stored is not None
        assert stored.status == "completed"
        assert stored.heal_rate == 1.0
        assert len(rows) == 3
        assert {case.arm for case in rows} == {"no_healing", "heuristic", "healtest"}
        assert stored.report_json != "{}"
    finally:
        if "db" in locals():
            db.close()
        engine.dispose()
