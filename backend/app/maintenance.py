"""Scheduled cleanup tasks for production worker deployments."""

from __future__ import annotations

import json
import logging
import os
import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import models
from .models import SessionLocal, init_db
from .queue import job_timeout_seconds

log = logging.getLogger("healtest.maintenance")


def expire_stale_jobs(db: Session) -> dict[str, int]:
    grace = os.environ.get("HEALTEST_STALE_JOB_GRACE_S", "60")
    try:
        grace_seconds = int(grace)
    except ValueError as exc:
        raise RuntimeError("HEALTEST_STALE_JOB_GRACE_S must be a positive integer") from exc
    if grace_seconds <= 0:
        raise RuntimeError("HEALTEST_STALE_JOB_GRACE_S must be a positive integer")

    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=job_timeout_seconds() + grace_seconds
    )
    now = datetime.now(timezone.utc)
    run_count = (
        db.query(models.Run)
        .filter(
            models.Run.status.in_(("queued", "running")),
            models.Run.started_at < cutoff,
        )
        .update(
            {
                models.Run.status: "error",
                models.Run.error: f"job exceeded its {job_timeout_seconds()}s execution limit",
                models.Run.finished_at: now,
            },
            synchronize_session=False,
        )
    )
    benchmark_count = (
        db.query(models.Benchmark)
        .filter(
            models.Benchmark.status.in_(("queued", "running")),
            models.Benchmark.started_at < cutoff,
        )
        .update(
            {
                models.Benchmark.status: "failed",
                models.Benchmark.error: f"job exceeded its {job_timeout_seconds()}s execution limit",
                models.Benchmark.finished_at: now,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return {"runs": run_count, "benchmarks": benchmark_count}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="HealTest database maintenance")
    parser.add_argument("action", choices=("migrate", "sweep-stale-jobs"))
    action = parser.parse_args(argv).action
    if action == "migrate":
        init_db()
        log.info("Database schema migration completed")
        return
    with SessionLocal() as db:
        counts = expire_stale_jobs(db)
    log.info("Finalized stale jobs: %s", json.dumps(counts, sort_keys=True))


if __name__ == "__main__":
    main()
