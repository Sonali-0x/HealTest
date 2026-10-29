"""Sandbox test runner: execute pytest, parse results, collect artifacts.

Two execution modes (``HEALTEST_SANDBOX``):

- ``local`` (default when no docker CLI): run pytest in-process via
  subprocess with a wall-clock timeout. The worker container itself is
  the isolation boundary.
- ``docker``: launch a fresh ``HEALTEST_SANDBOX_IMAGE`` container per run
  with CPU/memory/pid limits, read-only filesystem and dropped
  capabilities (PRD section 8.3). Needs the docker socket mounted and
  ``HEALTEST_SANDBOX_NETWORK`` set to the compose network.

``auto`` (default) picks ``docker`` when the docker CLI is available,
else ``local``. Importing this module needs stdlib only; pytest and
Selenium are exercised in the child process / container.

Results come from a JSONL log written by the evidence plugin
(``HEALTEST_REPORT_LOG``), since pytest core no longer ships
``--report-log``: without the plugin there are no results.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .evidence import parse_failing_location

PLUGIN_MODULE = "conftest_plugin"
ERROR_TEXT_CAP = 20_000


class RunnerUnavailable(Exception):
    """Raised when tests cannot be executed in this environment."""


@dataclass
class TestFailure:
    test_name: str
    error_text: str
    failing_file: str = ""
    failing_line: str = ""
    artifact_dir: str | None = None
    manifest: dict = field(default_factory=dict)


@dataclass
class RunSummary:
    total: int = 0
    passed: int = 0
    failed: list[TestFailure] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    timed_out: bool = False
    duration_s: float = 0.0
    mode: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["failed"] = [asdict(f) for f in self.failed]
        return d


def safe_name(nodeid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid).strip("_")[:180] or "test"


def plugin_dir() -> str | None:
    """Locate the directory containing the evidence plugin."""
    env = os.environ.get("HEALTEST_WORKER_DIR")
    if env and (Path(env) / f"{PLUGIN_MODULE}.py").exists():
        return env
    repo_worker = Path(__file__).resolve().parent.parent.parent / "worker"
    if (repo_worker / f"{PLUGIN_MODULE}.py").exists():
        return str(repo_worker)
    return None


def _longrepr_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except Exception:
        return str(value)


def parse_report_log(path: str) -> tuple[int, int, list[dict]]:
    """Parse the JSONL result log written by the evidence plugin.

    Returns (total, passed, failures) where each failure is
    ``{"nodeid": ..., "longrepr": ...}`` for the call phase.
    Legacy pytest ``--report-log`` entries are accepted too.
    """
    total = 0
    passed = 0
    failures: list[dict] = []
    if not os.path.exists(path):
        return total, passed, failures
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("$report_type") == "TestReport" or (
                "nodeid" in entry and "outcome" in entry
            ):
                pass
            else:
                continue
            if entry.get("when", "call") != "call":
                continue
            total += 1
            outcome = entry.get("outcome")
            if outcome == "passed":
                passed += 1
            elif outcome == "failed":
                failures.append(
                    {"nodeid": entry.get("nodeid", ""), "longrepr": _longrepr_text(entry.get("longrepr"))}
                )
    return total, passed, failures


def _attach_manifests(failures: list[TestFailure], artifact_dir: str) -> None:
    for f in failures:
        outdir = os.path.join(artifact_dir, safe_name(f.test_name))
        manifest_path = os.path.join(outdir, "manifest.json")
        if os.path.exists(manifest_path):
            f.artifact_dir = outdir
            try:
                with open(manifest_path, encoding="utf-8") as fh:
                    f.manifest = json.load(fh)
            except (OSError, json.JSONDecodeError):
                f.manifest = {}


def _build_failures(raw: list[dict], artifact_dir: str) -> list[TestFailure]:
    failures = []
    for item in raw:
        nodeid = item.get("nodeid", "")
        error_text = item.get("longrepr", "")[:ERROR_TEXT_CAP]
        loc_file, loc_line = parse_failing_location(item.get("longrepr", ""))
        failures.append(
            TestFailure(
                test_name=nodeid,
                error_text=error_text,
                failing_file=loc_file,
                failing_line=loc_line,
            )
        )
    _attach_manifests(failures, artifact_dir)
    return failures


def _pytest_cmd(test_dir: str, use_plugin: bool) -> list[str]:
    cmd = [sys.executable, "-m", "pytest", test_dir, "-q"]
    if use_plugin:
        cmd[4:4] = ["-p", PLUGIN_MODULE]
    return cmd


def _run_local(
    test_dir: str, app_url: str, artifact_dir: str, report_log: str, timeout_s: int, summary: RunSummary
) -> None:
    import importlib.util

    if importlib.util.find_spec("pytest") is None:
        raise RunnerUnavailable(
            "pytest is not installed in this environment; "
            "install worker/requirements.txt or run via the worker service"
        )
    plugdir = plugin_dir()
    if plugdir is None:
        summary.errors.append("evidence plugin not found; results and artifacts disabled")
    env = dict(os.environ)
    env["APP_URL"] = app_url
    env["HEALTEST_ARTIFACT_DIR"] = artifact_dir
    env["HEALTEST_REPORT_LOG"] = report_log
    if plugdir:
        env["PYTHONPATH"] = plugdir + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(
            _pytest_cmd(test_dir, use_plugin=plugdir is not None),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        summary.timed_out = True
        summary.errors.append(f"pytest exceeded wall-clock limit of {timeout_s}s")
        return
    if proc.returncode not in (0, 1):
        summary.errors.append(f"pytest exited with code {proc.returncode}: {(proc.stderr or '')[:2000]}")


def _sandbox_image() -> str:
    return os.environ.get("HEALTEST_SANDBOX_IMAGE", "healtest-worker")


def _sandbox_network() -> str:
    return os.environ.get("HEALTEST_SANDBOX_NETWORK", "healtest_default")


def _run_docker(
    test_dir: str, app_url: str, artifact_dir: str, report_log: str, timeout_s: int, summary: RunSummary
) -> None:
    if shutil.which("docker") is None:
        raise RunnerUnavailable("docker CLI not found for sandbox mode")
    cpus = os.environ.get("HEALTEST_SANDBOX_CPUS", "2")
    memory = os.environ.get("HEALTEST_SANDBOX_MEMORY", "1g")
    inner_tests = "/job/tests"
    inner_out = "/job/out"
    inner_report = f"{inner_out}/report.jsonl"
    # NOTE: bind-mount paths must be visible to the docker daemon (host
    # paths). Read-only root needs HOME + bytecode/cache redirects.
    cmd = [
        "docker", "run", "--rm",
        f"--cpus={cpus}", f"--memory={memory}", "--pids-limit=256",
        "--read-only", "--tmpfs", "/tmp",
        "--cap-drop=ALL", "--security-opt=no-new-privileges",
        f"--network={_sandbox_network()}",
        "-v", f"{os.path.abspath(test_dir)}:{inner_tests}:ro",
        "-v", f"{os.path.abspath(artifact_dir)}:{inner_out}:rw",
        "-e", f"APP_URL={app_url}",
        "-e", f"HEALTEST_ARTIFACT_DIR={inner_out}",
        "-e", f"HEALTEST_REPORT_LOG={inner_report}",
        "-e", "PYTHONPATH=/srv",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "HOME=/tmp",
        _sandbox_image(),
        "python", "-m", "pytest", inner_tests, "-q",
        "-p", PLUGIN_MODULE, "-p", "no:cacheprovider",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        summary.timed_out = True
        summary.errors.append(f"sandbox exceeded wall-clock limit of {timeout_s}s")
        return
    if proc.returncode not in (0, 1):
        summary.errors.append(f"sandbox exited with code {proc.returncode}: {(proc.stderr or '')[:2000]}")


def resolve_mode() -> str:
    mode = os.environ.get("HEALTEST_SANDBOX", "auto").lower()
    if mode == "auto":
        return "docker" if shutil.which("docker") else "local"
    if mode not in ("local", "docker"):
        raise RunnerUnavailable(f"unknown HEALTEST_SANDBOX mode: {mode!r}")
    return mode


def run_tests(
    test_dir: str,
    app_url: str,
    artifact_dir: str | None = None,
    timeout_s: int | None = None,
) -> RunSummary:
    """Execute a pytest suite and return a parsed summary with evidence."""
    import time

    if not os.path.isdir(test_dir):
        raise RunnerUnavailable(f"test directory not found: {test_dir}")
    started = time.monotonic()
    timeout_s = timeout_s or int(os.environ.get("HEALTEST_TIMEOUT_S", "300"))
    mode = resolve_mode()
    summary = RunSummary(mode=mode)
    owned_dir = artifact_dir is None
    artifact_dir = artifact_dir or tempfile.mkdtemp(prefix="healtest-run-")
    os.makedirs(artifact_dir, exist_ok=True)
    report_log = os.path.join(artifact_dir, "report.jsonl")
    try:
        if mode == "docker":
            _run_docker(test_dir, app_url, artifact_dir, report_log, timeout_s, summary)
        else:
            _run_local(test_dir, app_url, artifact_dir, report_log, timeout_s, summary)
    finally:
        summary.duration_s = time.monotonic() - started
    total, passed, raw = parse_report_log(report_log)
    summary.total = total
    summary.passed = passed
    summary.failed = _build_failures(raw, artifact_dir)
    if owned_dir:
        summary.errors.append(f"raw artifacts kept at {artifact_dir}")
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run a Selenium suite in the HealTest sandbox")
    parser.add_argument("--test-dir", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--out", default=None, help="artifact directory (default: temp dir)")
    parser.add_argument("--timeout", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        summary = run_tests(args.test_dir, args.app_url, args.out, args.timeout)
    except RunnerUnavailable as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(summary.to_dict(), indent=2))
    return 0
