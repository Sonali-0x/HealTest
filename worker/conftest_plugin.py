"""HealTest evidence plugin: capture screenshot/DOM/console/bboxes on failure.

Loaded by the runner with ``pytest -p conftest_plugin``. Standalone suite
runs (no ``-p`` flag) are unaffected.

For every failed ``call`` phase with an available Selenium driver, writes::

    $HEALTEST_ARTIFACT_DIR/<safe-nodeid>/
        manifest.json      # test name, url, failing line, file index
        screenshot.png     # full page when CDP allows, else viewport
        dom.html           # page source at failure time (capped)
        console.log        # browser console entries
        elements.json      # interactive elements with bounding boxes

The plugin never fails a test run: every capture step is best-effort.

It also writes a JSONL result log (one line per call-phase report) to
``$HEALTEST_REPORT_LOG``, which is how the runner learns pass/fail
counts and failure tracebacks (pytest core no longer ships --report-log).
"""

from __future__ import annotations

import base64
import json
import os
import re
import traceback

import pytest

from app.evidence import extract_locator_hint, parse_failing_location, trim_dom

ARTIFACT_ENV = "HEALTEST_ARTIFACT_DIR"
REPORT_ENV = "HEALTEST_REPORT_LOG"
MAX_ERROR_CHARS = 20_000
MAX_CONSOLE_ENTRIES = 500
MAX_ELEMENTS = 300

_BBOX_JS = """
return Array.from(document.querySelectorAll(
  'a,button,input,select,textarea,[role="button"],[onclick]'
)).slice(0, arguments[0]).map(function (el) {
  var r = el.getBoundingClientRect();
  return {
    tag: el.tagName.toLowerCase(),
    id: el.id || null,
    name: el.name || null,
    text: (el.innerText || el.value || '').slice(0, 80),
    x: Math.round(r.x), y: Math.round(r.y),
    w: Math.round(r.width), h: Math.round(r.height),
    viewport_width: window.innerWidth,
    viewport_height: window.innerHeight,
    visible: r.width > 0 && r.height > 0
  };
});
"""


def _artifact_root() -> str:
    root = os.environ.get(ARTIFACT_ENV) or os.path.join(os.getcwd(), ".healtest-artifacts")
    os.makedirs(root, exist_ok=True)
    return root


def _safe_name(nodeid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid).strip("_")[:180] or "test"


def _driver_for(item):
    try:
        if hasattr(item, "funcargs") and "driver" in item.funcargs:
            return item.funcargs["driver"]
    except Exception:
        pass
    inst = getattr(item, "instance", None)
    drv = getattr(inst, "driver", None)
    if drv is not None:
        return drv
    return getattr(getattr(inst, "cls", None), "driver", None)


def _screenshot_png(driver) -> bytes | None:
    try:
        shot = driver.execute_cdp_cmd(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": True, "fromSurface": True},
        )
        data = (shot or {}).get("data")
        if data:
            return base64.b64decode(data)
    except Exception:
        pass
    try:
        return driver.get_screenshot_as_png()
    except Exception:
        return None


def _console_text(driver) -> str:
    try:
        entries = driver.get_log("browser") or []
    except Exception:
        return ""
    lines = []
    for e in entries[:MAX_CONSOLE_ENTRIES]:
        lines.append(f"{e.get('level', '?')} {e.get('timestamp', '')} {e.get('message', '')}")
    if len(entries) > MAX_CONSOLE_ENTRIES:
        lines.append(f"... truncated ({len(entries)} total entries)")
    return "\n".join(lines) + ("\n" if lines else "")


def _elements(driver) -> list:
    try:
        return driver.execute_script(_BBOX_JS, MAX_ELEMENTS) or []
    except Exception:
        return []


def _capture(item, longrepr_text: str) -> str | None:
    driver = _driver_for(item)
    outdir = os.path.join(_artifact_root(), _safe_name(item.nodeid))
    os.makedirs(outdir, exist_ok=True)
    files: dict[str, str | None] = {
        "screenshot": None,
        "dom": None,
        "console": None,
        "elements": None,
        "error_trace": "error_trace.txt",
        "failing_location": "failing_location.txt",
        "page_url": "page_url.txt",
    }
    capture_errors: list[str] = []
    with open(os.path.join(outdir, "error_trace.txt"), "w", encoding="utf-8") as fh:
        fh.write(longrepr_text[:MAX_ERROR_CHARS])
    failing_file, failing_line = parse_failing_location(longrepr_text)
    failing_location = f"{failing_file}:{failing_line}" if failing_file else ""
    if not failing_location:
        capture_errors.append("could not identify failing test location")
    with open(os.path.join(outdir, "failing_location.txt"), "w", encoding="utf-8") as fh:
        fh.write(failing_location)

    url = ""
    if driver is None:
        capture_errors.append("Selenium WebDriver was unavailable")
    else:
        png = _screenshot_png(driver)
        if png:
            with open(os.path.join(outdir, "screenshot.png"), "wb") as fh:
                fh.write(png)
            files["screenshot"] = "screenshot.png"
        else:
            capture_errors.append("Could not capture screenshot")

        try:
            dom = driver.page_source or ""
        except Exception as exc:
            dom = ""
            capture_errors.append(f"Could not capture DOM: {exc}")
        locator_hint = extract_locator_hint(longrepr_text)
        trimmed_dom = trim_dom(dom, focus=locator_hint)
        with open(os.path.join(outdir, "dom.html"), "w", encoding="utf-8") as fh:
            fh.write(trimmed_dom)
        files["dom"] = "dom.html"

        console = _console_text(driver)
        with open(os.path.join(outdir, "console.log"), "w", encoding="utf-8") as fh:
            fh.write(console)
        files["console"] = "console.log"

        with open(os.path.join(outdir, "elements.json"), "w", encoding="utf-8") as fh:
            json.dump(_elements(driver), fh)
        files["elements"] = "elements.json"

        try:
            url = driver.current_url
        except Exception as exc:
            capture_errors.append(f"Could not capture page URL: {exc}")
        if not url:
            capture_errors.append("page URL was empty")

    with open(os.path.join(outdir, "page_url.txt"), "w", encoding="utf-8") as fh:
        fh.write(url)

    manifest = {
        "test_name": item.nodeid,
        "url": url,
        "failing_line": failing_location,
        "error_head": longrepr_text[:2000],
        "files": files,
        "capture_errors": capture_errors,
    }
    files["manifest"] = "manifest.json"
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return outdir


_REPORTS: list = []


def _outcome_of(rep) -> str:
    if getattr(rep, "passed", False):
        return "passed"
    if getattr(rep, "failed", False):
        return "failed"
    return "skipped"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """Hook wrapper: record the report; capture evidence when it failed."""
    rep = yield
    if rep.when == "call":
        try:
            longrepr_text = str(rep.longrepr)
        except Exception:
            longrepr_text = ""
        _REPORTS.append(
            {
                "nodeid": rep.nodeid,
                "when": rep.when,
                "outcome": _outcome_of(rep),
                "longrepr": longrepr_text,
            }
        )
        if rep.failed:
            try:
                outdir = _capture(item, longrepr_text)
                if outdir:
                    rep.sections.append(("HealTest artifacts", outdir))
            except Exception:
                traceback.print_exc()
    return rep


def pytest_sessionfinish(session, exitstatus):
    """Write the JSONL result log for the runner (best-effort)."""
    _ = (session, exitstatus)
    try:
        path = os.environ.get(REPORT_ENV) or os.path.join(_artifact_root(), "report.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for entry in _REPORTS:
                fh.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        traceback.print_exc()
