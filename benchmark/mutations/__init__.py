"""Deterministic, isolated UI mutations used by the benchmark."""

from __future__ import annotations

import re
from dataclasses import dataclass


MUTATION_TYPES = (
    "renamed_id",
    "button_text",
    "moved_element",
    "new_wrapper",
    "cookie_modal",
    "extra_flow_step",
    "button_to_link",
)


@dataclass(frozen=True)
class Target:
    element_id: str
    parent_selector: str
    label_prefix: str
    scenario: str


TARGETS = {
    "todo": Target("add-btn", "body", "Add", "todo_add"),
    "shop": Target("cart-btn", "header", "Cart", "shop_cart"),
    "multistep-form": Target("next-1", "#step-1", "Next", "form_step1"),
}

SECONDARY_TARGETS = {
    "todo": Target("clear-completed", "body > div", "Clear completed", "todo_clear"),
    "shop": Target("checkout-btn", "#cart-drawer", "Checkout", "shop_checkout"),
    "multistep-form": Target("next-2", "#step-2", "Next", "form_step2"),
}


def all_targets(app: str) -> tuple[Target, Target]:
    return TARGETS[app], SECONDARY_TARGETS[app]


class MutationError(ValueError):
    """Raised when a mutation cannot be applied to its expected fixture."""


def _element_pattern(element_id: str) -> re.Pattern[str]:
    return re.compile(
        rf"<(?P<tag>[a-zA-Z][\w:-]*)\b(?P<attrs>[^>]*\bid=['\"]{re.escape(element_id)}['\"][^>]*)>"
        rf"(?P<body>.*?)</(?P=tag)\s*>",
        re.DOTALL | re.IGNORECASE,
    )


def apply_mutation(html: str, mutation_type: str, target: Target) -> str:
    """Return a mutated document, raising instead of silently skipping a case."""
    if mutation_type not in MUTATION_TYPES:
        raise MutationError(f"unknown mutation type: {mutation_type}")

    element_id = target.element_id
    pattern = _element_pattern(element_id)
    match = pattern.search(html)
    if match is None:
        raise MutationError(f"target element #{element_id} was not found")

    if mutation_type == "renamed_id":
        replacement_id = f"{element_id}-renamed"
        changed = html.replace(
            f'id="{element_id}"', f'id="{replacement_id}"', 1
        ).replace(
            f"id='{element_id}'", f"id='{replacement_id}'", 1
        )
        if changed == html:
            raise MutationError(f"could not rename id #{element_id}")
        return changed.replace(f'"{element_id}"', f'"{replacement_id}"')

    if mutation_type == "button_text":
        body = match.group("body")
        if target.label_prefix not in body:
            raise MutationError(
                f"expected label {target.label_prefix!r} in #{element_id}"
            )
        changed_body = body.replace(target.label_prefix, f"Updated {target.label_prefix}", 1)
        return html[: match.start("body")] + changed_body + html[match.end("body") :]

    if mutation_type in ("moved_element", "new_wrapper"):
        wrapper = "benchmark-moved" if mutation_type == "moved_element" else "benchmark-wrapper"
        element = match.group(0)
        wrapped = f'<div class="{wrapper}">{element}</div>'
        return html[: match.start()] + wrapped + html[match.end() :]

    if mutation_type == "cookie_modal":
        overlay = """
<div id="benchmark-cookie-modal" style="position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.8);display:grid;place-items:center">
  <button id="benchmark-cookie-dismiss" type="button" onclick="document.getElementById('benchmark-cookie-modal').remove()">Accept cookies</button>
</div>
"""
        if "</body>" not in html:
            raise MutationError("document has no closing body tag")
        return html.replace("</body>", overlay + "</body>", 1)

    if mutation_type == "extra_flow_step":
        step = f"""
<div id="benchmark-extra-step" style="position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.8);display:none;place-items:center">
  <button id="benchmark-continue" type="button">Continue</button>
</div>
<script>
(function () {{
  var target = document.getElementById("{element_id}");
  var gate = document.getElementById("benchmark-extra-step");
  var released = false;
  target.addEventListener("click", function (event) {{
    if (released) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    gate.style.display = "grid";
  }}, true);
  document.getElementById("benchmark-continue").addEventListener("click", function () {{
    gate.remove();
    released = true;
    target.click();
  }});
}})();
</script>
"""
        if "</body>" not in html:
            raise MutationError("document has no closing body tag")
        return html.replace("</body>", step + "</body>", 1)

    if mutation_type == "button_to_link":
        tag = match.group("tag")
        element = match.group(0)
        linked = re.sub(rf"^<{tag}\b", "<a", element, count=1, flags=re.IGNORECASE)
        linked = re.sub(rf"</{tag}\s*>$", "</a>", linked, count=1, flags=re.IGNORECASE)
        if linked == element:
            raise MutationError(f"could not replace button #{element_id}")
        return html[: match.start()] + linked + html[match.end() :]

    raise MutationError(f"mutation {mutation_type!r} is not implemented")
