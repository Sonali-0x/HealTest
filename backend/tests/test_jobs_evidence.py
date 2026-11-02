from pathlib import Path
from types import SimpleNamespace

from app.jobs import REQUIRED_EVIDENCE, _persist_evidence
from app import store


class FakeDb:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)


def test_persist_evidence_creates_store_files_and_artifact_rows(tmp_path, monkeypatch):
    source = tmp_path / "capture"
    source.mkdir()
    files = {
        "error_trace": "error.txt",
        "failing_location": "location.txt",
        "page_url": "url.txt",
        "screenshot": "shot.png",
        "dom": "dom.html",
        "console": "console.log",
        "elements": "elements.json",
        "manifest": "manifest.json",
    }
    for kind, filename in files.items():
        (source / filename).write_text(kind, encoding="utf-8")
    monkeypatch.setenv("HEALTEST_DATA_DIR", str(tmp_path / "stored"))
    failure = SimpleNamespace(
        artifact_dir=str(source),
        manifest={"files": files, "capture_errors": []},
    )
    db = FakeDb()

    errors = _persist_evidence(db, failure, attempt_id=7)

    assert errors == []
    assert {row.type for row in db.added} == set(files)
    assert len(db.added) == len(files)
    assert all(Path(store.resolve(row.path)).is_file() for row in db.added)


def test_persist_evidence_reports_missing_required_items(tmp_path):
    failure = SimpleNamespace(artifact_dir=str(tmp_path), manifest={"files": {}})

    errors = _persist_evidence(FakeDb(), failure, attempt_id=2)

    assert errors == [f"required evidence missing: {', '.join(sorted(REQUIRED_EVIDENCE))}"]
