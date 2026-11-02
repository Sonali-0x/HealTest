"""Scan tracked and non-ignored files for common accidentally committed secrets."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(
        r"""(?i)\b(?:api[_-]?key|client[_-]?secret|password|token)\b"""
        r"""\s*[:=]\s*["']?([A-Za-z0-9/+_=-]{24,})"""
    ),
)
SKIP_NAMES = {".env"}
MAX_FILE_BYTES = 2_000_000


def main() -> int:
    paths = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    findings: list[str] = []
    scan_errors: list[str] = []
    for raw_path in paths:
        if not raw_path:
            continue
        relative = Path(raw_path.decode("utf-8", errors="surrogateescape"))
        if relative.name in SKIP_NAMES or relative.name.startswith(".env."):
            continue
        path = ROOT / relative
        try:
            if not path.is_file():
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                scan_errors.append(f"{relative}: exceeds the {MAX_FILE_BYTES}-byte scan limit")
                continue
            data = path.read_bytes()
        except OSError as exc:
            scan_errors.append(f"{relative}: could not read file ({exc})")
            continue
        if b"\0" in data:
            continue
        content = data.decode("utf-8", errors="replace")
        for line_number, line in enumerate(content.splitlines(), start=1):
            if any(pattern.search(line) for pattern in PATTERNS):
                findings.append(f"{relative}:{line_number}")

    if scan_errors:
        print("Secret scan incomplete:")
        print("\n".join(scan_errors))
    if findings:
        print("Potential secrets detected:")
        print("\n".join(findings))
    if findings or scan_errors:
        return 1
    print("Secret scan passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
