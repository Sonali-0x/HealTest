"""Single entry point for model calls: mock now, live clients in Phase 2/3.

Every call logs prompt/response/token count (PRD FR-15). Until live keys
are configured, all calls route to the mock gateway.
"""

from __future__ import annotations

import logging
import os
import time

from . import mock
from .schemas import Diagnosis, Fix

log = logging.getLogger("healtest.ai_gateway")


def mock_mode() -> bool:
    if os.environ.get("HEALTEST_MOCK_MODE") == "0":
        return False
    if os.environ.get("HEALTEST_MOCK_MODE") == "1":
        return True
    return not os.environ.get("MUSE_SPARK_API_KEY")


def diagnose(error: str, dom_excerpt: str, prior: str = "") -> Diagnosis:
    started = time.monotonic()
    result = mock.mock_diagnose(error, dom_excerpt, prior)
    log.info(
        "model_call model=mock-diagnose tokens=0 duration_ms=%d",
        int((time.monotonic() - started) * 1000),
    )
    return result


def fix(test_code: str, diagnosis: str, element: str = "") -> Fix:
    started = time.monotonic()
    result = mock.mock_fix(test_code, diagnosis, element)
    log.info(
        "model_call model=mock-fix tokens=0 duration_ms=%d",
        int((time.monotonic() - started) * 1000),
    )
    return result
