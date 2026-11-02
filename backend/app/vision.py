"""SAM-compatible visual element localization and DOM mapping."""

from __future__ import annotations

import base64
import io
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class VisionError(RuntimeError):
    """Raised when visual localization cannot produce a usable mask."""


@dataclass(frozen=True)
class VisualHit:
    dom_index: int
    iou: float
    tag: str = ""
    text: str = ""
    element: dict[str, Any] = field(default_factory=dict)
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    mask_png: bytes = b""


def _settings() -> tuple[str, str, float]:
    endpoint = os.environ.get("SAM_API_URL", "").strip()
    api_key = os.environ.get("SAM_API_KEY", "").strip()
    if not endpoint:
        raise VisionError("SAM_API_URL must be set to a SAM-compatible segmentation endpoint")
    if not api_key:
        raise VisionError("SAM_API_KEY is required for visual localization")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VisionError("SAM_API_URL must be an absolute HTTP or HTTPS URL")
    try:
        timeout = float(os.environ.get("HEALTEST_MODEL_TIMEOUT_S", "30"))
    except ValueError as exc:
        raise VisionError("HEALTEST_MODEL_TIMEOUT_S must be a positive number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise VisionError("HEALTEST_MODEL_TIMEOUT_S must be a positive number")
    return endpoint, api_key, timeout


def _read_screenshot(path: str) -> tuple[bytes, tuple[int, int]]:
    try:
        image_bytes = Path(path).read_bytes()
    except OSError as exc:
        raise VisionError(f"could not read the captured screenshot: {exc}") from exc
    if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
        raise VisionError("captured screenshot is empty or exceeds the 10 MB limit")
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise VisionError("captured screenshot exceeds the 20 megapixel limit")
            image.load()
            return image_bytes, image.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise VisionError("captured screenshot is not a valid, supported image") from exc


def _request_mask(
    screenshot: bytes,
    target_description: str,
) -> bytes:
    endpoint, api_key, timeout = _settings()
    try:
        response = httpx.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "image": base64.b64encode(screenshot).decode("ascii"),
                "text_prompt": target_description,
            },
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise VisionError(
            f"SAM segmentation request failed ({type(exc).__name__})"
        ) from exc
    if not response.is_success:
        raise VisionError(f"SAM segmentation endpoint returned HTTP {response.status_code}")
    if len(response.content) > MAX_IMAGE_BYTES * 2:
        raise VisionError("SAM response exceeds the 20 MB limit")
    if (
        response.headers.get("content-type", "").split(";", 1)[0].lower() == "image/png"
        or response.content.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        mask_png = response.content
    else:
        try:
            payload = response.json()
        except ValueError as exc:
            raise VisionError("SAM endpoint must return a PNG or JSON containing mask_png") from exc
        encoded_mask = payload.get("mask_png") if isinstance(payload, dict) else None
        if not isinstance(encoded_mask, str) or not encoded_mask:
            raise VisionError("SAM endpoint response is missing its base64 mask_png field")
        if encoded_mask.startswith("data:image/png;base64,"):
            encoded_mask = encoded_mask.removeprefix("data:image/png;base64,")
        try:
            mask_png = base64.b64decode(encoded_mask, validate=True)
        except ValueError as exc:
            raise VisionError("SAM endpoint returned an invalid base64 mask_png") from exc
    if not mask_png or len(mask_png) > MAX_IMAGE_BYTES:
        raise VisionError("SAM mask is empty or exceeds the 10 MB limit")
    return mask_png


def _mask_bbox(mask_png: bytes, screenshot_size: tuple[int, int]) -> tuple[int, int, int, int]:
    try:
        with Image.open(io.BytesIO(mask_png)) as image:
            if image.size != screenshot_size:
                raise VisionError(
                    "SAM mask dimensions do not match the captured screenshot"
                )
            image.load()
            if image.mode == "RGBA" and image.getchannel("A").getextrema() != (255, 255):
                mask = image.getchannel("A")
            else:
                mask = image.convert("L")
            bounds = mask.point(lambda pixel: 255 if pixel > 0 else 0).getbbox()
    except VisionError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise VisionError("SAM endpoint returned an invalid PNG mask") from exc
    if bounds is None:
        raise VisionError("SAM segmentation mask is empty")
    return bounds


def _element_bbox(
    element: dict[str, Any],
    screenshot_size: tuple[int, int],
) -> tuple[float, float, float, float] | None:
    if element.get("visible") is False:
        return None
    values = (element.get("x"), element.get("y"), element.get("w"), element.get("h"))
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        return None
    x, y, width, height = (float(value) for value in values)
    if not all(math.isfinite(value) for value in (x, y, width, height)):
        return None
    if width <= 0 or height <= 0:
        return None
    viewport_width = element.get("viewport_width", screenshot_size[0])
    viewport_height = element.get("viewport_height", screenshot_size[1])
    if (
        isinstance(viewport_width, bool)
        or not isinstance(viewport_width, (int, float))
        or isinstance(viewport_height, bool)
        or not isinstance(viewport_height, (int, float))
        or not math.isfinite(viewport_width)
        or not math.isfinite(viewport_height)
        or viewport_width <= 0
        or viewport_height <= 0
    ):
        return None
    scale_x = screenshot_size[0] / viewport_width
    scale_y = screenshot_size[1] / viewport_height
    x *= scale_x
    y *= scale_y
    width *= scale_x
    height *= scale_y
    return (
        max(0.0, x),
        max(0.0, y),
        min(float(screenshot_size[0]), x + width),
        min(float(screenshot_size[1]), y + height),
    )


def iou(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
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


def locate(
    screenshot_path: str,
    target_description: str,
    elements: list[dict[str, Any]],
) -> VisualHit | None:
    """Ask the configured segmenter for a mask and map it to the best DOM box."""
    if not target_description.strip():
        raise VisionError("visual target description is empty")
    screenshot, screenshot_size = _read_screenshot(screenshot_path)
    mask_png = _request_mask(screenshot, target_description)
    bbox = _mask_bbox(mask_png, screenshot_size)
    mask_rect = tuple(float(value) for value in bbox)

    best: VisualHit | None = None
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            continue
        element_rect = _element_bbox(element, screenshot_size)
        if element_rect is None:
            continue
        overlap = iou(mask_rect, element_rect)
        if overlap > (best.iou if best else 0.0):
            best = VisualHit(
                dom_index=index,
                iou=overlap,
                tag=str(element.get("tag", "")),
                text=str(element.get("text", "")),
                element=element,
                bbox=bbox,
                mask_png=mask_png,
            )
    return best
