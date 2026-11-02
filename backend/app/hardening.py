"""Request-size and job-submission limits for the demo API."""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import HTTPException, Request


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
    ):
        if max_requests <= 0 or window_seconds <= 0:
            raise ValueError("rate-limit values must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.clock = clock
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def __call__(self, request: Request) -> None:
        client = request.client
        key = client.host if client else "unknown"
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
