import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from benchmark import heuristic_baseline, report
from benchmark.mutations import MUTATION_TYPES, TARGETS, all_targets, apply_mutation
from benchmark import runner as benchmark_runner
from benchmark.runner import _model_mode, _test_code

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "app,target",
    [
        (app, target)
        for app in TARGETS
        for target in all_targets(app)
    ],
)
@pytest.mark.parametrize("mutation_type", MUTATION_TYPES)
def test_mutations_apply_to_every_sample_app(app, target, mutation_type):
    source = (ROOT / "sample_apps" / app / "index.html").read_text(encoding="utf-8")

    changed = apply_mutation(source, mutation_type, target)

    assert changed != source
    if mutation_type == "renamed_id":
        assert f'id="{target.element_id}-renamed"' in changed
    elif mutation_type == "button_text":
        assert f"Updated {target.label_prefix}" in changed
    elif mutation_type == "moved_element":
        assert "benchmark-moved" in changed
    elif mutation_type == "new_wrapper":
        assert "benchmark-wrapper" in changed
    elif mutation_type == "cookie_modal":
        assert "benchmark-cookie-dismiss" in changed
    elif mutation_type == "extra_flow_step":
        assert "benchmark-continue" in changed
    elif mutation_type == "button_to_link":
        assert f'<a id="{target.element_id}"' in changed


@pytest.mark.parametrize("app", TARGETS)
@pytest.mark.parametrize("mutation_type", MUTATION_TYPES)
def test_generated_case_is_valid_python(app, mutation_type):
    for target in all_targets(app):
        code, _ = _test_code(mutation_type, target)

        ast.parse(code)


def test_heuristic_only_uses_unique_dom_fallback_and_preserves_assertion():
    test_code = (
        'def test_case(driver):\n'
        '    target = driver.find_element(By.ID, "add-btn")\n'
        '    target.click()\n'
        '    assert "must remain" == "must remain"\n'
    )
    dom = '<button id="add-btn-renamed">Add</button>'

    fixed = heuristic_baseline.heal(
        test_code,
        dom,
        broken_target='driver.find_element(By.ID, "add-btn")',
    )

    assert fixed is not None
    assert 'By.ID, "add-btn-renamed"' in fixed
    assert 'assert "must remain" == "must remain"' in fixed
    assert heuristic_baseline.heal(
        test_code,
        dom + '<button id="add-btn-renamed">Duplicate</button>',
        broken_target='driver.find_element(By.ID, "add-btn")',
    ) is None


def test_heuristic_recognizes_modal_controls_from_the_captured_dom():
    test_code = (
        "def test_mutated_control(driver):\n"
        '    target = driver.find_element(By.ID, "add-btn")\n'
        "    target.click()\n"
    )
    dom = (
        '<button id="add-btn">Add</button>'
        '<button id="benchmark-cookie-dismiss">Accept</button>'
        '<button id="benchmark-continue">Continue</button>'
    )

    fixed = heuristic_baseline.heal(
        test_code,
        dom,
        broken_target='driver.find_element(By.ID, "add-btn")',
    )

    assert fixed is not None
    assert fixed.index("benchmark-cookie-dismiss") < fixed.index("target.click()")
    assert fixed.index("benchmark-continue") > fixed.index("target.click()")


def test_report_summarizes_metrics_and_keeps_failures_visible():
    cases = [
        {
            "case_id": "one",
            "mutation_type": "renamed_id",
            "arm": "no_healing",
            "healed": False,
            "seconds": 2,
            "attempts": 0,
            "error": "locator missing",
        },
        {
            "case_id": "one",
            "mutation_type": "renamed_id",
            "arm": "heuristic",
            "healed": True,
            "seconds": 4,
            "attempts": 1,
        },
        {
            "case_id": "one",
            "mutation_type": "renamed_id",
            "arm": "healtest",
            "healed": False,
            "seconds": 6,
            "attempts": 2,
            "tokens": 120,
            "unsafe_fixes": 1,
            "error": "model patch rejected",
        },
    ]

    summary = report.summarize(cases)

    assert summary["arms"]["no_healing"]["heal_rate"] == 0
    assert summary["arms"]["heuristic"]["heal_rate"] == 1
    assert summary["arms"]["healtest"]["avg_attempts"] == 2
    assert summary["arms"]["healtest"]["unsafe_fixes"] == 1
    assert summary["failures"] == [
        {
            "case_id": "one",
            "app": "",
            "mutation_type": "renamed_id",
            "arm": "no_healing",
            "error": "locator missing",
        },
        {
            "case_id": "one",
            "app": "",
            "mutation_type": "renamed_id",
            "arm": "healtest",
            "error": "model patch rejected",
        },
    ]
    assert "<svg" in report.render_svg(summary)


def test_report_writes_machine_readable_json_and_svg(tmp_path):
    summary = report.summarize([])
    json_path = tmp_path / "nested" / "results.json"
    svg_path = tmp_path / "nested" / "results.svg"

    report.write_report(summary, str(json_path), str(svg_path))

    assert json.loads(json_path.read_text(encoding="utf-8")) == summary
    assert "<svg" in svg_path.read_text(encoding="utf-8")


def test_model_mode_is_reported_without_exposing_credentials(monkeypatch):
    monkeypatch.delenv("HEALTEST_MOCK_MODE", raising=False)
    monkeypatch.delenv("MUSE_SPARK_API_KEY", raising=False)
    assert _model_mode() == ("mock", False)

    monkeypatch.setenv("HEALTEST_MOCK_MODE", "0")
    monkeypatch.setenv("MUSE_SPARK_API_KEY", "configured")
    monkeypatch.setenv("MUSE_SPARK_API_URL", "https://provider.invalid")
    monkeypatch.setenv("MUSE_SPARK_MODEL", "spark")
    monkeypatch.setenv("MUSE_CODE_API_KEY", "configured")
    monkeypatch.setenv("MUSE_CODE_API_URL", "https://provider.invalid")
    monkeypatch.setenv("MUSE_CODE_MODEL", "code")
    assert _model_mode() == ("live", True)
    monkeypatch.setenv("HEALTEST_MOCK_MODE", "unexpected")
    assert _model_mode() == ("invalid", False)


def test_local_healtest_benchmark_initializes_database_schema(tmp_path, monkeypatch):
    import sys

    sys.path.insert(0, str(ROOT / "backend"))
    from app import jobs, models

    engine = create_engine(
        f"sqlite:///{tmp_path / 'local-benchmark.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    init_calls = []

    def initialize_schema():
        init_calls.append(True)
        models.Base.metadata.create_all(engine)

    def execute_run(run_id):
        with sessions() as db:
            run = db.get(models.Run, run_id)
            run.status = "healed"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
        return {"status": "healed"}

    monkeypatch.setattr(models, "SessionLocal", sessions)
    monkeypatch.setattr(models, "init_db", initialize_schema)
    monkeypatch.setattr(jobs, "execute_run", execute_run)

    try:
        outcome, attempts, tokens, unsafe_fixes, run_id = benchmark_runner._run_healtest(
            tmp_path, "http://localhost", None, "database-init-test"
        )
        assert outcome["passed"] is True
        assert attempts == tokens == unsafe_fixes == 0
        assert run_id is not None
        assert init_calls == [True]
    finally:
        engine.dispose()
