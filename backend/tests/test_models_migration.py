from sqlalchemy import create_engine, inspect, text

from app import models


def test_init_db_adds_approved_copy_path_to_existing_patch_table(tmp_path):
    database = tmp_path / "existing.db"
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE patches ("
                "id INTEGER PRIMARY KEY, test_result_id INTEGER NOT NULL, "
                "diff TEXT NOT NULL DEFAULT '', rationale VARCHAR(1000) NOT NULL DEFAULT '', "
                "status VARCHAR(50) NOT NULL DEFAULT 'proposed', "
                "created_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE runs (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, "
                "status VARCHAR(50) NOT NULL, started_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE test_results (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, "
                "test_name VARCHAR(500) NOT NULL, status VARCHAR(50) NOT NULL)"
            )
        )
    engine.dispose()

    migrated_engine = models.init_db(url)

    inspector = inspect(migrated_engine)
    assert "approved_copy_path" in {
        column["name"] for column in inspector.get_columns("patches")
    }
    assert "flaky_tests" in {
        column["name"] for column in inspector.get_columns("runs")
    }
    assert "flaky" in {
        column["name"] for column in inspector.get_columns("test_results")
    }
    migrated_engine.dispose()


def test_init_db_adds_benchmark_report_columns_to_existing_tables(tmp_path):
    database = tmp_path / "existing-benchmarks.db"
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE benchmarks ("
                "id INTEGER PRIMARY KEY, started_at DATETIME NOT NULL, "
                "config TEXT NOT NULL, heal_rate FLOAT NOT NULL, "
                "avg_attempts FLOAT NOT NULL, median_seconds FLOAT NOT NULL, "
                "unsafe_fixes INTEGER NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE benchmark_cases ("
                "id INTEGER PRIMARY KEY, benchmark_id INTEGER NOT NULL, "
                "app VARCHAR(100) NOT NULL, mutation_type VARCHAR(100) NOT NULL, "
                "healed INTEGER NOT NULL, attempts INTEGER NOT NULL, seconds FLOAT NOT NULL)"
            )
        )
    engine.dispose()

    migrated_engine = models.init_db(url)
    inspector = inspect(migrated_engine)

    assert {
        "status",
        "seed",
        "finished_at",
        "report_json",
        "error",
    } <= {column["name"] for column in inspector.get_columns("benchmarks")}
    assert {
        "arm",
        "tokens",
        "unsafe_fixes",
        "error",
        "run_id",
    } <= {column["name"] for column in inspector.get_columns("benchmark_cases")}
    migrated_engine.dispose()
