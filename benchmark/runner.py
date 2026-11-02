"""Seeded benchmark execution for no-healing, heuristic, and HealTest arms."""

from __future__ import annotations

import argparse
from importlib import import_module
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import textwrap
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from benchmark import heuristic_baseline, report
from benchmark.mutations import (
    MUTATION_TYPES,
    TARGETS,
    MutationError,
    Target,
    all_targets,
    apply_mutation,
)

APPS = tuple(TARGETS)
TEST_TARGET = "test_benchmark.py::test_mutated_control"


class BenchmarkError(RuntimeError):
    """Raised when a benchmark case cannot be prepared or executed."""


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        _ = (format, args)


class _AppServer:
    def __init__(self, directory: str):
        handler = partial(_QuietHandler, directory=directory)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self) -> "_AppServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        _ = (exc_type, exc, traceback)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _sample_apps_root() -> Path:
    configured = os.environ.get("HEALTEST_SAMPLE_APPS_DIR", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.extend((_repository_root() / "sample_apps", Path("/sample_apps")))
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    raise BenchmarkError(
        "sample apps were not found; set HEALTEST_SAMPLE_APPS_DIR or mount /sample_apps"
    )


def _model_mode() -> tuple[str, bool]:
    configured_mode = os.environ.get("HEALTEST_MOCK_MODE", "").strip()
    if configured_mode not in ("", "0", "1"):
        return "invalid", False
    mock_mode = configured_mode == "1" or (
        not configured_mode and not os.environ.get("MUSE_SPARK_API_KEY", "").strip()
    )
    required_provider_settings = (
        "MUSE_SPARK_API_KEY",
        "MUSE_SPARK_API_URL",
        "MUSE_SPARK_MODEL",
        "MUSE_CODE_API_KEY",
        "MUSE_CODE_API_URL",
        "MUSE_CODE_MODEL",
    )
    providers_configured = all(
        os.environ.get(key, "").strip() for key in required_provider_settings
    )
    return ("mock" if mock_mode else "live"), providers_configured


def _test_code(mutation_type: str, target: Target) -> tuple[str, str]:
    target_id = target.element_id
    if mutation_type == "renamed_id":
        broken_target = f'driver.find_element(By.ID, "{target_id}")'
    elif mutation_type == "button_text":
        broken_target = (
            f'driver.find_element(By.XPATH, "//button[@id=\'{target_id}\' '
            f'and starts-with(normalize-space(.), \'{target.label_prefix}\')]")'
        )
    elif mutation_type in ("moved_element", "new_wrapper"):
        broken_target = (
            "driver.find_element(By.CSS_SELECTOR, "
            f'"{target.parent_selector} > #{target_id}")'
        )
    elif mutation_type == "button_to_link":
        broken_target = (
            "driver.find_element(By.CSS_SELECTOR, "
            f'"button#{target_id}")'
        )
    else:
        broken_target = f'driver.find_element(By.ID, "{target_id}")'

    setup = ""
    assertion = ""
    if target.scenario == "todo_add":
        setup = 'driver.find_element(By.ID, "new-todo").send_keys("benchmark task")'
        assertion = (
            'WebDriverWait(driver, 2).until('
            'lambda d: len(d.find_elements(By.CSS_SELECTOR, "#todo-list li")) == 1)'
        )
    elif target.scenario == "todo_clear":
        setup = "\n".join(
            (
                'driver.find_element(By.ID, "new-todo").send_keys("completed task")',
                'driver.find_element(By.ID, "add-btn").click()',
                'driver.find_element(By.CSS_SELECTOR, ".todo-toggle").click()',
            )
        )
        assertion = (
            'WebDriverWait(driver, 2).until('
            'lambda d: len(d.find_elements(By.CSS_SELECTOR, "#todo-list li")) == 0)'
        )
    elif target.scenario == "shop_cart":
        assertion = (
            'WebDriverWait(driver, 2).until('
            'lambda d: "open" in d.find_element(By.ID, "cart-drawer").get_attribute("class"))'
        )
    elif target.scenario == "shop_checkout":
        setup = "\n".join(
            (
                'driver.find_element(By.CSS_SELECTOR, ".add-to-cart").click()',
                'driver.find_element(By.ID, "cart-btn").click()',
            )
        )
        assertion = (
            'WebDriverWait(driver, 2).until('
            'lambda d: d.find_element(By.ID, "checkout-form").is_displayed())'
        )
    elif target.scenario == "form_step1":
        setup = "\n".join(
            (
                'driver.find_element(By.ID, "first-name").send_keys("Ada")',
                'driver.find_element(By.ID, "last-name").send_keys("Lovelace")',
                'driver.find_element(By.ID, "email").send_keys("ada@example.com")',
            )
        )
        assertion = (
            'WebDriverWait(driver, 2).until('
            'lambda d: d.find_element(By.ID, "step-2").is_displayed())'
        )
    else:
        setup = "\n".join(
            (
                'driver.find_element(By.ID, "first-name").send_keys("Ada")',
                'driver.find_element(By.ID, "last-name").send_keys("Lovelace")',
                'driver.find_element(By.ID, "email").send_keys("ada@example.com")',
                'driver.find_element(By.ID, "next-1").click()',
                'driver.find_element(By.ID, "street").send_keys("1 Main St")',
                'driver.find_element(By.ID, "city").send_keys("Springfield")',
                'Select(driver.find_element(By.ID, "country")).select_by_value("US")',
            )
        )
        assertion = (
            'WebDriverWait(driver, 2).until('
            'lambda d: d.find_element(By.ID, "step-3").is_displayed())'
        )

    setup_code = textwrap.indent(setup, "    ") if setup else "    pass"
    code = f'''"""Generated benchmark case; do not edit by hand."""
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait


def test_mutated_control(driver):
{setup_code}
    target = {broken_target}
    target.click()
    {assertion}
'''
    return code, broken_target


def _prepare_case(
    app: str,
    mutation_type: str,
    target: Target,
    source_root: Path,
    work: Path,
) -> tuple[Path, Path, str, str]:
    app_source = source_root / app
    html_path = app_source / "index.html"
    fixture_path = app_source / "tests" / "conftest.py"
    if not html_path.is_file() or not fixture_path.is_file():
        raise BenchmarkError(f"sample app {app!r} is missing its page or test fixture")

    try:
        changed_html = apply_mutation(
            html_path.read_text(encoding="utf-8"),
            mutation_type,
            target,
        )
    except (OSError, MutationError) as exc:
        raise BenchmarkError(f"could not apply {mutation_type} to {app}: {exc}") from exc

    app_root = work / "apps"
    app_dir = app_root / app
    app_dir.mkdir(parents=True)
    (app_dir / "index.html").write_text(changed_html, encoding="utf-8")

    test_dir = work / "tests"
    test_dir.mkdir()
    shutil.copy2(fixture_path, test_dir / "conftest.py")
    test_code, broken_target = _test_code(mutation_type, target)
    test_path = test_dir / "test_benchmark.py"
    test_path.write_text(test_code, encoding="utf-8")
    return app_root, test_dir, test_code, broken_target


def _run_test_case(test_dir: Path, app_url: str, artifact_dir: Path) -> dict[str, Any]:
    backend_dir = _repository_root() / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    runner_module = import_module("app.runner")

    try:
        summary = runner_module.run_tests(
            str(test_dir),
            app_url,
            str(artifact_dir),
            timeout_s=90,
            test_target=TEST_TARGET,
        )
    except (runner_module.RunnerUnavailable, OSError) as exc:
        return {
            "passed": False,
            "total": 0,
            "seconds": 0.0,
            "error": str(exc),
            "dom": "",
        }

    failure = summary.failed[0] if summary.failed else None
    dom_path = (
        Path(failure.artifact_dir) / "dom.html"
        if failure is not None and failure.artifact_dir
        else None
    )
    try:
        dom = dom_path.read_text(encoding="utf-8", errors="replace") if dom_path else ""
    except OSError:
        dom = ""
    error = ""
    if failure is not None:
        error = failure.error_text[:2000]
    elif summary.errors:
        error = "; ".join(summary.errors)[:2000]
    elif summary.total != 1:
        error = f"expected one test result, received {summary.total}"
    return {
        "passed": summary.total == 1 and summary.passed == 1 and not summary.failed,
        "total": summary.total,
        "seconds": summary.duration_s,
        "error": error,
        "dom": dom,
    }


def _case_row(
    case_id: str,
    app: str,
    mutation_type: str,
    arm: str,
    outcome: dict[str, Any],
    *,
    healed: bool,
    attempts: int = 0,
    tokens: int = 0,
    unsafe_fixes: int = 0,
    run_id: int | None = None,
    price_per_1k_tokens: float | None = None,
) -> dict[str, Any]:
    cost = (
        tokens / 1000 * price_per_1k_tokens
        if price_per_1k_tokens is not None
        else None
    )
    return {
        "case_id": case_id,
        "app": app,
        "mutation_type": mutation_type,
        "arm": arm,
        "healed": healed,
        "attempts": attempts,
        "seconds": round(float(outcome.get("seconds", 0.0)), 3),
        "unsafe_fixes": unsafe_fixes,
        "tokens": tokens,
        "cost": cost,
        "run_id": run_id,
        "error": "" if healed else str(outcome.get("error", "")),
    }


def _run_healtest(
    test_dir: Path,
    app_url: str,
    benchmark_id: int | None,
    case_id: str,
) -> tuple[dict[str, Any], int, int, int, int]:
    backend_dir = _repository_root() / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    jobs = import_module("app.jobs")
    models = import_module("app.models")

    models.init_db()
    db = models.SessionLocal()
    started = time.monotonic()
    try:
        project = models.Project(
            name=f"Benchmark {benchmark_id or 'local'}: {case_id}",
            app_url=app_url,
            test_path=str(test_dir),
            retry_limit=3,
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        run = models.Run(project_id=project.id, status="running")
        db.add(run)
        db.commit()
        db.refresh(run)
        # The benchmark is executing trusted, generated fixtures inside the worker.
        original_sandbox = os.environ.get("HEALTEST_SANDBOX")
        os.environ["HEALTEST_SANDBOX"] = "local"
        try:
            result = jobs.execute_run(run.id)
        finally:
            if original_sandbox is None:
                os.environ.pop("HEALTEST_SANDBOX", None)
            else:
                os.environ["HEALTEST_SANDBOX"] = original_sandbox

        db.expire_all()
        persisted_run = db.get(models.Run, run.id)
        results = (
            db.query(models.TestResult).filter_by(run_id=run.id).all()
            if persisted_run
            else []
        )
        attempts = 0
        tokens = 0
        unsafe_fixes = 0
        for test_result in results:
            attempts_for_result = (
                db.query(models.Attempt)
                .filter_by(test_result_id=test_result.id)
                .all()
            )
            attempts += len(attempts_for_result)
            tokens += sum(attempt.model_tokens for attempt in attempts_for_result)
            unsafe_fixes += (
                db.query(models.Patch)
                .filter_by(test_result_id=test_result.id, status="rejected")
                .count()
            )
        healed = bool(persisted_run and persisted_run.status == "healed")
        duration = (
            max(0.0, (persisted_run.finished_at - persisted_run.started_at).total_seconds())
            if persisted_run and persisted_run.finished_at and persisted_run.started_at
            else time.monotonic() - started
        )
        error = "" if healed else (
            (persisted_run.error if persisted_run else "")
            or str(result.get("error", "HealTest did not heal this case"))
        )
        return (
            {"passed": healed, "seconds": duration, "error": error},
            attempts,
            tokens,
            unsafe_fixes,
            run.id,
        )
    finally:
        db.close()


def run(config: dict) -> dict:
    """Run the matrix locally in the worker's isolation boundary."""
    original_sandbox = os.environ.get("HEALTEST_SANDBOX")
    os.environ["HEALTEST_SANDBOX"] = "local"
    try:
        return _run_matrix(config)
    finally:
        if original_sandbox is None:
            os.environ.pop("HEALTEST_SANDBOX", None)
        else:
            os.environ["HEALTEST_SANDBOX"] = original_sandbox


def _run_matrix(config: dict) -> dict:
    """Execute a seeded mutation matrix and return the full report payload."""
    seed = config.get("seed", 42)
    if type(seed) is not int:
        raise BenchmarkError("seed must be an integer")
    apps = config.get("apps", list(APPS))
    mutation_types = config.get("mutation_types", list(MUTATION_TYPES))
    if not isinstance(apps, list) or not apps or any(app not in APPS for app in apps):
        raise BenchmarkError(f"apps must be a non-empty subset of {', '.join(APPS)}")
    if (
        not isinstance(mutation_types, list)
        or not mutation_types
        or any(kind not in MUTATION_TYPES for kind in mutation_types)
    ):
        raise BenchmarkError("mutation_types contains an unknown or empty selection")

    price = config.get("cost_per_1k_tokens")
    if price is not None and (
        isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0
    ):
        raise BenchmarkError("cost_per_1k_tokens must be a non-negative number")
    price = float(price) if price is not None else None

    matrix = [
        (app, mutation_type, target_index, target)
        for app in apps
        for mutation_type in mutation_types
        for target_index, target in enumerate(all_targets(app))
    ]
    random.Random(seed).shuffle(matrix)
    sample_apps = _sample_apps_root()
    all_cases: list[dict[str, Any]] = []
    benchmark_id = config.get("benchmark_id")

    for ordinal, (app, mutation_type, target_index, target) in enumerate(matrix, start=1):
        case_id = f"{ordinal:03d}-{app}-{mutation_type}-{target_index + 1}"
        with tempfile.TemporaryDirectory(prefix=f"healtest-bench-{case_id}-") as temp:
            work = Path(temp)
            app_root, test_dir, original_code, broken_target = _prepare_case(
                app, mutation_type, target, sample_apps, work
            )
            with _AppServer(str(app_root)) as server:
                artifact_root = work / "artifacts"
                no_heal = _run_test_case(
                    test_dir, server.url, artifact_root / "no_healing"
                )
                if no_heal["passed"]:
                    no_heal["error"] = "mutation did not break the generated control test"
                all_cases.append(
                    _case_row(
                        case_id,
                        app,
                        mutation_type,
                        "no_healing",
                        no_heal,
                        healed=False,
                        price_per_1k_tokens=price,
                    )
                )

                heuristic_test_dir = work / "heuristic-tests"
                heuristic_test_dir.mkdir()
                shutil.copy2(test_dir / "conftest.py", heuristic_test_dir / "conftest.py")
                dom = no_heal.get("dom", "")
                fixed = heuristic_baseline.heal(
                    original_code,
                    dom,
                    broken_target=broken_target,
                )
                heuristic_outcome: dict[str, Any]
                heuristic_attempts = 0
                if fixed is None:
                    heuristic_outcome = {
                        "passed": False,
                        "seconds": 0.0,
                        "error": "heuristic found no unique stable fallback",
                    }
                else:
                    (heuristic_test_dir / "test_benchmark.py").write_text(
                        fixed, encoding="utf-8"
                    )
                    heuristic_outcome = _run_test_case(
                        heuristic_test_dir,
                        server.url,
                        artifact_root / "heuristic",
                    )
                    heuristic_attempts = 1
                all_cases.append(
                    _case_row(
                        case_id,
                        app,
                        mutation_type,
                        "heuristic",
                        heuristic_outcome,
                        healed=bool(heuristic_outcome.get("passed")),
                        attempts=heuristic_attempts,
                        price_per_1k_tokens=price,
                    )
                )

                healtest, attempts, tokens, unsafe_fixes, run_id = _run_healtest(
                    test_dir,
                    server.url,
                    benchmark_id,
                    case_id,
                )
                all_cases.append(
                    _case_row(
                        case_id,
                        app,
                        mutation_type,
                        "healtest",
                        healtest,
                        healed=bool(healtest.get("passed")),
                        attempts=attempts,
                        tokens=tokens,
                        unsafe_fixes=unsafe_fixes,
                        run_id=run_id,
                        price_per_1k_tokens=price,
                    )
                )

    summary = report.summarize(all_cases)
    model_mode, providers_configured = _model_mode()
    summary["seed"] = seed
    summary["apps"] = apps
    summary["mutation_types"] = mutation_types
    summary["case_count"] = len(matrix)
    summary["cost_per_1k_tokens"] = price
    summary["model_mode"] = model_mode
    summary["providers_configured"] = providers_configured
    return summary


def execute_benchmark_job(benchmark_id: int, config: dict) -> dict:
    """RQ entry point; persist terminal state and preserve failure details."""
    backend_dir = _repository_root() / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    models = import_module("app.models")

    db = models.SessionLocal()
    try:
        benchmark = db.get(models.Benchmark, benchmark_id)
        if benchmark is None:
            raise BenchmarkError(f"benchmark {benchmark_id} was not found")
        benchmark.status = "running"
        benchmark.started_at = models._now()
        db.commit()
        try:
            result = run({**config, "benchmark_id": benchmark_id})
            benchmark = db.get(models.Benchmark, benchmark_id)
            if benchmark is None:
                raise BenchmarkError(f"benchmark {benchmark_id} disappeared during execution")
            benchmark.status = "completed"
            benchmark.report_json = json.dumps(result, sort_keys=True)
            benchmark.heal_rate = result["arms"]["healtest"]["heal_rate"]
            benchmark.avg_attempts = result["arms"]["healtest"]["avg_attempts"]
            benchmark.median_seconds = result["arms"]["healtest"]["median_seconds"]
            benchmark.unsafe_fixes = result["arms"]["healtest"]["unsafe_fixes"]
            benchmark.finished_at = models._now()
            for case in result["cases"]:
                db.add(
                    models.BenchmarkCase(
                        benchmark_id=benchmark_id,
                        app=case["app"],
                        mutation_type=case["mutation_type"],
                        arm=case["arm"],
                        healed=int(case["healed"]),
                        attempts=case["attempts"],
                        seconds=case["seconds"],
                        tokens=case["tokens"],
                        unsafe_fixes=case["unsafe_fixes"],
                        error=case["error"],
                        run_id=case.get("run_id"),
                    )
                )
            db.commit()
            return {"id": benchmark_id, "status": benchmark.status}
        except Exception as exc:
            db.rollback()
            benchmark = db.get(models.Benchmark, benchmark_id)
            if benchmark is not None:
                benchmark.status = "failed"
                benchmark.error = f"{type(exc).__name__}: {exc}"
                benchmark.finished_at = models._now()
                db.commit()
            raise
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the HealTest benchmark matrix")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--apps", default=",".join(APPS))
    parser.add_argument("--mutations", default=",".join(MUTATION_TYPES))
    parser.add_argument("--json", dest="json_path", default="benchmark/results.json")
    parser.add_argument("--svg", dest="svg_path", default="benchmark/results.svg")
    parser.add_argument("--cost-per-1k-tokens", type=float)
    args = parser.parse_args(argv)
    result = run(
        {
            "seed": args.seed,
            "apps": [value.strip() for value in args.apps.split(",") if value.strip()],
            "mutation_types": [
                value.strip() for value in args.mutations.split(",") if value.strip()
            ],
            "cost_per_1k_tokens": args.cost_per_1k_tokens,
        }
    )
    report.write_report(result, args.json_path, args.svg_path)
    print(f"Seed: {result['seed']} | cases: {result['case_count']}")
    print(
        f"Model mode: {result['model_mode']}"
        + (
            " (provider configuration incomplete)"
            if result["model_mode"] == "live" and not result["providers_configured"]
            else ""
        )
    )
    print("Arm           Heal rate   Avg attempts   Median seconds   Unsafe")
    for arm, label in (
        ("no_healing", "No healing"),
        ("heuristic", "Heuristic"),
        ("healtest", "HealTest"),
    ):
        metrics = result["arms"][arm]
        print(
            f"{label:<14}{metrics['heal_rate']:>7.1%}"
            f"{metrics['avg_attempts']:>15.2f}"
            f"{metrics['median_seconds']:>17.2f}"
            f"{metrics['unsafe_fixes']:>10}"
        )
    print(f"JSON: {args.json_path}\nSVG:  {args.svg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
