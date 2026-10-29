"""Mock model for offline dev and CI (PRD risk mitigation).

Returns a fixed, valid diagnosis + diff so the loop, safety checks,
dashboard and benchmark harness work without model access.
"""

from __future__ import annotations

from .schemas import Diagnosis, Fix

MOCK_DIAGNOSIS = Diagnosis(
    failure_class="locator changed",
    confidence=0.9,
    explanation="Mock: submit button id changed from #submit to #submit-btn.",
    target_description="blue Submit button at the bottom of the form",
    needs_visual=False,
)

MOCK_FIX = Fix(
    diff=(
        "--- a/tests/test_form.py\n"
        "+++ b/tests/test_form.py\n"
        "@@ -10,1 +10,1 @@\n"
        '-    driver.find_element(By.ID, "submit").click()\n'
        '+    driver.find_element(By.ID, "submit-btn").click()\n'
    ),
    rationale="Mock: update renamed locator.",
)


def mock_diagnose(error: str = "", dom_excerpt: str = "", prior: str = "") -> Diagnosis:
    _ = (error, dom_excerpt, prior)
    return MOCK_DIAGNOSIS.model_copy()


def mock_fix(test_code: str = "", diagnosis: str = "", element: str = "") -> Fix:
    _ = (test_code, diagnosis, element)
    return MOCK_FIX.model_copy()
