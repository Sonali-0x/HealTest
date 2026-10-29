# HealTest

AI agent that repairs broken Selenium UI tests. See `HealTest PRD.md`.

## Quickstart (compose)

```sh
cp .env.example .env
docker compose up --build
curl localhost:8000/health
```

- API: http://localhost:8000 (`/health`, `/projects`, `/runs`, `/patches`, `/benchmarks`)
- Frontend stub: http://localhost:3000
- Sample apps: http://localhost:8901/todo/ (also `/shop/`, `/multistep-form/`)
- Mock-model mode is default (`HEALTEST_MOCK_MODE=1`); live models arrive Phase 2+.

## Phase 1 — run a real suite

Register a sample app and start a run (paths are inside the worker container):

```sh
curl -X POST localhost:8000/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"todo","app_url":"http://sample-apps:8901","test_path":"/sample_apps/todo/tests"}'
curl -X POST localhost:8000/projects/1/runs
curl localhost:8000/runs/1
```

Each failure stores error + stack trace, failing line, page URL, screenshot,
DOM, console log and element bounding boxes under `backend/data/`, linked
from the run detail as attempt artifacts.

Break one test on purpose (e.g. rename `#add-btn` in `sample_apps/todo/index.html`)
and rerun to see the evidence bundle for a failing test.

Local suite run without compose (needs Python, pytest, Selenium, Chrome):

```sh
cd sample_apps/todo/tests
APP_URL=http://localhost:8901 pytest -q
```

Backend unit tests (needs Python + pytest; evidence/runner tests need no DB):

```sh
cd backend
pytest tests/test_evidence.py tests/test_runner.py tests/test_safety.py tests/test_patcher.py -q
```

## Upgrading

The dev SQLite DB has no migrations: after pulling schema changes, delete
`backend/data/*.db` (and `backend/data/raw/`) and restart; tables are recreated.

## Sandbox notes (PRD §8.3)

- Compose default is `HEALTEST_SANDBOX=local`: pytest runs inside the worker
  container with a wall-clock timeout (`HEALTEST_TIMEOUT_S`).
- `HEALTEST_SANDBOX=docker` launches a fresh `HEALTEST_SANDBOX_IMAGE`
  container per run (`--cpus/--memory`, read-only FS, dropped caps). It needs
  the docker socket mounted and `HEALTEST_SANDBOX_NETWORK` on the compose net.
- Full egress allowlisting (target URL + AI gateway only) is future hardening;
  current sandbox shares the compose network.

## Layout

`frontend/` `backend/` `worker/` `benchmark/` `sample_apps/` `docs/` (PRD §14).
