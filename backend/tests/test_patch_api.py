import os

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import main, models, store
from app.patcher import build_diff


@pytest.fixture
def patch_context(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(engine)
    db = Session(engine)
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    original = 'driver.find_element(By.ID, "submit").click()\nassert True\n'
    (test_dir / "test_form.py").write_text(original, encoding="utf-8")
    project = models.Project(
        owner_sub="tenant-a",
        name="sample",
        app_url="http://localhost",
        test_path=str(test_dir),
    )
    db.add(project)
    db.commit()
    run = models.Run(project_id=project.id)
    db.add(run)
    db.commit()
    result = models.TestResult(run_id=run.id, test_name="test_form.py::test_submit")
    db.add(result)
    db.commit()
    patched = original.replace('"submit"', '"submit-btn"')
    diff = build_diff("tests/test_form.py", original, patched)
    patch = models.Patch(test_result_id=result.id, diff=diff, rationale="update locator")
    db.add(patch)
    db.commit()
    monkeypatch.setenv("HEALTEST_DATA_DIR", str(tmp_path / "store"))

    yield db, project, result, patch, test_dir, original, patched
    db.close()
    engine.dispose()


def test_approve_applies_only_to_test_copy(patch_context):
    db, _project, _result, patch, test_dir, original, patched = patch_context

    response = main.approve_patch(patch.id, "tenant-a", db)

    assert response.status == "approved"
    copy_root = store.resolve(response.approved_copy_path)
    assert os.path.isfile(os.path.join(copy_root, "test_form.py"))
    assert open(os.path.join(copy_root, "test_form.py"), encoding="utf-8").read() == patched
    assert (test_dir / "test_form.py").read_text(encoding="utf-8") == original
    assert main.approve_patch(patch.id, "tenant-a", db).approved_copy_path == response.approved_copy_path


def test_rejected_patch_cannot_be_approved(patch_context):
    db, _project, _result, patch, _test_dir, _original, _patched = patch_context
    main.reject_patch(patch.id, "tenant-a", db)

    with pytest.raises(HTTPException) as exc_info:
        main.approve_patch(patch.id, "tenant-a", db)

    assert exc_info.value.status_code == 409


def test_rejecting_approved_patch_is_conflict(patch_context):
    db, _project, _result, patch, _test_dir, _original, _patched = patch_context
    main.approve_patch(patch.id, "tenant-a", db)

    with pytest.raises(HTTPException) as exc_info:
        main.reject_patch(patch.id, "tenant-a", db)

    assert exc_info.value.status_code == 409


def test_invalid_patch_does_not_create_approved_copy(patch_context, tmp_path):
    db, _project, _result, patch, _test_dir, _original, _patched = patch_context
    patch.diff = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        main.approve_patch(patch.id, "tenant-a", db)

    assert exc_info.value.status_code == 422
    assert patch.status == "proposed"
    assert not (tmp_path / "store" / "approved" / f"patch-{patch.id}").exists()
