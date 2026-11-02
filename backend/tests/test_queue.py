import pytest
import redis

from app import queue
from app.queue import QueueUnavailable


def test_configured_redis_failure_is_not_run_inline(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://unavailable:6379/0")

    def fail_connection(*args, **kwargs):
        raise redis.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(redis.Redis, "from_url", fail_connection)
    called = []

    with pytest.raises(QueueUnavailable, match="could not accept"):
        queue.enqueue(lambda: called.append("inline"))

    assert called == []


def test_rq_enqueue_uses_configured_execution_timeout(monkeypatch):
    import rq

    captured = {}

    class FakeQueue:
        def __init__(self, name, connection):
            captured["queue_name"] = name
            captured["connection"] = connection

        def enqueue(self, function, *args, **kwargs):
            captured["job_timeout"] = kwargs["job_timeout"]
            return "queued"

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("HEALTEST_JOB_TIMEOUT_S", "75")
    monkeypatch.setattr(rq, "Queue", FakeQueue)

    result = queue.enqueue(lambda: None)

    assert result == "queued"
    assert captured["queue_name"] == "healtest"
    assert captured["job_timeout"] == 75


def test_invalid_job_timeout_is_rejected(monkeypatch):
    monkeypatch.setenv("HEALTEST_JOB_TIMEOUT_S", "0")

    with pytest.raises(QueueUnavailable, match="positive integer"):
        queue.job_timeout_seconds()
