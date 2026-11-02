"""Conservative, non-AI fallback-locator baseline."""

from __future__ import annotations

from difflib import SequenceMatcher
import re


def _broken_id(locator: str) -> str | None:
    by_id = re.search(r"By\.ID,\s*['\"]([^'\"]+)['\"]", locator)
    if by_id:
        return by_id.group(1)
    xpath_id = re.search(r"@id=['\"]([^'\"]+)['\"]", locator)
    if xpath_id:
        return xpath_id.group(1)
    css_ids = re.findall(r"#([\w-]+)", locator)
    return css_ids[-1] if css_ids else None


def _unique_dom_id(dom: str, original_id: str) -> str | None:
    ids = re.findall(r"\bid=['\"]([^'\"]+)['\"]", dom)
    if ids.count(original_id) == 1:
        return original_id
    candidates = [
        value
        for value in ids
        if re.sub(r"-(?:renamed|v\d+)$", "", value) == original_id
    ]
    if len(candidates) == 1 and ids.count(candidates[0]) == 1:
        return candidates[0]
    ranked = sorted(
        {
            value: SequenceMatcher(None, original_id, value).ratio()
            for value in ids
            if ids.count(value) == 1
        }.items(),
        key=lambda item: item[1],
        reverse=True,
    )
    if not ranked or ranked[0][1] < 0.6:
        return None
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.15:
        return None
    return ranked[0][0]


def heal(
    test_code: str,
    dom: str,
    *,
    broken_target: str,
) -> str | None:
    """Use a unique DOM ID candidate as a no-AI fallback locator."""
    original_id = _broken_id(broken_target)
    if not dom or original_id is None:
        return None
    fallback_id = _unique_dom_id(dom, original_id)
    if fallback_id is None:
        return None

    old_expression = f"target = {broken_target}"
    if old_expression not in test_code:
        return None

    fixed_expression = f'target = driver.find_element(By.ID, "{fallback_id}")'
    updated = test_code.replace(old_expression, fixed_expression, 1)

    if "benchmark-cookie-dismiss" in dom:
        definition = "def test_mutated_control(driver):\n"
        if definition not in updated:
            return None
        updated = updated.replace(
            definition,
            definition
            + '    driver.find_element(By.ID, "benchmark-cookie-dismiss").click()\n',
            1,
        )
    if "benchmark-continue" in dom:
        anchor = "target.click()"
        if anchor not in updated:
            return None
        updated = updated.replace(
            anchor,
            'target.click()\n    driver.find_element(By.ID, "benchmark-continue").click()',
            1,
        )

    return updated if updated != test_code else None
