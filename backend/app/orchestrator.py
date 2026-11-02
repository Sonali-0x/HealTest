"""Agent state machine entry point for one failing test."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from . import patcher, safety, vision
from .ai_gateway import client as gateway
from .ai_gateway.schemas import Diagnosis, Fix

STATES = ("COLLECT", "DIAGNOSE", "LOCATE", "FIX", "CHECK", "VERIFY", "DONE", "ESCALATE")
MAX_ATTEMPTS = 3


@dataclass
class LoopResult:
    final_state: str
    diagnosis: Diagnosis | None = None
    fix: Fix | None = None
    safety_reasons: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    error_message: str = ""
    model_tokens: int = 0
    attempts: list[dict] = field(default_factory=list)


def run_once(
    error: str,
    dom: str,
    test_code: str,
    verify: Callable[[str], bool | str] | None = None,
    test_file: str = "tests/test_x.py",
    max_attempts: int = MAX_ATTEMPTS,
    visual_locator: Callable[[str], vision.VisualHit | None] | None = None,
) -> LoopResult:
    if not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise ValueError(f"max_attempts must be between 1 and {MAX_ATTEMPTS}")

    events = ["COLLECT"]
    attempts: list[dict] = []
    model_tokens = 0
    last_diagnosis: Diagnosis | None = None
    last_fix: Fix | None = None
    last_safety_reasons: list[str] = []
    retry_context: list[str] = []

    for number in range(1, max_attempts + 1):
        events.append("DIAGNOSE")
        try:
            diagnosis, tokens = gateway.diagnose_with_usage(
                error, dom, "\n\n".join(retry_context)
            )
        except gateway.DiagnosisError as exc:
            events.append("ESCALATE")
            return LoopResult(
                "ESCALATE",
                diagnosis=last_diagnosis,
                fix=last_fix,
                safety_reasons=last_safety_reasons,
                events=events,
                error_message=str(exc),
                model_tokens=model_tokens,
                attempts=attempts,
            )

        model_tokens += tokens
        last_diagnosis = diagnosis
        attempt: dict = {
            "number": number,
            "diagnosis": diagnosis,
            "model_tokens": tokens,
            "fix": None,
            "visual_hit": None,
            "visual_error": "",
            "safety_reasons": [],
            "verification_error": "",
            "outcome": "pending",
        }
        attempts.append(attempt)
        if diagnosis.failure_class == "suspected real bug":
            attempt["outcome"] = "suspected real bug"
            events.append("ESCALATE")
            return LoopResult(
                "ESCALATE",
                diagnosis=diagnosis,
                events=events,
                model_tokens=model_tokens,
                attempts=attempts,
            )

        if diagnosis.needs_visual:
            events.append("LOCATE")
            visual_error = ""
            visual_hit: vision.VisualHit | None = None
            if visual_locator is None:
                visual_error = "visual localization is required but no locator is configured"
            else:
                try:
                    visual_hit = visual_locator(diagnosis.target_description)
                except vision.VisionError as exc:
                    visual_error = str(exc)
                else:
                    if visual_hit is None:
                        visual_error = "visual mask did not overlap any captured DOM element"
                    else:
                        attempt["visual_hit"] = visual_hit
            if visual_error:
                attempt["outcome"] = "visual location failed"
                attempt["visual_error"] = visual_error
                events.append("ESCALATE")
                return LoopResult(
                    "ESCALATE",
                    diagnosis=diagnosis,
                    events=events,
                    error_message=visual_error,
                    model_tokens=model_tokens,
                    attempts=attempts,
                )
            assert visual_hit is not None
            candidate_element = json.dumps(
                {
                    "element": visual_hit.element,
                    "intersection_over_union": round(visual_hit.iou, 4),
                    "mask_bbox": visual_hit.bbox,
                },
                ensure_ascii=False,
            )
        else:
            candidate_element = ""
        events.append("FIX")
        try:
            fix, fix_tokens = gateway.fix_with_usage(
                test_code,
                diagnosis.explanation,
                element=candidate_element,
                test_file=test_file,
                prior="\n\n".join(retry_context),
            )
        except gateway.FixError as exc:
            attempt["outcome"] = "fix failed"
            attempt["verification_error"] = str(exc)
            events.append("ESCALATE")
            return LoopResult(
                "ESCALATE",
                diagnosis=diagnosis,
                events=events,
                error_message=str(exc),
                model_tokens=model_tokens,
                attempts=attempts,
            )
        model_tokens += fix_tokens
        attempt["model_tokens"] += fix_tokens
        last_fix = fix
        attempt["fix"] = fix
        events.append("CHECK")
        try:
            patched_files = patcher.apply_diff_to_files(
                {test_file.removeprefix("tests/"): test_code},
                fix.diff,
                strip_prefix="tests",
            )
        except patcher.PatchError as exc:
            reasons = [f"invalid patch: {exc}"]
        else:
            reasons = safety.check_all(
                fix.diff,
                {test_file.removeprefix("tests/"): test_code},
                patched_files,
                dom,
            )
        last_safety_reasons = reasons
        attempt["safety_reasons"] = reasons
        if reasons:
            attempt["outcome"] = "unsafe patch"
            retry_context.append(
                f"Attempt {number} was rejected by safety checks: {'; '.join(reasons)}"
            )
            continue

        events.append("VERIFY")
        if verify is None:
            verification_error = "no rerun verifier was provided"
        else:
            verification_result = verify(fix.diff)
            verification_error = (
                "" if verification_result is True
                else str(verification_result) if isinstance(verification_result, str)
                else "patched test did not pass"
            )
        attempt["verification_error"] = verification_error
        if not verification_error:
            attempt["outcome"] = "healed"
            events.append("DONE")
            return LoopResult(
                "DONE",
                diagnosis=diagnosis,
                fix=fix,
                events=events,
                model_tokens=model_tokens,
                attempts=attempts,
            )
        if verification_error.startswith("flaky verification:"):
            attempt["outcome"] = "flaky"
            events.append("ESCALATE")
            return LoopResult(
                "FLAKY",
                diagnosis=diagnosis,
                fix=fix,
                events=events,
                error_message=verification_error,
                model_tokens=model_tokens,
                attempts=attempts,
            )
        attempt["outcome"] = "verification failed"
        retry_context.append(
            f"Attempt {number} failed verification: {verification_error}\n"
            f"Rejected diff:\n{fix.diff}"
        )

    events.append("ESCALATE")
    final_error = "maximum repair attempts exhausted"
    if attempts and attempts[-1]["verification_error"]:
        final_error = attempts[-1]["verification_error"]
    return LoopResult(
        "ESCALATE",
        diagnosis=last_diagnosis,
        fix=last_fix,
        safety_reasons=last_safety_reasons,
        events=events,
        error_message=final_error,
        model_tokens=model_tokens,
        attempts=attempts,
    )
