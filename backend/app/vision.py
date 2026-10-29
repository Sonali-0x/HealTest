"""SAM 3.1 visual fallback (implemented in Phase 5; contract fixed here)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VisualHit:
    dom_index: int
    iou: float
    tag: str = ""
    text: str = ""


def locate(screenshot_path: str, target_description: str, elements: list[dict]) -> VisualHit | None:
    """Map a SAM mask to the DOM element with max IoU. Stub until Phase 5."""
    _ = (screenshot_path, target_description, elements)
    return None


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union else 0.0
