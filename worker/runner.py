"""Sandbox CLI: run a Selenium suite through the HealTest runner.

Thin shim over :mod:`app.runner` for debugging the worker/sandbox image:

    python runner.py --test-dir /sample_apps/todo/tests --app-url http://sample-apps:8901
"""

from __future__ import annotations

import os
import sys


def _ensure_backend_on_path() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (
        os.environ.get("HEALTEST_BACKEND_DIR"),
        "/srv",  # worker image layout (code at /srv/app)
        os.path.join(os.path.dirname(here), "backend"),  # repo checkout
    ):
        if candidate and os.path.isdir(os.path.join(candidate, "app")):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            return


if __name__ == "__main__":
    _ensure_backend_on_path()
    from app.runner import main

    sys.exit(main())
