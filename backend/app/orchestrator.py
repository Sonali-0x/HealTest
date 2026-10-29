"""Agent state machine skeleton (PRD section 6.3).

Phase 0: drives COLLECT → DIAGNOSE → FIX → CHECK → VERIFY → DONE with the
mock gateway and an injected verify hook, persisting one attempt + patch.
Worker execution, retries and visual fallback arrive in later phases.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from . import safety
from .ai_gateway import client as gateway
from .ai_gateway.schemas import Diagnosis, Fix

STATES = ("COLLECT", "DIAGNOSE", "LOCATE", "FIX", "CHECK", "VERIFY", "DONE", "ESCALATE")


@dataclass
class LoopResult:
    final_state: str
    diagnosis: Diagnosis | None = None
    fix: Fix | None = None
    safety_reasons: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)


def run_once(
    error: str,
    dom: str,
    test_code: str,
    verify: Callable[[str], bool] | None = None,
) -> LoopResult:
    events: list[str] = []

    def emit(state: str) -> None:
        events.append(state)

    started = time.monotonic()
    emit("COLLECT")
    emit("DIAGNOSE")
    diagnosis = gateway.diagnose(error, dom)
    if diagnosis.failure_class == "suspected real bug":
        emit("ESCALATE")
        return LoopResult("ESCALATE", diagnosis=diagnosis, events=events)

    if diagnosis.needs_visual:
        emit("LOCATE")  # Phase 5 fills in SAM call
    emit("FIX")
    fix = gateway.fix(test_code, diagnosis.explanation)
    emit("CHECK")
    reasons = safety.check_all(fix.diff, {"tests/test_x.py": test_code}, {"tests/test_x.py": test_code})
    if reasons:
        emit("ESCALATE")
        return LoopResult("ESCALATE", diagnosis, fix, reasons, events)

    emit("VERIFY")
    ok = verify(fix.diff) if verify else True
    _ = time.monotonic() - started
    if ok:
        emit("DONE")
        return LoopResult("DONE", diagnosis, fix, [], events)
    emit("ESCALATE")
    return LoopResult("ESCALATE", diagnosis, fix, [], events)
