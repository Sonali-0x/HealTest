# HealTest: Product Requirements Document

Version 1.0 | Author: Sona | Event: Meta Global AI Developer Hackathon, Theme 2 (Push the Frontier), software idea

## 1. Overview

### 1.1 Summary

HealTest is an AI agent that automatically repairs broken web UI tests. When an end-to-end test fails because the application's interface changed (a renamed element, a moved button, a new popup), HealTest collects evidence, diagnoses the cause, writes a minimal fix, verifies it by rerunning the test in a sandbox, and either saves the repair as a reviewable patch or escalates to a human.

### 1.2 Problem

UI test suites are brittle. A small front-end change breaks locators, and teams lose hours repairing tests by hand instead of building features. Broken tests also reduce trust in CI, so people start ignoring red builds.

### 1.3 Vision

Tests that repair themselves, where every repair is verified and reviewable, so developers only look at failures that need human judgment.

### 1.4 Hackathon fit

Theme 2 asks for models that give a machine a new capability. HealTest gives a test runner the capability to diagnose and repair itself. It uses three Meta models, each for a distinct job: Muse Spark for reasoning, Muse Code for writing fixes, and SAM 3.1 for visual element location. Confirm the exact model names, access method and rate limits in the hackathon developer docs before building.

## 2. Goals, non-goals and success metrics

### 2.1 Goals

- Repair a majority of locator and flow breakages in a benchmark of broken tests without human help.
- Prove every repair by rerunning the test, never by trusting the model's claim.
- Make each repair easy to review: clear diagnosis, small diff, before and after evidence.
- Deliver a working demo and a measurable benchmark within ten days.

### 2.2 Non-goals (version 1)

- Generating tests from scratch.
- Testing mobile or native apps.
- Automatically merging fixes into a main branch.
- Multi-user accounts, billing or teams.
- Fixing real application bugs. HealTest repairs tests only and must flag suspected real bugs for a human.

### 2.3 Success metrics

| Metric | Initial target | How it is measured |
| --- | --- | --- |
| Heal rate | 70% or higher | Broken tests repaired and passing, divided by total broken tests in the benchmark |
| Attempts per heal | 2 or fewer on average | Average attempts among healed tests |
| Time to heal | Under 60 seconds median | From failure detected to passing rerun |
| Unsafe fixes | 0 | Fixes that weaken or delete an assertion, caught by the safety check |
| Baseline gap | Beats the heuristic baseline | Compare against a simple fallback-locator script with no AI |

Targets are starting points. Update them after the first benchmark run and report the real numbers.

## 3. Users and use cases

### 3.1 Personas

- QA engineer: owns the end-to-end suite and spends hours each sprint fixing locators.
- Frontend developer: ships UI changes and gets blamed for broken tests.
- Hackathon judge or viewer: needs to understand the value in under a minute from the demo.

### 3.2 User stories

1. As a QA engineer, I register my app URL and test files so HealTest can watch them.
2. As a QA engineer, I see which tests failed and why, in plain language.
3. As a developer, I review a proposed patch as a diff with the evidence behind it.
4. As a developer, I approve or reject a patch with one click, and I can export it.
5. As a viewer, I see a benchmark chart showing pass rate before and after healing.

## 4. Scope

### 4.1 Must have (MVP)

- Run Selenium (Python and pytest) tests in a sandbox and capture failures.
- Collect evidence: error log, DOM snapshot, screenshot.
- Diagnose the failure with Muse Spark and return a structured result.
- Generate a minimal patch with Muse Code.
- Rerun to verify, with a retry limit of three.
- Safety checks on every patch.
- Web dashboard: run list, run detail with diff viewer, approve or reject.
- Benchmark runner with injected UI changes on three sample apps.

### 4.2 Should have

- SAM 3.1 visual fallback to locate an element when the DOM alone is not enough.
- Live progress updates in the dashboard.
- Export a patch file.

### 4.3 Could have

- Open a pull request on GitHub automatically.
- Playwright support.
- Slack or email notification.

### 4.4 Won't have (this version)

- Auto-merge, user accounts, mobile testing, test generation.

## 5. Functional requirements

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-1 | A user can create a project with a target app URL and a folder of test files (upload or repository path). | Must |
| FR-2 | The system runs the tests in an isolated Docker sandbox with headless Chrome and records pass or fail per test. | Must |
| FR-3 | On failure, the system stores the error log and stack trace, a DOM snapshot, a full-page screenshot, the browser console log, and the failing step. | Must |
| FR-4 | The diagnosis step classifies the failure as one of: locator changed, element moved, element removed, new popup or overlay, flow changed, timing or wait issue, or suspected real bug. | Must |
| FR-5 | If the class is suspected real bug, the system stops and escalates without writing a patch. | Must |
| FR-6 | The fix step produces a minimal unified diff touching only the test files. | Must |
| FR-7 | Before rerunning, the system checks that any new locator matches exactly one element in the saved DOM snapshot. | Should |
| FR-8 | The system reruns the patched test and records the result. On failure it retries up to three times, passing the previous attempts to the model. | Must |
| FR-9 | Safety checks reject a patch that removes or weakens assertions, edits non-test files, or exceeds a diff size limit. | Must |
| FR-10 | If SAM 3.1 is enabled and the DOM is not enough, the system segments the screenshot to find the intended element and maps it to a DOM element. | Should |
| FR-11 | Successful repairs are saved as patches with a report: diagnosis, diff, attempts, time taken. | Must |
| FR-12 | After three failed attempts the run is marked needs human review, with all evidence attached. | Must |
| FR-13 | The dashboard lets a user approve, reject or export each patch. Approval applies the patch to a copy of the test files, never a main branch. | Must |
| FR-14 | The benchmark runner injects scripted UI changes into the sample apps, runs HealTest, and reports metrics. | Must |
| FR-15 | Every model call, prompt, response and token count is logged per attempt. | Should |

## 6. System architecture

### 6.1 Components

- Frontend: Next.js web dashboard.
- API service: FastAPI (Python) exposing REST endpoints and a live event stream.
- Orchestrator: a Python state machine that drives the agent loop for one failing test.
- Test worker: Docker container running pytest, Selenium and headless Chrome against the target app.
- AI gateway: one module that calls Meta's models, handles retries, timeouts, logging and cost tracking.
- Storage: PostgreSQL (SQLite is acceptable for the hackathon) plus a file store for screenshots, DOM snapshots and patches.
- Queue: Redis with a simple job queue (RQ or Celery), so test runs never block the API.

### 6.2 End-to-end flow

1. The user starts a run from the dashboard. The API creates a run record and enqueues a job.
2. A worker executes the tests in the sandbox and collects results.
3. For each failed test, the orchestrator starts the agent loop: collect evidence, diagnose, optionally locate visually, write a fix, run safety checks, rerun.
4. Each step writes progress to the database and publishes an event, which the dashboard shows live.
5. The loop ends in one of three states: healed, needs human review, or suspected real bug.
6. The user reviews the patch and approves, rejects or exports it.

### 6.3 Agent state machine

COLLECT, then DIAGNOSE, then LOCATE (only when needed), then FIX, then CHECK (safety), then VERIFY (rerun). From VERIFY the loop goes to DONE on pass, back to DIAGNOSE on fail while attempts remain, and to ESCALATE when attempts run out. A suspected real bug goes straight to ESCALATE from DIAGNOSE.

### 6.4 Technology stack

| Layer | Choice | Reason |
| --- | --- | --- |
| Frontend | Next.js, TypeScript, Tailwind CSS | Fast to build, familiar tooling |
| UI libraries | shadcn/ui, Recharts, a diff viewer component | Ready-made dashboard parts |
| Backend | Python, FastAPI | Same language as the tests and Selenium |
| Test engine | pytest and Selenium | Matches the target problem |
| Sandbox | Docker with headless Chrome | Isolation and reproducibility |
| Database | PostgreSQL or SQLite | Simple relational data |
| Queue | Redis with RQ | Lightweight background jobs |
| AI | Muse Spark, Muse Code, SAM 3.1 | Hackathon requirement and best fit per task |
| Deployment | docker-compose locally, optional cloud deploy for the demo | Keeps setup to one command |

## 7. Frontend requirements

### 7.1 Pages

1. Dashboard: list of projects and recent runs, with summary cards for heal rate, average time and runs needing review.
2. Project setup: form for app URL, test files, retry limit and model settings.
3. Run detail: the main screen. A vertical timeline of the agent loop, one card per step: failure evidence, diagnosis, visual location (with the segmentation mask drawn on the screenshot), proposed diff, rerun result. Approve, reject and export buttons sit at the top.
4. Benchmark: a before and after pass-rate chart, a table by failure type, and the time and cost per heal.
5. Settings: API keys (stored server-side only), retry limit, safety thresholds.

### 7.2 Key components

- Status badge: running, healed, needs review, suspected bug, rejected.
- Evidence viewer: screenshot with zoom, collapsible DOM excerpt, console log.
- Diff viewer: side-by-side, with the changed locator highlighted.
- Step timeline: live-updating, with elapsed time per step.
- Metric cards and charts.

### 7.3 UX requirements

- Live progress through server-sent events or WebSocket, with polling as a fallback.
- Every screen has loading, empty and error states.
- Show the diagnosis in plain language before technical detail.
- Destructive or approving actions need a confirmation step.
- Desktop first, usable on a tablet, with dark mode and keyboard-accessible controls.
- The run detail page must explain the whole story in one screen, since it is the demo.

## 8. Backend requirements

### 8.1 Modules

- api: routes, request validation, auth for the demo (a single shared token is enough).
- orchestrator: the state machine and retry policy.
- runner: starts the sandbox, executes pytest, parses results and collects artifacts.
- evidence: DOM snapshot trimming, screenshot capture, log parsing.
- ai\_gateway: prompt builders, model clients, structured-output parsing, timeouts, retries and logging.
- vision: SAM 3.1 call and mask-to-element mapping.
- patcher: builds and applies unified diffs to a temporary copy of the tests.
- safety: assertion integrity check, diff limits, locator uniqueness check.
- benchmark: mutation injection and metric calculation.

### 8.2 API endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | /projects | Create a project |
| GET | /projects | List projects |
| POST | /projects/{id}/runs | Start a run |
| GET | /runs | List runs with filters |
| GET | /runs/{id} | Run detail with all attempts |
| GET | /runs/{id}/events | Live event stream |
| POST | /patches/{id}/approve | Approve a patch |
| POST | /patches/{id}/reject | Reject a patch |
| GET | /patches/{id}/download | Download the patch file |
| POST | /benchmarks | Start a benchmark |
| GET | /benchmarks/{id} | Benchmark results |
| GET | /health | Health check |

### 8.3 Sandbox rules

- Each run uses a fresh container with an ephemeral filesystem.
- CPU, memory and wall-clock limits per test run, with a hard timeout.
- Network access only to the target app URL and the AI gateway. Everything else is blocked.
- No secrets inside the sandbox.

### 8.4 Evidence collection

On each failure capture the full error message and stack trace, the failing line and step, the page URL, a full-page screenshot, the DOM at failure time, the browser console log, and the list of elements with their bounding boxes (needed for the visual mapping). Trim the DOM before sending it to a model: keep the region around the failing locator, remove scripts and styles, and cap the size.

## 9. AI design

### 9.1 Model roles

| Model | Job | Input | Output |
| --- | --- | --- | --- |
| Muse Spark | Diagnose the cause | Error, trimmed DOM, screenshot context, previous attempts | Failure class, confidence, short explanation, what to look for |
| SAM 3.1 | Find the intended element visually | Screenshot and a description from the diagnosis | Mask and bounding box |
| Muse Code | Write the fix | Failing test code, diagnosis, candidate element details | Minimal unified diff |

### 9.2 Structured outputs

The diagnosis returns fixed fields: failure\_class, confidence from 0 to 1, explanation, target\_description and needs\_visual (true or false). The fix returns a diff and a one-line rationale. Parse and validate every response. If parsing fails, retry once with a repair prompt, then count it as a failed attempt.

### 9.3 Visual fallback

When needs\_visual is true, send the screenshot and the target description to SAM 3.1. Take the returned mask, compute its bounding box, and match it to the DOM element with the highest overlap (intersection over union) using the stored element rectangles. Pass that element's details to the fix step.

### 9.4 Prompt and cost rules

- Low temperature for diagnosis and code.
- Retry attempts include the previous diff and its failure, so the model does not repeat itself.
- Token budget per attempt, with DOM trimming to stay inside it.
- Cache evidence so repeated attempts do not recollect it.
- Fallbacks: on a model timeout or error, retry once, then escalate.

### 9.5 Guardrails

- Page content is untrusted. Text inside the DOM must be treated as data and never as instructions, to prevent prompt injection from a hostile page.
- The assertion integrity check compares assertions before and after the patch. Any removed or loosened assertion rejects the patch.
- The model may only edit files in the test directory.
- The model's claim of success never counts. Only a passing rerun counts.

## 10. Data model

| Table | Key fields |
| --- | --- |
| projects | id, name, app\_url, test\_path, retry\_limit, created\_at |
| runs | id, project\_id, status, started\_at, finished\_at, total\_tests, failed\_tests |
| test\_results | id, run\_id, test\_name, status, error\_text, final\_state |
| attempts | id, test\_result\_id, number, failure\_class, confidence, explanation, used\_visual, model\_tokens, duration\_ms, outcome |
| artifacts | id, attempt\_id, type (screenshot, dom, log, mask), path |
| patches | id, test\_result\_id, diff, rationale, status (proposed, approved, rejected), created\_at |
| benchmarks | id, started\_at, config, heal\_rate, avg\_attempts, median\_seconds, unsafe\_fixes |
| benchmark\_cases | id, benchmark\_id, app, mutation\_type, healed, attempts, seconds |

## 11. Evaluation plan

### 11.1 Sample apps

Use three small web apps with a working test suite each: a to-do app, a simple shop with cart and checkout, and a multi-step form. Open-source demo apps are fine. Each suite should have 8 to 10 tests.

### 11.2 Injected UI changes

For each app, create variants that break tests in realistic ways: renamed id or class, changed button text, element moved to another container, new wrapper element, cookie banner or modal added, an extra step inserted in a flow, a button replaced by a link. This gives roughly 30 to 60 broken-test cases.

### 11.3 Comparison

Report three numbers side by side: no healing (baseline, 0%), a simple heuristic that tries fallback locators with no AI, and HealTest. Break results down by change type and say honestly where HealTest fails.

## 12. Security, privacy and safety

- API keys live in environment variables on the server and are never sent to the browser or the sandbox.
- Run all test code in the sandbox only. Treat uploaded tests as untrusted.
- Never auto-merge. Every change needs explicit approval.
- Do not send customer data to models. Use the sample apps for the demo, and warn users to avoid real credentials in tests.
- Log all model calls for audit, and keep logs free of secrets.
- Rate-limit the API and validate file uploads (type, size).

## 13. Non-functional requirements

- Performance: median heal under 60 seconds for a single test, and the dashboard loads in under 2 seconds.
- Reliability: a failed model call or crashed container must not leave a run stuck. Use timeouts and mark the run as errored.
- Reproducibility: the benchmark runs with one command and a fixed seed, so results can be repeated.
- Observability: structured logs, a run timeline and per-attempt token and time counts.
- Cost: track tokens per heal and show the average in the benchmark page.
- Accessibility: keyboard navigation and sufficient color contrast.

## 14. DevOps and repository

- One repository with these folders: frontend, backend, worker, benchmark, sample\_apps, docs.
- docker-compose starts the API, worker, Redis, database and frontend with one command.
- A basic CI workflow runs unit tests for the safety checks and the patcher, since those are the riskiest logic.
- The README covers setup, architecture, how to run the benchmark and known limits.
- Optional: deploy the frontend and API for a live demo link, with a recorded fallback video in case the live demo fails.

## 15. Timeline (10 days)

| Days | Milestone | Done when |
| --- | --- | --- |
| 1 to 2 | Sample apps, test suites, Docker sandbox, evidence collection | A failing test produces screenshot, DOM and log |
| 3 | Diagnosis with Muse Spark | Structured diagnosis for every failure type |
| 4 to 5 | Fix generation, safety checks, rerun and retry loop | End-to-end heal works on one app |
| 6 | Dashboard with run list and run detail | A run can be watched live |
| 7 | SAM 3.1 visual fallback | Cases the DOM cannot solve are healed |
| 8 | Benchmark runner and results page | Metrics table and chart generated |
| 9 | Polish, bug fixes, README, architecture diagram | Fresh clone runs with one command |
| 10 | Demo video and submission | Video, repository and write-up submitted |

If time runs short, cut in this order: GitHub pull request export, live streaming (use polling), SAM 3.1. Never cut the safety checks or the benchmark.

## 16. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Model access or rate limits slow development | Cache evidence, keep prompts small, build the mock-model mode early so work continues offline |
| Model hallucinates a locator that does not exist | Check the locator against the saved DOM before rerunning |
| Fix makes the test pass by weakening it | Assertion integrity check on every patch |
| Tests are flaky, so a heal looks like a failure | Run each verification twice and report flaky tests separately |
| Scope grows beyond ten days | Strict MVP, with the cut order above |
| Live demo fails | Pre-recorded fallback video and a local run |
| Prompt injection through page content | Treat DOM text as data, never as instructions |

## 17. Demo plan and submission checklist

### 17.1 Three-minute demo script

1. Show a passing test suite (10 seconds).
2. Change the app's UI so tests break (20 seconds).
3. Run HealTest and watch the timeline live: evidence, diagnosis, fix, rerun (60 seconds).
4. Show the diff and the safety check, then approve it (30 seconds).
5. Show the benchmark chart: baseline versus HealTest, with honest failure cases (40 seconds).
6. Close with the vision and next steps (20 seconds).

### 17.2 Checklist

- Working demo and recorded video.
- Public repository with a clear README.
- Architecture diagram and this PRD summarized in the write-up.
- Benchmark results with the method explained.
- Honest limitations section.

## 18. Open questions

- What are the exact model names, access method, rate limits and context sizes in the hackathon docs?
- Does the submission require a hosted demo, or is a video enough?
- Are there rules about which tools or libraries can be used alongside Meta's models?
- Which three sample apps will give the most varied failure types in the time available?
