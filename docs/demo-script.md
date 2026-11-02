# Three-minute demo script

## Before recording

1. Follow the [quick start](../README.md#quick-start-with-docker-compose) and
   wait for `scripts/start.ps1` to report API readiness.
2. Open `http://localhost:3000` and `http://localhost:8000/docs` in separate
   tabs. Keep the dashboard API settings on the default localhost URL.
3. Use mock mode for a deterministic offline demonstration. Do not display
   API keys or `.env` values in a recording.
4. For a live healing demonstration, configure the provider and browser-enabled
   worker before the recording and prepare a disposable failing test project.
   The bundled `todo` suite is a passing-run demonstration; it does not prove
   live model healing.

## Timed walkthrough

| Time | Show and say |
| --- | --- |
| 0:00–0:25 | Introduce HealTest as a review-first Selenium repair prototype. Point out that it never edits the original test tree automatically. |
| 0:25–0:45 | Show the dashboard and project setup. Explain the API, worker/queue, SQLite state, evidence store, and optional model providers. |
| 0:45–1:10 | In PowerShell, run `.\scripts\demo.ps1`. It creates a project against the sample todo app, submits a run, and polls its terminal status. |
| 1:10–1:40 | Open the submitted run in the dashboard. Show status and per-test results; for a prepared failing run, show screenshot, DOM, console, and error evidence. |
| 1:40–2:10 | Open a proposed patch. Explain test-only path restrictions, assertion/locator checks, and that two passing verification runs are required. If the run is flaky, show that it escalates instead of counting as healed. |
| 2:10–2:35 | Demonstrate patch approval or rejection. Approval creates a separate test copy; it does not edit the source project. |
| 2:35–3:00 | Open Benchmarks and explain the measured baseline/heuristic/HealTest arms, then close with the prototype limitations: live provider and full browser matrix are not certified by mock results. |

## Fallback recording

If live providers or browser drivers are unavailable, record the same flow in
mock mode using the passing sample run and dashboard/API walkthrough. Label
the recording as an offline product walkthrough; do not present mock results
as evidence of live healing effectiveness. Keep the recording outside the
repository unless submission rules request an artifact in source control.
