import base64
import io

import httpx
import pytest
from PIL import Image, ImageDraw

from app import vision


def _png(size: tuple[int, int], box: tuple[int, int, int, int] | None = None) -> bytes:
    image = Image.new("L", size, 0)
    if box:
        ImageDraw.Draw(image).rectangle(
            (box[0], box[1], box[2] - 1, box[3] - 1),
            fill=255,
        )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _set_settings(monkeypatch):
    monkeypatch.setenv("SAM_API_URL", "https://sam.example.test/segment")
    monkeypatch.setenv("SAM_API_KEY", "test-token")


def test_locate_sends_screenshot_and_description_then_selects_best_overlap(
    tmp_path, monkeypatch
):
    _set_settings(monkeypatch)
    screenshot = _png((100, 80))
    screenshot_path = tmp_path / "screenshot.png"
    screenshot_path.write_bytes(screenshot)
    mask = _png((100, 80), (20, 20, 50, 50))
    request = {}

    def fake_post(url, **kwargs):
        request.update(url=url, **kwargs)
        return httpx.Response(
            200,
            json={"mask_png": base64.b64encode(mask).decode("ascii")},
        )

    monkeypatch.setattr(vision.httpx, "post", fake_post)
    elements = [
        {"tag": "button", "id": "submit", "text": "Submit", "x": 20, "y": 20, "w": 30, "h": 30},
        {"tag": "a", "id": "cancel", "text": "Cancel", "x": 0, "y": 0, "w": 10, "h": 10},
        {"tag": "button", "id": "hidden", "text": "", "x": 20, "y": 20, "w": 30, "h": 30, "visible": False},
    ]

    result = vision.locate(str(screenshot_path), "blue Submit button", elements)

    assert result is not None
    assert result.dom_index == 0
    assert result.element["id"] == "submit"
    assert result.iou == 1
    assert result.bbox == (20, 20, 50, 50)
    assert result.mask_png == mask
    assert request["url"] == "https://sam.example.test/segment"
    assert request["headers"]["Authorization"] == "Bearer test-token"
    assert request["timeout"] == 30
    assert request["json"]["text_prompt"] == "blue Submit button"
    assert base64.b64decode(request["json"]["image"]) == screenshot


def test_locate_accepts_png_response_body(tmp_path, monkeypatch):
    _set_settings(monkeypatch)
    screenshot_path = tmp_path / "screenshot.png"
    screenshot_path.write_bytes(_png((40, 30)))
    mask = _png((40, 30), (5, 6, 15, 16))
    monkeypatch.setattr(
        vision.httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(
            200,
            content=mask,
            headers={"content-type": "application/octet-stream"},
        ),
    )

    result = vision.locate(
        str(screenshot_path),
        "primary action",
        [{"tag": "button", "x": 5, "y": 6, "w": 10, "h": 10}],
    )

    assert result is not None
    assert result.iou == 1


def test_locate_scales_css_bounds_to_screenshot_pixels(tmp_path, monkeypatch):
    _set_settings(monkeypatch)
    screenshot_path = tmp_path / "screenshot.png"
    screenshot_path.write_bytes(_png((100, 80)))
    mask = _png((100, 80), (20, 20, 50, 50))
    monkeypatch.setattr(
        vision.httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(
            200,
            json={"mask_png": base64.b64encode(mask).decode("ascii")},
        ),
    )

    result = vision.locate(
        str(screenshot_path),
        "Submit button",
        [
            {
                "tag": "button",
                "x": 10,
                "y": 10,
                "w": 15,
                "h": 15,
                "viewport_width": 50,
                "viewport_height": 40,
            }
        ],
    )

    assert result is not None
    assert result.iou == 1


def test_locate_returns_none_when_mask_does_not_overlap_a_visible_element(
    tmp_path, monkeypatch
):
    _set_settings(monkeypatch)
    screenshot_path = tmp_path / "screenshot.png"
    screenshot_path.write_bytes(_png((40, 30)))
    mask = _png((40, 30), (5, 6, 15, 16))
    monkeypatch.setattr(
        vision.httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(
            200,
            json={"mask_png": base64.b64encode(mask).decode("ascii")},
        ),
    )

    result = vision.locate(
        str(screenshot_path),
        "missing target",
        [{"tag": "button", "x": 20, "y": 10, "w": 10, "h": 10}],
    )

    assert result is None


def test_locate_requires_matching_nonempty_mask_dimensions(tmp_path, monkeypatch):
    _set_settings(monkeypatch)
    screenshot_path = tmp_path / "screenshot.png"
    screenshot_path.write_bytes(_png((40, 30)))
    mask = _png((20, 15), (5, 6, 15, 16))
    monkeypatch.setattr(
        vision.httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(
            200,
            json={"mask_png": base64.b64encode(mask).decode("ascii")},
        ),
    )

    with pytest.raises(vision.VisionError, match="dimensions do not match"):
        vision.locate(str(screenshot_path), "target", [])


def test_locate_reports_missing_endpoint_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("SAM_API_URL", raising=False)
    monkeypatch.setenv("SAM_API_KEY", "test-token")
    screenshot_path = tmp_path / "screenshot.png"
    screenshot_path.write_bytes(_png((10, 10)))

    with pytest.raises(vision.VisionError, match="SAM_API_URL"):
        vision.locate(str(screenshot_path), "target", [])


def test_locate_reports_endpoint_errors_without_falling_back_to_dom_only(
    tmp_path, monkeypatch
):
    _set_settings(monkeypatch)
    screenshot_path = tmp_path / "screenshot.png"
    screenshot_path.write_bytes(_png((10, 10)))
    monkeypatch.setattr(
        vision.httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(503),
    )

    with pytest.raises(vision.VisionError, match="HTTP 503"):
        vision.locate(
            str(screenshot_path),
            "target",
            [{"tag": "button", "x": 0, "y": 0, "w": 10, "h": 10}],
        )


def test_iou_handles_touching_and_overlapping_boxes():
    assert vision.iou((0, 0, 10, 10), (10, 0, 20, 10)) == 0
    assert vision.iou((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(1 / 3)
