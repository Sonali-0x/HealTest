"""Job queue: Redis + RQ when REDIS_URL is set, inline fallback otherwise."""

from __future__ import annotations

import os
import logging
from typing import Any, Callable

log = logging.getLogger("healtest.queue")


class QueueUnavailable(RuntimeError):
    """Raised when a configured Redis queue cannot accept a job."""


def _redis_url() -> str | None:
    return os.environ.get("REDIS_URL") or None


def job_timeout_seconds() -> int:
    raw_timeout = os.environ.get("HEALTEST_JOB_TIMEOUT_S", "1800")
    try:
        timeout = int(raw_timeout)
    except ValueError as exc:
        raise QueueUnavailable("HEALTEST_JOB_TIMEOUT_S must be a positive integer") from exc
    if timeout <= 0:
        raise QueueUnavailable("HEALTEST_JOB_TIMEOUT_S must be a positive integer")
    return timeout


def enqueue(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Enqueue a job; run inline in dev when Redis is unavailable."""
    url = _redis_url()
    if not url:
        return fn(*args, **kwargs)
    try:
        import redis
        from rq import Queue
    except ImportError as exc:
        raise QueueUnavailable(
            "REDIS_URL is configured but Redis/RQ dependencies are unavailable"
        ) from exc

    timeout = job_timeout_seconds()
    try:
        queue = Queue("healtest", connection=redis.Redis.from_url(url))
        return queue.enqueue(fn, *args, job_timeout=timeout, **kwargs)
    except Exception as exc:
        function_name = getattr(fn, "__name__", type(fn).__name__)
        log.exception("Could not enqueue %s using the configured Redis queue", function_name)
        raise QueueUnavailable("configured Redis queue could not accept the job") from exc
