"""Model gateway with offline mocks and configurable Muse Spark/Code endpoints."""

from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Any

import httpx
from pydantic import ValidationError

from . import mock
from .prompts import SYSTEM_DIAGNOSE, SYSTEM_FIX, build_diagnose_prompt, build_fix_prompt
from .schemas import Diagnosis, Fix

log = logging.getLogger("healtest.ai_gateway")


class DiagnosisError(RuntimeError):
    """Raised when a diagnosis cannot be obtained or validated."""


class FixError(RuntimeError):
    """Raised when a patch cannot be obtained or validated."""


def mock_mode() -> bool:
    configured = os.environ.get("HEALTEST_MOCK_MODE", "").strip()
    if configured == "0":
        return False
    if configured == "1":
        return True
    if configured:
        raise DiagnosisError("HEALTEST_MOCK_MODE must be '0' or '1'")
    return not os.environ.get("MUSE_SPARK_API_KEY")


def _spark_settings() -> tuple[str, str, str, float]:
    api_key = os.environ.get("MUSE_SPARK_API_KEY", "").strip()
    endpoint = os.environ.get("MUSE_SPARK_API_URL", "").strip()
    model = os.environ.get("MUSE_SPARK_MODEL", "").strip()
    if not api_key:
        raise DiagnosisError("MUSE_SPARK_API_KEY is required when mock mode is disabled")
    if not endpoint:
        raise DiagnosisError(
            "MUSE_SPARK_API_URL must be set to the provider's chat-completions endpoint"
        )
    if not model:
        raise DiagnosisError("MUSE_SPARK_MODEL must be set to the provider's model identifier")
    try:
        timeout = float(os.environ.get("HEALTEST_MODEL_TIMEOUT_S", "30"))
    except ValueError as exc:
        raise DiagnosisError("HEALTEST_MODEL_TIMEOUT_S must be a positive number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise DiagnosisError("HEALTEST_MODEL_TIMEOUT_S must be a positive number")
    return api_key, endpoint, model, timeout


def _code_settings() -> tuple[str, str, str, float]:
    api_key = os.environ.get("MUSE_CODE_API_KEY", "").strip()
    endpoint = os.environ.get("MUSE_CODE_API_URL", "").strip()
    model = os.environ.get("MUSE_CODE_MODEL", "").strip()
    if not api_key:
        raise FixError("MUSE_CODE_API_KEY is required when mock mode is disabled")
    if not endpoint:
        raise FixError(
            "MUSE_CODE_API_URL must be set to the provider's chat-completions endpoint"
        )
    if not model:
        raise FixError("MUSE_CODE_MODEL must be set to the provider's model identifier")
    try:
        timeout = float(os.environ.get("HEALTEST_MODEL_TIMEOUT_S", "30"))
    except ValueError as exc:
        raise FixError("HEALTEST_MODEL_TIMEOUT_S must be a positive number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise FixError("HEALTEST_MODEL_TIMEOUT_S must be a positive number")
    return api_key, endpoint, model, timeout


def _content_from_response(payload: Any) -> tuple[str, int]:
    if not isinstance(payload, dict):
        raise DiagnosisError("Muse Spark response must be a JSON object")
    token_count = 0
    if "usage" in payload:
        usage = payload["usage"]
        if not isinstance(usage, dict):
            raise DiagnosisError("Muse Spark returned invalid token usage metadata")
        if "total_tokens" in usage:
            token_count = usage["total_tokens"]
        elif "prompt_tokens" in usage and "completion_tokens" in usage:
            prompt_tokens = usage["prompt_tokens"]
            completion_tokens = usage["completion_tokens"]
            if type(prompt_tokens) is not int or type(completion_tokens) is not int:
                raise DiagnosisError("Muse Spark returned invalid token usage metadata")
            token_count = prompt_tokens + completion_tokens
        else:
            raise DiagnosisError("Muse Spark returned invalid token usage metadata")
    if type(token_count) is not int or token_count < 0:
        raise DiagnosisError("Muse Spark returned invalid token usage metadata")
    tokens = token_count

    content = payload.get("output_text")
    if isinstance(content, str):
        return content, tokens

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        message = choice.get("message") if isinstance(choice, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            return content, tokens
        if isinstance(content, list):
            text = "".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            )
            if text:
                return text, tokens

    raise DiagnosisError("Muse Spark response did not contain text output")


def _response_excerpt(value: Any) -> str:
    if isinstance(value, str):
        response = value
    else:
        response = json.dumps(value, ensure_ascii=True, default=str)
    return response[:8_000]


def _send_chat_completion(
    prompt: str,
    provider: str = "spark",
    system_prompt: str = SYSTEM_DIAGNOSE,
) -> tuple[str, int]:
    if provider == "spark":
        api_key, endpoint, model, timeout = _spark_settings()
        provider_name = "Muse Spark"
    elif provider == "code":
        api_key, endpoint, model, timeout = _code_settings()
        provider_name = "Muse Code"
    else:
        raise ValueError(f"unknown model provider: {provider}")
    model_name = f"muse-{provider}"
    request_body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }

    for attempt in range(2):
        started = time.monotonic()
        try:
            response = httpx.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json=request_body,
                timeout=timeout,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            log.warning(
                "model_call model=%s request_ok=false "
                "tokens=0 duration_ms=%d prompt=%r response=%r",
                model_name,
                duration_ms,
                prompt,
                type(exc).__name__,
            )
            if attempt == 0:
                log.warning("%s request failed; retrying once (%s)", provider_name, type(exc).__name__)
                continue
            message = f"{provider_name} request failed after one retry: {type(exc).__name__}"
            error_type = DiagnosisError if provider == "spark" else FixError
            raise error_type(message) from exc
        except httpx.InvalidURL as exc:
            log.warning(
                "model_call model=%s request_ok=false "
                "tokens=0 prompt=%r response=%r",
                model_name,
                prompt,
                type(exc).__name__,
            )
            setting = "MUSE_SPARK_API_URL" if provider == "spark" else "MUSE_CODE_API_URL"
            error_type = DiagnosisError if provider == "spark" else FixError
            raise error_type(f"{setting} is invalid") from exc
        if response.is_error:
            log.warning(
                "model_call model=%s request_ok=false "
                "status=%d tokens=0 prompt=%r response=%r",
                model_name,
                response.status_code,
                prompt,
                _response_excerpt(response.text),
            )
            error_type = DiagnosisError if provider == "spark" else FixError
            raise error_type(f"{provider_name} returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            log.warning(
                "model_call model=%s request_ok=false "
                "status=%d tokens=0 prompt=%r response=%r",
                model_name,
                response.status_code,
                prompt,
                _response_excerpt(response.text),
            )
            error_type = DiagnosisError if provider == "spark" else FixError
            raise error_type(f"{provider_name} response was not valid JSON") from exc
        try:
            return _content_from_response(payload)
        except DiagnosisError as exc:
            log.warning(
                "model_call model=%s request_ok=false "
                "status=%d tokens=0 prompt=%r response=%r",
                model_name,
                response.status_code,
                prompt,
                _response_excerpt(payload),
            )
            if provider == "code":
                raise FixError(str(exc)) from exc
            raise

    error_type = DiagnosisError if provider == "spark" else FixError
    raise error_type(f"{provider_name} request failed")


def _parse_diagnosis(raw: str) -> Diagnosis:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DiagnosisError(f"diagnosis was not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise DiagnosisError("diagnosis JSON must be an object")
    try:
        return Diagnosis.model_validate(payload)
    except ValidationError as exc:
        raise DiagnosisError(f"diagnosis did not match the required schema: {exc}") from exc


def _live_diagnose(error: str, dom_excerpt: str, prior: str = "") -> tuple[Diagnosis, int]:
    repair_response = ""
    total_tokens = 0
    for parse_attempt in range(2):
        prompt = build_diagnose_prompt(
            error, dom_excerpt, prior, repair_response=repair_response
        )
        started = time.monotonic()
        response, tokens = _send_chat_completion(prompt)
        total_tokens += tokens
        duration_ms = int((time.monotonic() - started) * 1000)
        try:
            diagnosis = _parse_diagnosis(response)
        except DiagnosisError as exc:
            log.warning(
                "model_call model=muse-spark operation=diagnose parse_valid=false "
                "tokens=%d duration_ms=%d prompt=%r response=%r",
                tokens,
                duration_ms,
                prompt,
                response,
            )
            if parse_attempt == 0:
                repair_response = response
                continue
            raise DiagnosisError(
                f"Muse Spark diagnosis remained invalid after one repair request: {exc}"
            ) from exc

        log.info(
            "model_call model=muse-spark operation=diagnose parse_valid=true "
            "tokens=%d duration_ms=%d prompt=%r response=%r",
            tokens,
            duration_ms,
            prompt,
            response,
        )
        return diagnosis, total_tokens
    raise DiagnosisError("Muse Spark diagnosis failed")


def diagnose_with_usage(error: str, dom_excerpt: str, prior: str = "") -> tuple[Diagnosis, int]:
    if mock_mode():
        result = mock.mock_diagnose(error, dom_excerpt, prior)
        log.info(
            "model_call model=mock-diagnose operation=diagnose tokens=0 prompt=%r response=%r",
            build_diagnose_prompt(error, dom_excerpt, prior),
            result.model_dump(),
        )
        return result, 0
    return _live_diagnose(error, dom_excerpt, prior)


def diagnose(error: str, dom_excerpt: str, prior: str = "") -> Diagnosis:
    diagnosis, _ = diagnose_with_usage(error, dom_excerpt, prior)
    return diagnosis


def _parse_fix(raw: str) -> Fix:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FixError(f"patch response was not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise FixError("patch response JSON must be an object")
    try:
        result = Fix.model_validate(payload)
    except ValidationError as exc:
        raise FixError(f"patch response did not match the required schema: {exc}") from exc
    if not result.diff:
        raise FixError("Muse Code returned an empty unified diff")
    return result


def fix_with_usage(
    test_code: str,
    diagnosis: str,
    element: str = "",
    test_file: str = "tests/test_x.py",
    prior: str = "",
) -> tuple[Fix, int]:
    started = time.monotonic()
    if mock_mode():
        result = mock.mock_fix(test_code, diagnosis, element, test_file, prior)
        if not result.diff:
            log.info(
                "model_call model=mock-fix operation=fix request_ok=false "
                "tokens=0 duration_ms=%d prompt=%r response=%r",
                int((time.monotonic() - started) * 1000),
                build_fix_prompt(test_code, diagnosis, element, prior, test_file),
                result.model_dump(),
            )
            raise FixError(
                result.rationale or "mock fixer could not produce a patch for this fixture"
            )
        log.info(
            "model_call model=mock-fix operation=fix tokens=0 duration_ms=%d prompt=%r response=%r",
            int((time.monotonic() - started) * 1000),
            build_fix_prompt(test_code, diagnosis, element, prior, test_file),
            result.model_dump(),
        )
        return result, 0

    prompt = build_fix_prompt(test_code, diagnosis, element, prior, test_file)
    response, tokens = _send_chat_completion(
        prompt,
        provider="code",
        system_prompt=SYSTEM_FIX,
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    try:
        result = _parse_fix(response)
    except FixError:
        log.warning(
            "model_call model=muse-code operation=fix parse_valid=false "
            "tokens=%d duration_ms=%d prompt=%r response=%r",
            tokens,
            duration_ms,
            prompt,
            response,
        )
        raise
    log.info(
        "model_call model=muse-code operation=fix parse_valid=true "
        "tokens=%d duration_ms=%d prompt=%r response=%r",
        tokens,
        duration_ms,
        prompt,
        response,
    )
    return result, tokens


def fix(
    test_code: str,
    diagnosis: str,
    element: str = "",
    test_file: str = "tests/test_x.py",
    prior: str = "",
) -> Fix:
    result, _ = fix_with_usage(test_code, diagnosis, element, test_file, prior)
    return result
