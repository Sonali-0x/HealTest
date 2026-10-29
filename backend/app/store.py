"""File store layout for screenshots, DOM snapshots, logs, masks, patches.

Paths persisted in the DB are relative to the store root so rows stay
valid across containers that mount the same volume at different paths.
Use :func:`resolve` to map a stored path back to an absolute one.
"""

from __future__ import annotations

import os
import shutil

SUBDIRS = ("screenshots", "doms", "logs", "masks", "patches", "consoles", "elements")

_ARTIFACT_SUBDIR = {
    "screenshot": ("screenshots", "png"),
    "dom": ("doms", "html"),
    "log": ("logs", "log"),
    "mask": ("masks", "png"),
    "console": ("consoles", "log"),
    "elements": ("elements", "json"),
}


def store_root() -> str:
    root = os.environ.get("HEALTEST_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    )
    os.makedirs(root, exist_ok=True)
    for sub in SUBDIRS:
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    return root


def artifact_path(kind: str, attempt_id: int, ext: str = "") -> str:
    sub, default_ext = _ARTIFACT_SUBDIR[kind]
    return os.path.join(store_root(), sub, f"attempt-{attempt_id}.{ext or default_ext}")


def patch_path(patch_id: int) -> str:
    return os.path.join(store_root(), "patches", f"patch-{patch_id}.diff")


def relpath(path: str) -> str:
    return os.path.relpath(os.path.abspath(path), os.path.abspath(store_root()))


def resolve(stored: str) -> str:
    return os.path.join(store_root(), stored)


def save_text(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def save_bytes(path: str, content: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    return path


def ingest_file(src: str, kind: str, attempt_id: int) -> str:
    """Copy a worker artifact into the store; returns the stored rel path."""
    if not os.path.exists(src):
        raise FileNotFoundError(src)
    dst = artifact_path(kind, attempt_id)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return relpath(dst)
