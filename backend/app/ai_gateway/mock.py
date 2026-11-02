"""Mock model for offline development and CI."""

from __future__ import annotations

from .. import patcher
from .schemas import Diagnosis, Fix

MOCK_DIAGNOSIS = Diagnosis(
    failure_class="locator changed",
    confidence=0.9,
    explanation="Mock: submit button id changed from #submit to #submit-btn.",
    target_description="blue Submit button at the bottom of the form",
    needs_visual=False,
)

def mock_diagnose(error: str = "", dom_excerpt: str = "", prior: str = "") -> Diagnosis:
    _ = (error, dom_excerpt, prior)
    return MOCK_DIAGNOSIS.model_copy()


def mock_fix(
    test_code: str = "",
    diagnosis: str = "",
    element: str = "",
    test_file: str = "tests/test_x.py",
    prior: str = "",
) -> Fix:
    _ = (diagnosis, element, prior)
    if 'By.ID, "submit"' not in test_code:
        return Fix(diff="", rationale="Mock could not identify a supported locator to update.")
    updated = test_code.replace('By.ID, "submit"', 'By.ID, "submit-btn"', 1)
    return Fix(
        diff=patcher.build_diff(test_file, test_code, updated),
        rationale="Mock: update the renamed submit-button locator.",
    )
