"""Request-size and job-submission limits for the demo API."""

from __future__ import annotations

import math
import hashlib
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable

import redis
from fastapi import HTTPException, Request
from redis.exceptions import RedisError


_RATE_LIMIT_SCRIPT = """
local time_parts = redis.call('TIME')
local now = tonumber(time_parts[1]) + tonumber(time_parts[2]) / 1000000
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[1]) then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    local retry_after = math.max(1, math.ceil(window - (now - tonumber(oldest[2]))))
    return {0, retry_after}
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('EXPIRE', KEYS[1], window + 1)
return {1, 0}
"""


class RequestBodyLimitMiddleware:
    """Reject oversized requests before the API parses their bodies."""

    def __init__(self, app, max_bytes: int):
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.lower(): value
            for key, value in scope.get("headers", [])
        }
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                content_length = int(raw_length)
            except ValueError:
                await self._reject(send, 400, "invalid Content-Length")
                return
            if content_length < 0:
                await self._reject(send, 400, "invalid Content-Length")
                return
            if content_length > self.max_bytes:
                await self._reject(send, 413, "request body too large")
                return

        messages = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            size += len(message.get("body", b""))
            if size > self.max_bytes:
                await self._reject(send, 413, "request body too large")
                return
            messages.append(message)
            if not message.get("more_body", False):
                break

        iterator = iter(messages)

        async def replay():
            try:
                return next(iterator)
            except StopIteration:
                return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _reject(send, status: int, detail: str) -> None:
        body = detail.encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class JobRateLimiter:
    """Thread-safe, per-client sliding window for expensive job submissions."""

    def __init__(
        self,
        max_requests: int,
        window_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
        redis_url: str | None = None,
    ):
        if max_requests <= 0 or window_seconds <= 0:
            raise ValueError("rate-limit values must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.clock = clock
        self.redis_url = redis_url
        self._redis_client = (
            redis.Redis.from_url(
                redis_url,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            if redis_url
            else None
        )
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def __call__(self, request: Request) -> None:
        client = request.client
        key = client.host if client else "unknown"
        self.check(key)

    def check(self, key: str) -> None:
        if self.redis_url:
            self._check_redis(key)
            return
        if os.environ.get("HEALTEST_ENV", "").strip().lower() == "production":
            raise HTTPException(status_code=503, detail="shared rate limiter unavailable")
        self._check_local(key)

    def _check_redis(self, identity: str) -> None:
        if self._redis_client is None:
            raise HTTPException(
                status_code=503, detail="shared rate limiter unavailable"
            )
        try:
            key_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            allowed, retry_after = self._redis_client.eval(
                _RATE_LIMIT_SCRIPT,
                1,
                f"healtest:job-rate:{key_hash}",
                self.max_requests,
                self.window_seconds,
                uuid.uuid4().hex,
            )
        except RedisError as exc:
            raise HTTPException(
                status_code=503, detail="shared rate limiter unavailable"
            ) from exc
        if not int(allowed):
            raise HTTPException(
                status_code=429,
                detail="job submission rate limit exceeded",
                headers={"Retry-After": str(int(retry_after))},
            )

    def _check_local(self, key: str) -> None:
        now = self.clock()
        cutoff = now - self.window_seconds
        with self._lock:
            for address in tuple(self._requests):
                entries = self._requests[address]
                while entries and entries[0] <= cutoff:
                    entries.popleft()
                if not entries:
                    del self._requests[address]

            entries = self._requests[key]
            if len(entries) >= self.max_requests:
                retry_after = max(
                    1,
                    math.ceil(self.window_seconds - (now - entries[0])),
                )
                raise HTTPException(
                    status_code=429,
                    detail="job submission rate limit exceeded",
                    headers={"Retry-After": str(retry_after)},
                )
            entries.append(now)
