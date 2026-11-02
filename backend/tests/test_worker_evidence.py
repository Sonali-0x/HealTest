import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


PLUGIN_PATH = Path(__file__).resolve().parents[2] / "worker" / "conftest_plugin.py"
SPEC = importlib.util.spec_from_file_location("healtest_worker_plugin", PLUGIN_PATH)
plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plugin)


class FakeDriver:
    page_source = (
        "<html><head><script>secret()</script></head><body>"
        '<button id="submit">Submit</button></body></html>'
    )
    current_url = "http://sample-apps:8901/todo/"

    def execute_cdp_cmd(self, command, options):
        return {"data": base64.b64encode(b"png-data").decode("ascii")}

    def get_log(self, log_type):
        return [{"level": "SEVERE", "timestamp": 1, "message": "browser error"}]

    def execute_script(self, script, max_elements):
        assert "viewport_width: window.innerWidth" in script
        return [
            {
                "tag": "button",
                "id": "submit",
                "x": 1,
                "y": 2,
                "w": 3,
                "h": 4,
                "viewport_width": 800,
                "viewport_height": 600,
            }
        ]


def test_capture_writes_all_failure_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv(plugin.ARTIFACT_ENV, str(tmp_path))
    item = SimpleNamespace(
        nodeid="tests/test_todo.py::test_submit",
        funcargs={"driver": FakeDriver()},
    )
    traceback = (
        "tests/test_todo.py:12: in test_submit\n"
        "E   Unable to locate element: #submit\n"
        "E   selenium.common.exceptions.NoSuchElementException"
    )

    outdir = Path(plugin._capture(item, traceback))
    manifest = json.loads((outdir / "manifest.json").read_text(encoding="utf-8"))

    assert set(manifest["files"]) == {
        "screenshot", "dom", "console", "elements", "error_trace",
        "failing_location", "page_url", "manifest",
    }
    assert manifest["capture_errors"] == []
    assert (outdir / "screenshot.png").read_bytes() == b"png-data"
    dom = (outdir / "dom.html").read_text(encoding="utf-8")
    assert "secret()" not in dom
    assert '<button id="submit">' in dom
    assert (outdir / "error_trace.txt").read_text(encoding="utf-8") == traceback
    assert (outdir / "failing_location.txt").read_text(encoding="utf-8") == "tests/test_todo.py:12"
    assert (outdir / "page_url.txt").read_text(encoding="utf-8") == FakeDriver.current_url
    assert "browser error" in (outdir / "console.log").read_text(encoding="utf-8")
    element = json.loads((outdir / "elements.json").read_text(encoding="utf-8"))[0]
    assert element["id"] == "submit"
    assert element["viewport_width"] == 800
