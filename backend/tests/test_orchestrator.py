import json

import pytest

from app import orchestrator, patcher, vision
from app.ai_gateway import client
from app.ai_gateway.schemas import Diagnosis, Fix

TEST_CODE = (
    'driver.find_element(By.ID, "submit").click()\n'
    'assert result == "ok"\n'
)
DOM = '<button id="submit-btn">Submit</button>'


@pytest.fixture(autouse=True)
def use_mock_gateway(monkeypatch):
    monkeypatch.setenv("HEALTEST_MOCK_MODE", "1")


def test_mock_loop_heals_only_after_successful_verification():
    result = orchestrator.run_once(
        "NoSuchElementException: #submit",
        DOM,
        TEST_CODE,
        verify=lambda diff: True,
        test_file="tests/test_x.py",
    )
    assert result.final_state == "DONE"
    assert result.diagnosis is not None
    assert result.fix is not None
    assert result.events == [
        "COLLECT", "DIAGNOSE", "FIX", "CHECK", "VERIFY", "DONE"
    ]
    assert result.attempts[0]["outcome"] == "healed"
    assert result.fix.diff.startswith("--- a/tests/test_x.py")


def test_verification_failure_retries_with_prior_attempt_context(monkeypatch):
    original_diagnose = client.diagnose_with_usage
    prior_context = []

    def diagnose(error, dom, prior=""):
        prior_context.append(prior)
        return original_diagnose(error, dom, prior)

    monkeypatch.setattr(client, "diagnose_with_usage", diagnose)
    verify_results = iter([False, True])
    result = orchestrator.run_once(
        "locator failed",
        DOM,
        TEST_CODE,
        verify=lambda diff: next(verify_results),
        test_file="tests/test_x.py",
    )

    assert result.final_state == "DONE"
    assert len(result.attempts) == 2
    assert result.attempts[0]["outcome"] == "verification failed"
    assert "Attempt 1 failed verification" in prior_context[1]
    assert result.attempts[1]["outcome"] == "healed"


def test_three_failed_verifications_escalate_after_three_attempts():
    calls = []

    def verify(diff):
        calls.append(diff)
        return "still failing"

    result = orchestrator.run_once(
        "locator failed", DOM, TEST_CODE, verify=verify, test_file="tests/test_x.py"
    )

    assert result.final_state == "ESCALATE"
    assert len(calls) == orchestrator.MAX_ATTEMPTS == 3
    assert len(result.attempts) == 3
    assert result.events.count("VERIFY") == 3
    assert result.events[-1] == "ESCALATE"


def test_flaky_verification_escalates_without_retrying_or_healing():
    result = orchestrator.run_once(
        "locator failed",
        DOM,
        TEST_CODE,
        verify=lambda diff: "flaky verification: attempt 1 passed; attempt 2 failed",
        test_file="tests/test_x.py",
    )

    assert result.final_state == "FLAKY"
    assert len(result.attempts) == 1
    assert result.attempts[0]["outcome"] == "flaky"


def test_unsafe_patch_is_rejected_without_verification(monkeypatch):
    unsafe = Fix(
        diff=patcher.build_diff(
            "tests/test_x.py",
            TEST_CODE,
            'driver.find_element(By.ID, "submit-btn").click()\n',
        ),
        rationale="remove assertion",
    )
    monkeypatch.setattr(
        client, "fix_with_usage", lambda *args, **kwargs: (unsafe, 0)
    )
    verified = []
    result = orchestrator.run_once(
        "locator failed",
        DOM,
        TEST_CODE,
        verify=lambda diff: verified.append(diff) or True,
        test_file="tests/test_x.py",
    )

    assert result.final_state == "ESCALATE"
    assert "assertion" in " ".join(result.safety_reasons)
    assert verified == []
    assert len(result.attempts) == 3
    assert all(attempt["outcome"] == "unsafe patch" for attempt in result.attempts)


def test_missing_verifier_never_reports_a_heal():
    result = orchestrator.run_once(
        "locator failed", DOM, TEST_CODE, test_file="tests/test_x.py"
    )

    assert result.final_state == "ESCALATE"
    assert result.error_message == "no rerun verifier was provided"


def test_suspected_real_bug_escalates_without_fix(monkeypatch):
    diagnosis = Diagnosis(
        failure_class="suspected real bug",
        confidence=0.98,
        explanation="The application behavior itself appears broken.",
        target_description="Checkout submit action",
        needs_visual=False,
    )
    monkeypatch.setattr(client, "diagnose_with_usage", lambda *args: (diagnosis, 17))
    monkeypatch.setattr(
        client,
        "fix_with_usage",
        lambda *args, **kwargs: pytest.fail("suspected application bugs must not produce patches"),
    )

    result = orchestrator.run_once("application error", DOM, TEST_CODE)

    assert result.final_state == "ESCALATE"
    assert result.diagnosis == diagnosis
    assert result.fix is None
    assert result.model_tokens == 17
    assert result.events == ["COLLECT", "DIAGNOSE", "ESCALATE"]


def test_gateway_failure_escalates_without_patch(monkeypatch):
    def fail_diagnosis(*args):
        raise client.DiagnosisError("invalid diagnosis")

    monkeypatch.setattr(client, "diagnose_with_usage", fail_diagnosis)
    monkeypatch.setattr(
        client,
        "fix_with_usage",
        lambda *args, **kwargs: pytest.fail("failed diagnosis must not produce a patch"),
    )

    result = orchestrator.run_once("error", DOM, TEST_CODE)

    assert result.final_state == "ESCALATE"
    assert result.diagnosis is None
    assert result.fix is None
    assert result.error_message == "invalid diagnosis"
    assert result.events == ["COLLECT", "DIAGNOSE", "ESCALATE"]


def test_visual_diagnosis_locates_element_and_passes_it_to_fix(monkeypatch):
    diagnosis = Diagnosis(
        failure_class="element moved",
        confidence=0.93,
        explanation="The target moved to a different position.",
        target_description="blue Submit button",
        needs_visual=True,
    )
    hit = vision.VisualHit(
        dom_index=0,
        iou=0.75,
        element={"tag": "button", "id": "submit-btn", "text": "Submit"},
        bbox=(20, 20, 50, 50),
        mask_png=b"mask-png",
    )
    fix = Fix(
        diff=patcher.build_diff(
            "tests/test_x.py",
            TEST_CODE,
            TEST_CODE.replace('"submit"', '"submit-btn"'),
        ),
        rationale="Use the visually located submit button.",
    )
    locator_calls = []
    fix_candidates = []
    monkeypatch.setattr(client, "diagnose_with_usage", lambda *args: (diagnosis, 4))

    def locate(target_description):
        locator_calls.append(target_description)
        return hit

    def fix_with_usage(*args, element="", **kwargs):
        fix_candidates.append(json.loads(element))
        return fix, 6

    monkeypatch.setattr(client, "fix_with_usage", fix_with_usage)

    result = orchestrator.run_once(
        "NoSuchElementException: submit",
        DOM,
        TEST_CODE,
        verify=lambda diff: True,
        test_file="tests/test_x.py",
        visual_locator=locate,
    )

    assert result.final_state == "DONE"
    assert result.events == [
        "COLLECT", "DIAGNOSE", "LOCATE", "FIX", "CHECK", "VERIFY", "DONE"
    ]
    assert locator_calls == ["blue Submit button"]
    assert fix_candidates == [
        {
            "element": {"tag": "button", "id": "submit-btn", "text": "Submit"},
            "intersection_over_union": 0.75,
            "mask_bbox": [20, 20, 50, 50],
        }
    ]
    assert result.attempts[0]["visual_hit"] is hit
    assert result.attempts[0]["outcome"] == "healed"


def test_visual_diagnosis_escalates_instead_of_fixing_without_a_match(monkeypatch):
    diagnosis = Diagnosis(
        failure_class="element moved",
        confidence=0.93,
        explanation="The target moved.",
        target_description="Submit button",
        needs_visual=True,
    )
    monkeypatch.setattr(client, "diagnose_with_usage", lambda *args: (diagnosis, 4))
    monkeypatch.setattr(
        client,
        "fix_with_usage",
        lambda *args, **kwargs: pytest.fail("must not fix without a visual match"),
    )

    result = orchestrator.run_once(
        "locator failed",
        DOM,
        TEST_CODE,
        visual_locator=lambda target_description: None,
    )

    assert result.final_state == "ESCALATE"
    assert result.error_message == "visual mask did not overlap any captured DOM element"
    assert result.events == ["COLLECT", "DIAGNOSE", "LOCATE", "ESCALATE"]
    assert result.attempts[0]["outcome"] == "visual location failed"


def test_visual_endpoint_failure_escalates_without_dom_only_fallback(monkeypatch):
    diagnosis = Diagnosis(
        failure_class="element moved",
        confidence=0.93,
        explanation="The target moved.",
        target_description="Submit button",
        needs_visual=True,
    )
    monkeypatch.setattr(client, "diagnose_with_usage", lambda *args: (diagnosis, 4))
    monkeypatch.setattr(
        client,
        "fix_with_usage",
        lambda *args, **kwargs: pytest.fail("must not bypass unavailable visual fallback"),
    )

    def fail_visual(target_description):
        raise vision.VisionError("SAM_API_URL must be configured")

    result = orchestrator.run_once(
        "locator failed",
        DOM,
        TEST_CODE,
        visual_locator=fail_visual,
    )

    assert result.final_state == "ESCALATE"
    assert result.error_message == "SAM_API_URL must be configured"
    assert result.attempts[0]["visual_error"] == "SAM_API_URL must be configured"
    assert result.attempts[0]["outcome"] == "visual location failed"
