# HealTest

HealTest is an experimental tool for diagnosing and repairing failing Selenium UI tests. It runs pytest suites, collects browser evidence for failures, checks proposed test diffs against basic safety rules, and records runs and patches through a FastAPI service.

> **Prototype status:** this is not yet a production test-healing system. Diagnosis and patch generation support configured OpenAI-compatible Muse Spark and Muse Code endpoints, with deterministic mock mode for offline development. Candidate patches are checked and the affected test is rerun twice in a copy; inconsistent outcomes are marked flaky, and only two passes count as healed. Patches are never applied to the original project. The dashboard provides project setup, run review, evidence downloads, patch approval/rejection/export, and API settings. Visual element location requires a configured SAM-compatible endpoint. The complete 42-case Selenium benchmark has run in mock mode, but live model/SAM behavior and the benchmark's target effectiveness remain unverified.

## What is implemented

- **API and persistence:** FastAPI endpoints backed by SQLAlchemy; SQLite is the default.
- **Test execution:** a worker runs pytest with Selenium and headless Chromium. Each failed test can produce a screenshot, DOM snapshot, browser console log, interactive-element bounding boxes, and a manifest.
- **Diagnosis and patch proposal:** the state machine supports live Muse Spark and Muse Code-compatible endpoints or the offline mock, retries diagnosis/fix/verify up to three times, and escalates suspected application bugs without patching.
- **Patch checks and verification:** unified diffs are applied to a temporary copy, restricted to tests, checked for preserved assertions and unique new locators in the saved DOM, then verified by rerunning the affected test.
- **Job and request limits:** request bodies are capped, expensive job submissions are rate limited, configured queue failures are surfaced, RQ jobs have a timeout, and stale jobs are finalized when their status is read.
- **Visual fallback:** when diagnosis requests visual evidence, a configured SAM-compatible endpoint segments the screenshot; HealTest maps the mask to the highest-overlap visible DOM element and includes that candidate with the fix request.
- **Review dashboard:** workspace overview, project setup, run timeline/evidence, proposed-diff review, patch actions, browser-local API settings, and system-preference dark mode.
- **Sample suite:** three static apps (todo, shop, and multistep form) with 29 Selenium tests in total.
- **Compose development stack:** API, Redis, worker, frontend, and sample-app web server.

## Quick start with Docker Compose

Requirements: Docker Desktop (or Docker Engine) with the Compose plugin.

Copy the example environment file, then start the services:

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

Or use the PowerShell startup helper, which also waits for API health:

```powershell
.\scripts\start.ps1
```

On macOS/Linux, use `cp .env.example .env` followed by
`docker compose up --build -d`.

The example configuration runs in mock-model mode and needs no model credentials. Compose runs each test suite in a fresh Docker sandbox container. The sandbox network is internal and connects the demo `sample-apps` target only; the worker's model calls run outside the sandbox.

| Service | Address |
| --- | --- |
| FastAPI | <http://localhost:8000> |
| API health | <http://localhost:8000/health> |
| Interactive API docs | <http://localhost:8000/docs> |
| Frontend dashboard | <http://localhost:3000> |
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

For a repeatable three-minute passing-run walkthrough, use
`.\scripts\demo.ps1`; see [docs/demo-script.md](docs/demo-script.md) for the
timed narration and recording guidance. The passing sample run demonstrates
the dashboard and evidence workflow, not live model healing.

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
| `GET`, `POST` | `/benchmarks` | List benchmarks or start a seeded benchmark |
| `GET` | `/benchmarks/{benchmark_id}` | Get benchmark status, metrics, and case results |

When `HEALTEST_API_TOKEN` is non-empty, the API requires authorization for protected endpoints. The default `.env.example` leaves it empty for local development.

## Local development and tests

### Backend

Python 3.12 is used by the Docker images. Install backend dependencies and run the test suite:

```powershell
python -m pip install -r backend/requirements.txt
Set-Location backend
python -m pytest tests ..\benchmark\tests -q
Set-Location ..
```

The tests cover runner timeouts, evidence handling, patch safety, repeated
verification/flaky outcomes, API body and job limits, stale jobs, and the
benchmark contracts. Run the repository-local secret scan with
`python scripts/check_secrets.py`.

### Benchmark

Run the full 42-case mutation matrix from the repository root. The command
requires the backend/worker Python dependencies and a working Chromium +
ChromeDriver installation. Configure Muse Spark and Muse Code to measure live
model behavior; otherwise the configured mock mode is used.

```powershell
python -m benchmark.runner --seed 42
```

The command writes `benchmark/results.json` and `benchmark/results.svg`.
Narrow runs can select apps/mutations, and an optional token price enables
cost-per-heal reporting:

```powershell
python -m benchmark.runner --seed 42 --apps todo,shop --mutations renamed_id,button_text
python -m benchmark.runner --seed 42 --cost-per-1k-tokens 0.02
```

With the Compose worker running, start the same benchmark from the dashboard's
**Benchmarks** page. The job reports status and per-case outcomes through the
benchmark API; only passing verification reruns count as HealTest successes.

The fixed-seed 42-case matrix was also executed in the Compose worker with
Chromium in deterministic mock mode: no-healing 0%, heuristic 100% (42/42), and
HealTest mock 0% (0/42), with zero unsafe fixes. The dashboard/API reporting
path was verified with a seeded two-case run, including the strategy chart,
mutation breakdown, and visible HealTest failures. The mock fixer is
intentionally limited and cannot replace a live-provider effectiveness run;
these numbers do not establish the PRD heal-rate target.

### Frontend

The frontend is a Next.js 16 dashboard. Use Node.js 20.9 or newer:

```powershell
Set-Location frontend
npm ci
npm run build
npm run dev
```

The dashboard defaults to `http://localhost:8000` for the API. Configure a different URL and, when enabled, the shared API token from **Settings**; these values are stored in the browser, not in the repository. The Compose frontend is available at <http://localhost:3000>.

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
| `HEALTEST_CORS_ORIGINS` | Comma-separated browser origins allowed to call the API; defaults to localhost ports 3000 |
| `DATABASE_URL` | SQLAlchemy database URL; defaults to SQLite |
| `REDIS_URL` | Redis/RQ job queue URL; without it, jobs run inline (development only) |
| `HEALTEST_MOCK_MODE` | `1` selects deterministic offline responses; `0` requires live Muse Spark configuration; unset uses mock when no key is present |
| `MUSE_SPARK_API_KEY` | Credential for the configured Muse Spark-compatible API |
| `MUSE_SPARK_API_URL` | Provider-supplied OpenAI-compatible chat-completions endpoint |
| `MUSE_SPARK_MODEL` | Provider-supplied model identifier; intentionally has no guessed default |
| `HEALTEST_MODEL_TIMEOUT_S` | Request timeout for diagnosis calls; each transport failure is retried once |
| `HEALTEST_SANDBOX` | Test runner mode: `auto`, `local`, or `docker` |
| `HEALTEST_TIMEOUT_S` | Wall-clock timeout for a pytest run, in seconds |
| `HEALTEST_MAX_REQUEST_BYTES` | Maximum API request body size; defaults to 1 MiB |
| `HEALTEST_JOB_RATE_LIMIT` | Job submissions allowed per client per window; defaults to 10 |
| `HEALTEST_JOB_RATE_WINDOW_S` | Job submission rate-limit window; defaults to 60 seconds |
| `HEALTEST_JOB_TIMEOUT_S` | Maximum RQ job execution time; defaults to 1,800 seconds |
| `HEALTEST_STALE_JOB_GRACE_S` | Time after job timeout before stale records are finalized; defaults to 60 seconds |
| `HEALTEST_SANDBOX_IMAGE` | Image used for per-run Docker sandbox mode |
| `HEALTEST_SANDBOX_NETWORK` | Docker network used by sandbox containers |
| `HEALTEST_SANDBOX_CPUS`, `HEALTEST_SANDBOX_MEMORY` | Resource limits for Docker sandbox containers |
| `CHROMEDRIVER_PATH` | Optional explicit ChromeDriver executable path |
| `MUSE_CODE_API_KEY` | Credential for the configured Muse Code-compatible API |
| `MUSE_CODE_API_URL` | Provider-supplied OpenAI-compatible chat-completions endpoint for patch generation |
| `MUSE_CODE_MODEL` | Provider-supplied model identifier for patch generation |
| `SAM_API_URL` | Provider-supplied SAM 3.1-compatible segmentation endpoint; intentionally has no guessed default |
| `SAM_API_KEY` | Credential for the configured SAM-compatible endpoint |

`HEALTEST_SANDBOX=docker` starts a fresh constrained container for each run. It requires the Docker CLI and socket in the worker, a configured Docker network, and a target hostname listed in `HEALTEST_SANDBOX_ALLOWED_HOSTS`. Compose configures an internal network for `sample-apps`, so sandbox tests cannot make outbound Internet connections. The AI gateway is called by the worker, not by the test container. Because access to the Docker socket gives the worker control over the host Docker daemon, use this setup only in a trusted local environment; test containers do not receive the socket. `local` mode runs pytest as a subprocess inside the worker and is intended only for development; it is not an isolated per-run sandbox. For non-Compose setups, configure an internal network containing only the target app before using Docker sandbox mode.

The diagnosis and patch gateways support provider-configured OpenAI-compatible chat-completions APIs. To use them, set `HEALTEST_MOCK_MODE=0` and supply the endpoint, model identifier, and key provided for Muse Spark and Muse Code; values are blank in `.env.example` rather than guessed. A malformed diagnosis gets one format-repair request; transport failures are retried once. Generated patches are checked against the test-only path allowlist, assertion preservation, diff-size limit, and saved-DOM locator uniqueness before the affected test is rerun in a copied test tree. Approval creates another patched test-only copy and never changes the original project.

The visual fallback runs only when Muse Spark sets `needs_visual=true`. Configure `SAM_API_URL` and `SAM_API_KEY` in the backend/worker environment. The endpoint contract is a bearer-authenticated `POST` with JSON fields `image` (base64-encoded screenshot PNG) and `text_prompt` (the diagnosed target description); it returns either a PNG mask body or JSON with a base64 `mask_png` field. The mask must have the screenshot's dimensions, with nonzero pixels indicating the selected region. HealTest persists the mask as evidence and sends the highest-IoU visible DOM candidate to Muse Code. If configuration, segmentation, or DOM matching fails, the attempt escalates rather than silently reverting to DOM-only repair. This adapter does not guess a provider URL or credentials; verify the selected host's request/response format before configuring it.

## Repository layout

```text
backend/app/       FastAPI API, database models, runner, orchestrator, safety checks, AI gateway
backend/tests/     Backend unit tests
worker/            RQ worker image, pytest evidence plugin, runner CLI
frontend/          Next.js workspace dashboard
sample_apps/       Static demo apps and Selenium suites
benchmark/         Seeded mutations, runner, heuristic baseline, and reports
docs/              Architecture and benchmark notes
```

## Current limitations

- Model keys and endpoint/model values in the example environment are blank; mock mode is deterministic. Live diagnosis and patch generation require provider configuration.
- Only the affected test is rerun against a temporary patched copy. A "healed" outcome reflects that rerun, but the original project is never changed automatically.
- Live SAM 3.1 inference requires a provider endpoint and credentials and has not been validated in this workspace. The full Selenium benchmark runs in mock mode, but actual model acceptance and target heal rate require configured live providers; mock-mode results do not prove live healing.
- Safety checks are conservative guards, not a security boundary or a complete semantic assertion-preservation analysis. Review diffs before using them.
- The job rate limiter is in-memory and per API process; use an upstream/shared limiter for multi-worker production deployments. Stale jobs are finalized when a run or benchmark is read, not by a periodic sweeper.
- SQLite schema changes beyond explicitly supported migrations are not migrated automatically. For a disposable development database, stop the stack and remove `backend/data/healtest.db` before restarting after schema changes.

See [docs/architecture.md](docs/architecture.md), [docs/benchmark-method.md](docs/benchmark-method.md), [docs/demo-script.md](docs/demo-script.md), [docs/submission-checklist.md](docs/submission-checklist.md), and [sample_apps/README.md](sample_apps/README.md) for additional project notes.
