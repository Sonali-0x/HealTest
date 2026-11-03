"""SQLAlchemy models mirroring PRD section 10 (data model)."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_sub: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    app_url: Mapped[str] = mapped_column(String(500))
    test_path: Mapped[str] = mapped_column(String(500))
    retry_limit: Mapped[int] = mapped_column(Integer, default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    status: Mapped[str] = mapped_column(String(50), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_tests: Mapped[int] = mapped_column(Integer, default=0)
    failed_tests: Mapped[int] = mapped_column(Integer, default=0)
    flaky_tests: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")


class TestResult(Base):
    __tablename__ = "test_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    test_name: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(50), default="failed")
    error_text: Mapped[str] = mapped_column(Text, default="")
    final_state: Mapped[str] = mapped_column(String(50), default="pending")
    flaky: Mapped[int] = mapped_column(Integer, default=0)


class Attempt(Base):
    __tablename__ = "attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    test_result_id: Mapped[int] = mapped_column(ForeignKey("test_results.id"))
    number: Mapped[int] = mapped_column(Integer)
    failure_class: Mapped[str] = mapped_column(String(100), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    explanation: Mapped[str] = mapped_column(Text, default="")
    used_visual: Mapped[int] = mapped_column(Integer, default=0)
    model_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(String(50), default="pending")


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id"))
    type: Mapped[str] = mapped_column(String(50))  # screenshot, dom, log, mask
    path: Mapped[str] = mapped_column(String(1000))


class Patch(Base):
    __tablename__ = "patches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    test_result_id: Mapped[int] = mapped_column(ForeignKey("test_results.id"))
    diff: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(String(1000), default="")
    status: Mapped[str] = mapped_column(String(50), default="proposed")
    approved_copy_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Benchmark(Base):
    __tablename__ = "benchmarks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_sub: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="queued")
    seed: Mapped[int] = mapped_column(Integer, default=42)
    config: Mapped[str] = mapped_column(Text, default="{}")
    report_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(Text, default="")
    heal_rate: Mapped[float] = mapped_column(Float, default=0.0)
    avg_attempts: Mapped[float] = mapped_column(Float, default=0.0)
    median_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    unsafe_fixes: Mapped[int] = mapped_column(Integer, default=0)


class BenchmarkCase(Base):
    __tablename__ = "benchmark_cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    benchmark_id: Mapped[int] = mapped_column(ForeignKey("benchmarks.id"))
    app: Mapped[str] = mapped_column(String(100))
    mutation_type: Mapped[str] = mapped_column(String(100))
    arm: Mapped[str] = mapped_column(String(50), default="healtest")
    healed: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    unsafe_fixes: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)


def get_engine(url: str | None = None):
    url = url or os.environ.get("DATABASE_URL", "sqlite:///./data/healtest.db")
    if url.startswith("sqlite"):
        path = url.split("sqlite:///", 1)[1].split("?")[0]
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True, pool_recycle=300)


engine = get_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db(url: str | None = None):
    eng = get_engine(url) if url else engine
    Base.metadata.create_all(bind=eng)
    additions = {
        "projects": {
            "owner_sub": "VARCHAR(128)",
        },
        "patches": {
            "approved_copy_path": "VARCHAR(1000)",
        },
        "runs": {
            "flaky_tests": "INTEGER NOT NULL DEFAULT 0",
        },
        "test_results": {
            "flaky": "INTEGER NOT NULL DEFAULT 0",
        },
        "benchmarks": {
            "owner_sub": "VARCHAR(128)",
            "finished_at": "TIMESTAMP",
            "status": "VARCHAR(50) NOT NULL DEFAULT 'queued'",
            "seed": "INTEGER NOT NULL DEFAULT 42",
            "report_json": "TEXT NOT NULL DEFAULT '{}'",
            "error": "TEXT NOT NULL DEFAULT ''",
        },
        "benchmark_cases": {
            "arm": "VARCHAR(50) NOT NULL DEFAULT 'healtest'",
            "tokens": "INTEGER NOT NULL DEFAULT 0",
            "unsafe_fixes": "INTEGER NOT NULL DEFAULT 0",
            "error": "TEXT NOT NULL DEFAULT ''",
            "run_id": "INTEGER",
        },
    }
    with eng.begin() as connection:
        inspector = inspect(eng)
        for table, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, declaration in columns.items():
                if name not in existing:
                    connection.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
                    )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_projects_owner_sub "
                "ON projects (owner_sub)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_benchmarks_owner_sub "
                "ON benchmarks (owner_sub)"
            )
        )
    return eng


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
