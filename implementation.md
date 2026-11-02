## Goal

Build HealTest per `HealTest PRD.md` v1.0: an AI agent that repairs broken Selenium/pytest UI tests via collect → diagnose → locate → fix → check → verify loop, with a reviewable dashboard and a measurable benchmark, in 10 days.

## Success Criteria

- A failing test produces screenshot, trimmed DOM, console log, error trace (PRD §5 FR-3, §8.4).
- Structured diagnosis for all 7 failure classes; `suspected real bug` escalates with no patch (FR-4, FR-5).
- Minimal unified diff touching test files only; safety checks reject weakened assertions, non-test edits, oversized diffs (FR-6, FR-9).
- Verify by rerun, ≤3 attempts with history passed to model (FR-8); end states: healed / needs human review / suspected bug (PRD §6.3).
- Dashboard: run list, run detail timeline + diff viewer, approve/reject/export; approval patches a copy, never main (FR-11–13, §7).
- Benchmark on 3 sample apps (~30–60 injected breakages) reports heal rate, attempts/heal, median seconds, unsafe fixes, baseline vs heuristic vs HealTest (FR-14, §11).
- Initial targets: heal rate ≥70%, ≤2 attempts/heal, median <60s, 0 unsafe fixes (PRD §2.3).

## Approach

- Follow PRD stack exactly, no substitutions: Next.js+TS+Tailwind frontend; FastAPI backend; pytest+Selenium+headless Chrome in Docker; SQLite for hackathon (Postgres-compatible models); Redis+RQ; Muse Spark (diagnose) / Muse Code (fix) / SAM 3.1 (visual fallback).
- Backend owns the state machine; worker only runs tests and returns artifacts. One `ai_gateway` module for all model calls with logging, timeouts, token counts.
- Build `mock-model mode` in Phase 0 so all loop/safety/dashboard work continues offline if model access is rate-limited (PRD §16).
- Reuse decisions: unified-diff patcher + assertion-integrity check are the riskiest logic — unit-tested first, CI-gated (PRD §14).

## Folder Structure (target, from PRD §14 + §8.1)

```text
HealTest/
├── docker-compose.yml          # api, worker, redis, db, frontend, sample apps
├── README.md                   # setup, arch, benchmark, limits
├── docs/
│   ├── architecture.md         # diagram + agent state machine
│   └── benchmark-method.md
├── frontend/                   # Next.js + TS + Tailwind + shadcn/ui + Recharts
│   ├── app/
│   │   ├── page.tsx            # dashboard: projects, runs, metric cards
│   │   ├── projects/[id]/     # setup form
│   │   ├── runs/[id]/          # run detail: timeline, evidence, diff, approve
│   │   ├── benchmarks/[id]/    # chart + table by failure type
│   │   └── settings/
│   ├── components/
│   │   ├── StatusBadge.tsx EvidenceViewer.tsx DiffViewer.tsx
│   │   ├── StepTimeline.tsx MetricCards.tsx
│   └── lib/api.ts              # REST + SSE with polling fallback
├── backend/                    # FastAPI + orchestrator
│   ├── app/
│   │   ├── main.py             # routes: /projects /runs /patches /benchmarks /health
│   │   ├── models.py           # projects, runs, test_results, attempts, artifacts, patches, benchmarks, benchmark_cases
│   │   ├── schemas.py
│   │   ├── orchestrator.py     # COLLECT→DIAGNOSE→LOCATE→FIX→CHECK→VERIFY→DONE/ESCALATE
│   │   ├── ai_gateway/
│   │   │   ├── client.py       # Spark/Code clients, retry, timeout, token log
│   │   │   ├── prompts.py      # diagnose + fix + repair-parse prompts
│   │   │   ├── schemas.py      # failure_class, confidence, explanation, target_description, needs_visual
│   │   │   └── mock.py         # mock-model mode
│   │   ├── evidence.py         # DOM trim, log parse, bbox list
│   │   ├── vision.py           # SAM 3.1 + mask→bbox→IoU→DOM element
│   │   ├── patcher.py          # build/apply unified diffs to temp copy
│   │   ├── safety.py           # assertion check, path allowlist, size + locator-uniqueness checks
│   │   ├── queue.py            # RQ jobs + SSE events
│   │   └── store.py            # file store: screenshots/doms/patches/masks
│   ├── tests/
│   │   ├── test_safety.py test_patcher.py test_orchestrator.py
│   └── requirements.txt
├── worker/                     # sandbox test execution
│   ├── Dockerfile              # pytest + selenium + headless chrome
│   ├── runner.py               # run pytest, parse results, capture artifacts
│   └── conftest_plugin.py      # screenshot/DOM/console/bbox hook on failure
├── benchmark/
│   ├── runner.py               # inject mutation → run HealTest → metrics
│   ├── mutations/              # rename id, text change, move, wrapper, banner/modal, extra step, button→link
│   ├── heuristic_baseline.py   # fallback-locator, no AI
│   └── report.py
└── sample_apps/
    ├── todo/ shop/ multistep-form/   # each: app + 8–10 selenium tests
    └── README.md
```

## Steps

### Phase 0 — Scaffold + contracts (0.5 day)

- Create tree above; `docker-compose.yml` (api, worker, redis, sqlite volume, frontend, 3 sample apps); `.env.example` for keys; single shared-token demo auth.
- Implement `models.py` tables per PRD §10; file-store layout; RQ job + SSE event skeleton; `mock.py` returning fixed diagnosis/diff.
- Validation: `docker compose up --build`, `GET /health` 200, one mock run writes run → attempt → patch rows.

**Implementation update (2026-11-02):** in addition to the local mock-mode checks, built and started all Compose services and confirmed `/health` returned 200. A queued run was consumed from the `healtest` RQ queue and executed in the Docker sandbox: the intentionally broken submit locator was healed, and the database persisted the healed result, attempt, proposed non-empty patch diff, and eight evidence records. Acceptance also resolved Debian 13's separate `docker-cli` package and changed test/evidence transfer to Docker exec streams so the sandbox can keep its root filesystem read-only. Phase 0 Compose startup and queued-job acceptance now pass.

### Phase 1 — Sample apps + sandbox + evidence (PRD days 1–2; FR-1–3)

- Build/fix 3 sample apps with 8–10 passing Selenium tests each (§11.1).
- `worker/runner.py`: fresh container per run, CPU/mem/timeout limits, network allowlist (target URL + AI gateway only, §8.3), pytest JSON parsing.
- `conftest_plugin.py` + `evidence.py`: error+stack, failing line, URL, full-page screenshot, DOM, console log, element bboxes; DOM trim (failing-locator region, strip scripts/styles, size cap).
- API: `POST /projects`, `POST /projects/{id}/runs`, `GET /runs`, `GET /runs/{id}`.
- Done when: one intentionally broken test yields all 7 evidence items in `store/` + `artifacts` rows.

**Implementation update (2026-11-02):** all 29 sample Selenium tests passed on host Chrome and were then rerun through the queued Compose workflow in fresh Docker sandboxes: todo 10/10, shop 10/10, and multistep-form 9/9. A deliberately broken locator was also healed in a queued Docker run, persisting the result, proposed patch, attempt, and all eight evidence records (including the seven required evidence types). Phase 1 sandbox and evidence acceptance checks pass.

### Phase 2 — Diagnosis with Muse Spark (day 3; FR-4–5, §9.1–9.2)

- `prompts.py` + `schemas.py`: low temperature, page-text-as-data guard, strict fixed-field parse; one repair-prompt retry, then failed attempt.
- `orchestrator.py`: COLLECT → DIAGNOSE; `suspected real bug` → ESCALATE, no patch.
- Log every prompt/response/token count per attempt (FR-15).
- Done when: each of the 7 failure classes returns valid structured diagnosis on fixtures + one live failure.

**Implementation update (2026-10-29):** the gateway now supports a configurable OpenAI-compatible chat-completions endpoint for Muse Spark, validates strict diagnosis JSON for all seven failure classes, retries transport failures once, and makes one format-repair request for invalid output. The orchestrator escalates diagnosis failures and suspected-real-bug classifications without generating a patch; prompts, responses, token counts, and timings are logged. Mocked provider fixtures cover the contract and retry paths. No provider credentials or confirmed endpoint/model identifier are configured in this workspace, so the required live-failure validation remains pending.

**Implementation update (2026-10-30):** token-usage metadata is now validated explicitly, including prompt/completion fallback counts. All 62 checked-in backend tests pass, including the seven diagnosis-class fixtures, retry/repair cases, and escalation checks. Live-failure validation remains pending: no Muse Spark key, endpoint, or model is configured in the environment.

**Implementation update (2026-11-02):** hardened token accounting so malformed or partial provider `usage` metadata fails explicitly instead of silently reporting zero tokens; a response with no usage metadata remains supported and records zero. Diagnosis and orchestrator checks pass (50 tests), including all seven failure classes, one format-repair retry, transport retry, strict schema validation, and suspected-real-bug escalation without patching. The Compose worker has no `MUSE_SPARK_API_KEY`, `MUSE_SPARK_API_URL`, or `MUSE_SPARK_MODEL` configured, so a live Muse Spark failure diagnosis remains the only Phase 2 acceptance item outstanding.

### Phase 3 — Fix + safety + verify loop (days 4–5; FR-6–9, FR-11–12)

- `patcher.py`: Muse Code → minimal unified diff, test-dir only.
- `safety.py`: (1) assertion compare before/after, (2) path allowlist, (3) diff-size limit, (4) new-locator == exactly 1 match in saved DOM (FR-7).
- `orchestrator.py`: FIX → CHECK → VERIFY; VERIFY fail → DIAGNOSE with prior diffs+failures, max 3; then `needs human review` with all evidence. Cache evidence across attempts.
- API: `POST /patches/{id}/approve|reject`, `GET /patches/{id}/download`; approve applies to a test-copy only.
- Done when: end-to-end heal passes on one app; unsafe-fixture patch is rejected; 3-strike run escalates.

**Implementation update (2026-11-02):** live Muse Code-compatible patch generation is configurable without guessing provider URLs/models; mock fixes work for a deterministic locator fixture. Unified diffs apply only when every hunk matches, and the safety gate rejects unsafe paths, oversized diffs, changed/removed assertions, and non-unique or uninspectable new locators. Each failure gets up to three diagnose/fix/check/rerun attempts with prior diffs and failures included in subsequent prompts. Verification runs the selected pytest case twice against a temporary copy; both must pass. Approval writes a separate test-only copy and never edits the source. A queued Compose run against a deliberately broken Selenium locator healed successfully, passed repeat verification, persisted a proposed safe patch and all eight evidence artifacts, and left the original test unchanged. Focused Phase 3 safety, patcher, orchestrator, job-verification, and patch-approval tests pass (39 tests), including unsafe-patch rejection and three-strike escalation. Live Muse Spark/Code provider validation remains pending because credentials and provider configuration are unavailable.

### Phase 4 — Dashboard (day 6; FR-13, §7)

- Pages: dashboard, project setup, run detail (timeline + evidence + mask overlay slot + diff + rerun result + approve/reject/export), benchmark placeholder, settings.
- SSE `/runs/{id}/events` with polling fallback; loading/empty/error states; confirm on approve; plain-language diagnosis first; desktop-first + dark mode + keyboard access.
- Done when: a live run can be watched start→healed and approved from run detail in one screen.

**Implementation update (2026-11-02):** the dashboard, project setup, run review, benchmark placeholder, settings, typed API client, authenticated evidence/patch downloads, SSE snapshots with polling fallback, configurable CORS, and system-preference dark mode are implemented. Browser acceptance created a project, launched two real queued mock repairs, and watched them reach `healed`; the event stream stayed connected while a run was active. A proposed locator patch was approved from the run detail page into `approved/patch-3`; the approved copy contains the patched test while the original fixture remained unchanged. This also exposed and fixed two Compose/runtime integration issues: request-body middleware now forwards later `receive()` calls instead of synthesizing a disconnect (which had truncated streams), and the API mounts `sample_apps` read-only so it can copy configured test sources for approval. The SSE endpoint returned a complete terminal snapshot with a clean curl exit. All 130 checked-in backend tests, frontend ESLint, and the production build pass. Live external-model validation remains pending because provider credentials/configuration are unavailable.

### Phase 5 — SAM 3.1 visual fallback (day 7; FR-10, §9.3)

- `vision.py`: only when `needs_visual=true`; screenshot + target_description → mask → bbox → max-IoU DOM element → pass to fix step. Draw mask on screenshot in run detail.
- Done when: ≥1 DOM-insufficient case (moved/restyled element) heals that failed in Phase 3.

**Implementation update (2026-11-03):** the visual fallback implementation is in place: it runs only for `needs_visual=true`, validates the screenshot and segmentation mask, maps the mask bounding box to the best-overlap visible DOM element, passes that candidate to Muse Code, persists mask evidence, and displays the mask over the screenshot in run review. Missing/invalid segmentation or no DOM overlap escalates rather than generating a DOM-only patch. Targeted offline validation passed (**21 tests** across vision, orchestration, worker evidence capture, and job persistence). Live acceptance remains blocked: the Compose worker currently has no SAM or Muse provider credentials configured, so the real SAM 3.1 request and the moved/restyled Selenium heal case cannot yet be exercised. Configure provider-issued `SAM_API_URL`, `SAM_API_KEY`, and Muse Spark/Code settings in the local `.env`; the provider must support the request/response contract documented in the README.

### Phase 6 — Benchmark + results page (day 8; FR-14, §11)

- `mutations/` (7 types), `runner.py` (seeded, one command), `heuristic_baseline.py`, `report.py` (heal rate, avg attempts, median seconds, unsafe fixes, cost/heal, by-type table).
- API: `POST /benchmarks`, `GET /benchmarks/{id}`; frontend benchmark chart (baseline 0% vs heuristic vs HealTest).
- Done when: `one-command benchmark` produces chart + table; honest failures listed.

**Implementation update (2026-11-03):** added a seeded 42-case matrix (seven mutation types across two controls in each sample app), temporary app/test copies, and three measured arms: no healing, a DOM-uniqueness-checked locator heuristic, and the normal HealTest diagnosis/safety/verification workflow. Reports include heal rate, attempts, median time, rejected unsafe suggestions, tokens and optional cost per heal, mutation breakdowns, and individual failures; the CLI writes JSON and SVG. The CLI initializes its database schema, with a regression test. The complete fixed-seed matrix was run in the Compose worker using deterministic mock mode: no-healing 0%, heuristic 100% (42/42), HealTest mock 0% (0/42), and zero unsafe fixes; the generated report contains 126 arm rows and all 42 HealTest failures. A seeded two-case API benchmark was also completed and rendered in the dashboard, verifying the three-arm chart, mutation breakdown, and failure table. These checks validate the Selenium/mutation/report pipeline, API persistence, and UI but do not measure live-model effectiveness or meet the HealTest heal-rate target. Live-provider acceptance remains pending.

### Phase 7 — Hardening + demo (days 9–10)

- CI: unit tests for `safety.py` + `patcher.py`; timeouts so no run sticks in `running`; flaky check (double verify, report separately); rate-limit + upload validation; secrets audit.
- README + architecture diagram; fresh-clone one-command run; 3-min demo script + recorded fallback video; submission checklist (PRD §17).
- Cut order if short (PRD §15): PR-open export → live streaming (keep polling) → SAM 3.1. Never cut safety or benchmark.

**Implementation update (2026-11-03):** added bounded API request bodies, per-client job-submission limits, explicit Redis/RQ enqueue failures, configurable RQ timeouts, stale queued/running job finalization, and double verification that records inconsistent results as flaky rather than healed. Added migrations and API/UI reporting for flaky counts, startup/demo PowerShell scripts, a local secret scan, CI for backend/benchmark and frontend checks, architecture documentation, demo narration, and a submission checklist. Phase 7 acceptance in this workspace passed: the startup script rebuilt/restarted Compose and observed API health; the demo script submitted a run that completed in Docker sandbox mode with 10 passing tests; the local secret scan passed; the complete backend+benchmark suite passed (200 tests); frontend lint/build and Compose configuration checks passed. The sandbox mode was confirmed as `docker`, and the benchmark/mock and patch-safety acceptance are recorded above. A clean-clone startup, hosted GitHub Actions result, recorded walkthrough, live model/SAM behavior, live-provider benchmark effectiveness, and the PRD performance targets remain unverified.

## Validation Plan

- Phase 0–1: `docker compose up --build`; `curl GET /health`; break 1 locator → assert 7 artifacts exist; `pytest worker/` passes.
- Phase 2: fixture suite covering 7 classes → 100% valid parses; suspected-bug fixture → no diff, status escalated.
- Phase 3: `pytest backend/tests/test_safety.py backend/tests/test_patcher.py` (incl. assertion-removal rejection, non-test-path rejection, duplicate-locator rejection); 1 e2e heal; 1 forced 3-fail → `needs human review`.
- Phase 4: manual live-run watch; approve → copy patched, main untouched; reject/export works.
- Phase 5: before/after on visual-only case.
- Phase 6: benchmark unit matrix and API/report contract pass; fixed-seed 42-case Compose-worker run and dashboard rendering verified in mock mode. Live-provider effectiveness remains unverified.
- Highest-risk check: safety suite + rerun-proof (model claim never counts; only passing rerun does).

## Risks / Open Questions

- Model names/access/rate limits/context sizes — confirm in hackathon docs before Phase 2; mitigated by mock mode + small prompts + evidence cache.
- Hallucinated locators → DOM-uniqueness precheck; weakened tests → assertion check; flaky tests → double verify.
- Which 3 sample apps maximize failure variety; whether hosted demo or video suffices; allowed libraries alongside Meta models (PRD §18) — assumed: open-source demo apps OK, video acceptable, standard OSS libs allowed.
