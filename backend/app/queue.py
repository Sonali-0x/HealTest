"""Job queue: Redis + RQ when REDIS_URL is set, inline fallback otherwise."""

from __future__ import annotations

import os
from typing import Any, Callable


def _redis_url() -> str | None:
    return os.environ.get("REDIS_URL") or None


def enqueue(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Enqueue a job; run inline in dev when Redis is unavailable."""
    url = _redis_url()
    if not url:
        return fn(*args, **kwargs)
    try:
        import redis
        from rq import Queue

        q = Queue(connection=redis.Redis.from_url(url))
        return q.enqueue(fn, *args, **kwargs)
    except Exception:
        return fn(*args, **kwargs)
