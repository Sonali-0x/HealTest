# HealTest

HealTest is an experimental tool for diagnosing and repairing failing Selenium UI tests. It runs pytest suites, collects browser evidence for failures, checks proposed test diffs against basic safety rules, and records runs and patches through a FastAPI service.

> **Prototype status:** the end-to-end flow is not yet a production test-healing system. The model gateway currently uses fixed mock responses, the generated patch is not applied or verified by rerunning the test, visual element location and benchmark execution are stubs, and the frontend is a placeholder. Treat run outcomes and proposed patches accordingly.

## What is implemented

- **API and persistence:** FastAPI endpoints backed by SQLAlchemy; SQLite is the default.
- **Test execution:** a worker runs pytest with Selenium and headless Chromium. Each failed test can produce a screenshot, DOM snapshot, browser console log, interactive-element bounding boxes, and a manifest.
- **Diagnosis and patch proposal:** a state-machine skeleton records a diagnosis and proposed unified diff. The current mock returns fixed output.
- **Basic patch checks:** checks restrict diff paths to tests, cap diff size, and reject reductions in a simple assertion count.
- **Sample suite:** three static apps (todo, shop, and multistep form) with 29 Selenium tests in total.
- **Compose development stack:** API, Redis, worker, frontend, and sample-app web server.

## Quick start with Docker Compose

Requirements: Docker Desktop (or Docker Engine) with the Compose plugin.

Copy the example environment file, then start the services:

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

On macOS/Linux, use `cp .env.example .env` instead of `Copy-Item`.

The example configuration runs in mock-model mode and needs no model credentials. The Compose worker uses the `local` sandbox mode inside its worker container.

| Service | Address |
| --- | --- |
| FastAPI | <http://localhost:8000> |
| API health | <http://localhost:8000/health> |
| Interactive API docs | <http://localhost:8000/docs> |
| Frontend placeholder | <http://localhost:3000> |
| Sample apps | <http://localhost:8901/todo/>, <http://localhost:8901/shop/>, <http://localhost:8901/multistep-form/> |

Check that the API is ready:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

To stop the stack, run `docker compose down`. The SQLite database and run artifacts are stored under `backend/data/` on the host.

## Run a sample project

Create a project and start a run. `test_path` is a path **inside the worker container**, and `app_url` is the address the worker uses to reach the sample-app service:

```powershell
$project = Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/projects `
  -ContentType 'application/json' `
  -Body '{"name":"todo","app_url":"http://sample-apps:8901","test_path":"/sample_apps/todo/tests"}'

$run = Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/projects/$($project.id)/runs"

Invoke-RestMethod "http://localhost:8000/runs/$($run.id)"
```

Run details include per-test results, attempts, proposed patches, and stored paths to captured artifacts. With the default mock, a proposed patch is **not** applied to the suite.

The API also exposes:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Health check |
| `GET`, `POST` | `/projects` | List or create projects |
| `POST` | `/projects/{project_id}/runs` | Start a test run |
| `GET` | `/runs` | List runs |
| `GET` | `/runs/{run_id}` | Get run results, attempts, patches, and artifacts |
| `GET` | `/runs/{run_id}/events` | Server-sent event snapshot |
| `POST` | `/patches/{patch_id}/approve` | Mark a patch approved |
| `POST` | `/patches/{patch_id}/reject` | Mark a patch rejected |
| `GET` | `/patches/{patch_id}/download` | Download a patch diff |
| `POST`, `GET` | `/benchmarks`, `/benchmarks/{benchmark_id}` | Benchmark endpoints (execution is currently a stub) |

When `HEALTEST_API_TOKEN` is non-empty, the API requires authorization for protected endpoints. The default `.env.example` leaves it empty for local development.

## Local development and tests

### Backend

Python 3.12 is used by the Docker images. Install backend dependencies and run the test suite:

```powershell
python -m pip install -r backend/requirements.txt
Set-Location backend
python -m pytest -q
Set-Location ..
```

The backend tests cover the runner, artifact/evidence handling, patch checks, and orchestrator.

### Frontend

The frontend is a Next.js scaffold. With Node.js installed:

```powershell
Set-Location frontend
npm install
npm run dev
```

The Compose frontend is available at <http://localhost:3000>.

### Sample apps outside Compose

The sample pages are static files and can be served with Python:

```powershell
python -m http.server 8901 --directory sample_apps
```

Running the Selenium suites directly also requires pytest, Selenium, a compatible Chrome/Chromium browser, and its driver. In a second terminal, run:

```powershell
$env:APP_URL = 'http://localhost:8901'
Set-Location sample_apps/todo/tests
python -m pytest -q
```

The `worker` image includes Chromium, ChromeDriver, pytest, and Selenium.

## Configuration

Copy `.env.example` to `.env`; do not commit `.env` or put credentials in source control.

| Variable | Purpose |
| --- | --- |
| `HEALTEST_API_TOKEN` | Optional shared API token; blank disables API auth for local development |
| `DATABASE_URL` | SQLAlchemy database URL; defaults to SQLite |
| `REDIS_URL` | Redis/RQ job queue URL; without it, jobs run inline (development only) |
| `HEALTEST_MOCK_MODE` | Example configuration selects offline mode; the gateway currently uses mock responses regardless |
| `HEALTEST_SANDBOX` | Test runner mode: `auto`, `local`, or `docker` |
| `HEALTEST_TIMEOUT_S` | Wall-clock timeout for a pytest run, in seconds |
| `HEALTEST_SANDBOX_IMAGE` | Image used for per-run Docker sandbox mode |
| `HEALTEST_SANDBOX_NETWORK` | Docker network used by sandbox containers |
| `HEALTEST_SANDBOX_CPUS`, `HEALTEST_SANDBOX_MEMORY` | Resource limits for Docker sandbox containers |
| `CHROMEDRIVER_PATH` | Optional explicit ChromeDriver executable path |
| `MUSE_SPARK_API_KEY`, `MUSE_CODE_API_KEY`, `SAM_API_KEY` | Reserved/example model credentials; live model and visual integrations are not currently wired into the gateway |

`HEALTEST_SANDBOX=local` executes tests as a subprocess inside the worker container and enforces a timeout; it is **not** a separate per-run container. `docker` mode starts a fresh constrained container per run and requires a usable Docker CLI/socket and the configured network. Neither mode currently provides full network-egress allowlisting.

## Repository layout

```text
backend/app/       FastAPI API, database models, runner, orchestrator, safety checks, AI gateway
backend/tests/     Backend unit tests
worker/            RQ worker image, pytest evidence plugin, runner CLI
frontend/          Next.js placeholder
sample_apps/       Static demo apps and Selenium suites
benchmark/         Benchmark and baseline scaffolds (not implemented)
docs/              Architecture and benchmark notes
```

## Current limitations

- Model keys in the example environment are placeholders; the gateway currently returns deterministic mock diagnoses and diffs.
- The orchestrator does not apply the proposed diff or rerun the failing test to verify it. A reported "healed" outcome is therefore not proof that the test was repaired.
- Visual element location, benchmark execution, and the dashboard are not implemented.
- Safety checks are lightweight guards, not a security boundary or a full semantic assertion-preservation analysis. Review diffs before using them.
- SQLite schema changes are not migrated automatically. For a disposable development database, stop the stack and remove `backend/data/healtest.db` before restarting after schema changes.

See [docs/architecture.md](docs/architecture.md), [docs/benchmark-method.md](docs/benchmark-method.md), and [sample_apps/README.md](sample_apps/README.md) for additional project notes.
