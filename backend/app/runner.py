"""Sandbox test runner: execute pytest, parse results, collect artifacts.

Two execution modes (``HEALTEST_SANDBOX``):

- ``local`` (explicit opt-in only): run pytest in a subprocess with a
  wall-clock timeout. This is for trusted development suites only; test code
  inherits the worker's environment and filesystem access.
- ``docker``: launch a fresh ``HEALTEST_SANDBOX_IMAGE`` container per run
  with CPU/memory/pid limits, read-only filesystem and dropped
  capabilities (PRD section 8.3). Requires a TLS-authenticated remote Docker
  daemon and a pre-created ``HEALTEST_SANDBOX_NETWORK``.

``auto`` (default) always requires Docker sandbox mode and never falls back
to local execution. Importing this module needs stdlib only; pytest and
Selenium are exercised in the child process / container.

Results come from a JSONL log written by the evidence plugin
(``HEALTEST_REPORT_LOG``), since pytest core no longer ships
``--report-log``: without the plugin there are no results.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

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


def _pytest_cmd(test_dir: str, use_plugin: bool, test_target: str | None = None) -> list[str]:
    cmd = [sys.executable, "-m", "pytest", test_target or test_dir, "-q"]
    if use_plugin:
        cmd[4:4] = ["-p", PLUGIN_MODULE]
    return cmd


def _run_local(
    test_dir: str,
    app_url: str,
    artifact_dir: str,
    report_log: str,
    timeout_s: int,
    summary: RunSummary,
    test_target: str | None = None,
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
        backend_dir = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (plugdir, backend_dir, env.get("PYTHONPATH", "")) if part
        )
    try:
        proc = subprocess.run(
            _pytest_cmd(
                test_dir,
                use_plugin=plugdir is not None,
                test_target=(
                    os.path.join(test_dir, test_target) if test_target else None
                ),
            ),
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
    return os.environ.get("HEALTEST_SANDBOX_NETWORK", "healtest-sandbox")


def _validate_sandbox_target(app_url: str) -> None:
    hostname = urlparse(app_url).hostname
    allowed_hosts = {
        host.strip().casefold()
        for host in os.environ.get("HEALTEST_SANDBOX_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    }
    if not hostname or hostname.casefold() not in allowed_hosts:
        raise RunnerUnavailable(
            "sandbox target host must be listed in HEALTEST_SANDBOX_ALLOWED_HOSTS"
        )


def _validate_docker_endpoint() -> None:
    endpoint = os.environ.get("DOCKER_HOST", "")
    if urlparse(endpoint).scheme != "tcp":
        raise RunnerUnavailable(
            "docker sandbox requires a remote tcp:// Docker endpoint; "
            "local socket and non-TLS endpoints are not allowed"
        )
    if os.environ.get("DOCKER_TLS_VERIFY") != "1":
        raise RunnerUnavailable("docker sandbox requires Docker TLS verification")
    cert_dir = os.environ.get("DOCKER_CERT_PATH", "")
    if not cert_dir or any(
        not os.path.isfile(os.path.join(cert_dir, name))
        for name in ("ca.pem", "cert.pem", "key.pem")
    ):
        raise RunnerUnavailable(
            "docker sandbox requires ca.pem, cert.pem, and key.pem in DOCKER_CERT_PATH"
        )


def _docker_run(
    args: list[str], *, timeout_s: int | None = None, input_data: bytes | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=input_data is None,
            input=input_data,
            timeout=timeout_s,
            check=False,
        )
        if input_data is not None:
            return subprocess.CompletedProcess(
                args,
                proc.returncode,
                proc.stdout.decode("utf-8", errors="replace"),
                proc.stderr.decode("utf-8", errors="replace"),
            )
        return proc
    except subprocess.TimeoutExpired:
        raise
    except OSError as exc:
        raise RunnerUnavailable(f"could not execute Docker CLI: {exc}") from exc


def _docker_run_bytes(
    args: list[str], *, timeout_s: int | None = None
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["docker", *args], capture_output=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired:
        raise
    except OSError as exc:
        raise RunnerUnavailable(f"could not execute Docker CLI: {exc}") from exc


def _archive_test_suite(test_dir: str) -> bytes:
    root = os.path.realpath(test_dir)
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w") as archive:
        for current_dir, directories, filenames in os.walk(root, followlinks=False):
            for name in sorted(directories):
                path = os.path.join(current_dir, name)
                if os.path.islink(path):
                    raise RunnerUnavailable("test suites containing symlinks cannot be sandboxed")
                relative = os.path.relpath(path, root).replace(os.sep, "/")
                archive.addfile(archive.gettarinfo(path, arcname=relative))
            for name in sorted(filenames):
                path = os.path.join(current_dir, name)
                if os.path.islink(path):
                    raise RunnerUnavailable("test suites containing symlinks cannot be sandboxed")
                if not stat.S_ISREG(os.stat(path, follow_symlinks=False).st_mode):
                    raise RunnerUnavailable("test suites may contain regular files only")
                relative = os.path.relpath(path, root).replace(os.sep, "/")
                with open(path, "rb") as source:
                    archive.addfile(archive.gettarinfo(path, arcname=relative), source)
    return archive_buffer.getvalue()


def _extract_artifact_archive(archive_data: bytes, artifact_dir: str) -> None:
    root = os.path.realpath(artifact_dir)
    os.makedirs(root, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:") as archive:
            for member in archive:
                relative = os.path.normpath(member.name)
                if relative in ("", "."):
                    continue
                if os.path.isabs(relative) or relative == ".." or relative.startswith(
                    ".." + os.sep
                ):
                    raise RunnerUnavailable("sandbox artifact archive contains an unsafe path")
                target = os.path.realpath(os.path.join(root, relative))
                if os.path.commonpath((root, target)) != root:
                    raise RunnerUnavailable("sandbox artifact archive contains an unsafe path")
                if member.isdir():
                    os.makedirs(target, exist_ok=True)
                    continue
                if not member.isfile():
                    raise RunnerUnavailable(
                        "sandbox artifact archive contains a non-regular file"
                    )
                os.makedirs(os.path.dirname(target), exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise RunnerUnavailable("sandbox artifact archive contains an unreadable file")
                with source, open(target, "wb") as destination:
                    shutil.copyfileobj(source, destination)
    except (tarfile.TarError, OSError) as exc:
        raise RunnerUnavailable(f"could not unpack sandbox artifacts: {exc}") from exc


def _docker_error(
    action: str,
    proc: subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes],
) -> RunnerUnavailable:
    details = proc.stderr or proc.stdout or "no error details"
    if isinstance(details, bytes):
        details = details.decode("utf-8", errors="replace")
    details = details.strip()[:2000]
    return RunnerUnavailable(f"Docker {action} failed (exit {proc.returncode}): {details}")


def _run_docker(
    test_dir: str,
    app_url: str,
    artifact_dir: str,
    timeout_s: int,
    summary: RunSummary,
    test_target: str | None = None,
) -> None:
    if shutil.which("docker") is None:
        raise RunnerUnavailable(
            "docker CLI not found; refusing to run tests without sandboxing"
        )
    _validate_sandbox_target(app_url)
    _validate_docker_endpoint()
    cpus = os.environ.get("HEALTEST_SANDBOX_CPUS", "2")
    memory = os.environ.get("HEALTEST_SANDBOX_MEMORY", "1g")
    inner_tests = "/job/tests"
    inner_out = "/job/out"
    inner_report = f"{inner_out}/report.jsonl"
    container_name = f"healtest-run-{uuid.uuid4().hex}"
    create_cmd = [
        "create",
        "--name", container_name,
        f"--cpus={cpus}", f"--memory={memory}", "--pids-limit=256",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
        "--tmpfs", "/job:rw,noexec,nosuid,size=512m",
        "--cap-drop=ALL", "--security-opt=no-new-privileges",
        f"--network={_sandbox_network()}",
        "-e", f"APP_URL={app_url}",
        "-e", f"HEALTEST_ARTIFACT_DIR={inner_out}",
        "-e", f"HEALTEST_REPORT_LOG={inner_report}",
        "-e", "PYTHONPATH=/srv",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "HOME=/tmp",
        _sandbox_image(),
        "sleep", "infinity",
    ]
    created = _docker_run(create_cmd)
    if created.returncode:
        raise _docker_error("container creation", created)
    try:
        started = _docker_run(["start", container_name])
        if started.returncode:
            raise _docker_error("container start", started)

        prepared = _docker_run(
            ["exec", container_name, "mkdir", "-p", inner_tests, inner_out]
        )
        if prepared.returncode:
            raise _docker_error("sandbox directory setup", prepared)

        archive_tests = _archive_test_suite(test_dir)
        copied_tests = _docker_run(
            [
                "exec", "-i", container_name, "python", "-c",
                "import os,sys,tarfile; "
                f"destination={inner_tests!r}; "
                "os.makedirs(destination, exist_ok=True); "
                "tarfile.open(fileobj=sys.stdin.buffer, mode='r|*').extractall("
                "destination, filter='data')",
            ],
            input_data=archive_tests,
        )
        if copied_tests.returncode:
            raise _docker_error("test-suite copy", copied_tests)

        test_command = [
            "exec", container_name, "python", "-m", "pytest",
            os.path.join(inner_tests, test_target).replace("\\", "/") if test_target else inner_tests,
            "-q",
            "-p", PLUGIN_MODULE, "-p", "no:cacheprovider",
        ]
        try:
            executed = _docker_run(test_command, timeout_s=timeout_s)
        except subprocess.TimeoutExpired:
            summary.timed_out = True
            killed = _docker_run(["kill", container_name])
            if killed.returncode:
                summary.errors.append(_docker_error("timeout cleanup", killed).args[0])
            summary.errors.append(f"sandbox exceeded wall-clock limit of {timeout_s}s")
            return
        if executed.returncode not in (0, 1):
            details = (executed.stderr or executed.stdout or "").strip()[:2000]
            summary.errors.append(
                f"sandbox exited with code {executed.returncode}: {details}"
            )

        archive_script = (
            "import os, stat, sys, tarfile\n"
            "root = os.environ['HEALTEST_ARTIFACT_DIR']\n"
            "with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as archive:\n"
            "    for current, directories, filenames in os.walk(root, followlinks=False):\n"
            "        for name in sorted(directories):\n"
            "            path = os.path.join(current, name)\n"
            "            if os.path.islink(path):\n"
            "                raise SystemExit('artifact directory contains a symlink')\n"
            "            archive.add(path, arcname=os.path.relpath(path, root), recursive=False)\n"
            "        for name in sorted(filenames):\n"
            "            path = os.path.join(current, name)\n"
            "            if os.path.islink(path) or not stat.S_ISREG(os.stat(path, "
            "follow_symlinks=False).st_mode):\n"
            "                raise SystemExit('artifact directory contains a non-regular file')\n"
            "            archive.add(path, arcname=os.path.relpath(path, root), recursive=False)\n"
        )
        artifact_archive = _docker_run_bytes(
            ["exec", container_name, "python", "-c", archive_script]
        )
        if artifact_archive.returncode:
            raise _docker_error("artifact collection", artifact_archive)
        _extract_artifact_archive(artifact_archive.stdout, artifact_dir)
    finally:
        removed = _docker_run(["rm", "-f", container_name])
        if removed.returncode:
            summary.errors.append(_docker_error("container cleanup", removed).args[0])


def resolve_mode() -> str:
    mode = os.environ.get("HEALTEST_SANDBOX", "auto").lower()
    if mode == "auto":
        return "docker"
    if mode not in ("local", "docker"):
        raise RunnerUnavailable(f"unknown HEALTEST_SANDBOX mode: {mode!r}")
    return mode


def run_tests(
    test_dir: str,
    app_url: str,
    artifact_dir: str | None = None,
    timeout_s: int | None = None,
    test_target: str | None = None,
) -> RunSummary:
    """Execute a pytest suite and return a parsed summary with evidence."""
    import time

    if not os.path.isdir(test_dir):
        raise RunnerUnavailable(f"test directory not found: {test_dir}")
    if test_target:
        target_path, separator, selector = test_target.partition("::")
        normalized_target = os.path.normpath(target_path)
        if (
            os.path.isabs(normalized_target)
            or normalized_target in ("", ".")
            or normalized_target == ".."
            or normalized_target.startswith(".." + os.sep)
            or not os.path.isfile(os.path.join(test_dir, normalized_target))
            or (separator and not selector)
        ):
            raise RunnerUnavailable("test target must be an existing file inside the test directory")
        test_target = normalized_target + (f"::{selector}" if separator else "")
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
            _run_docker(
                test_dir, app_url, artifact_dir, timeout_s, summary, test_target
            )
        else:
            _run_local(
                test_dir, app_url, artifact_dir, report_log, timeout_s, summary, test_target
            )
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
