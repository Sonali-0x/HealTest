# Submission checklist

## Product and safety

- [x] `.\scripts\start.ps1` builds/starts the Compose stack and `/health` returns
      `ok` in the current initialized workspace. A fresh-clone run is still
      pending.
- [x] Project creation, run submission/status, run review, and evidence
      downloads have been exercised through the dashboard/API.
- [x] Proposed diffs are reviewed; source tests remain unchanged unless copied
      explicitly for approval.
- [x] Safety and patcher tests pass in the complete backend/benchmark suite.
- [x] Repeat verification passes twice before a heal is counted; inconsistent
      outcomes are visible as flaky and escalated.
- [x] Oversized request rejection, job rate limits, job timeouts, queue failure,
      and stale-job finalization are covered by tests.
- [x] `python scripts/check_secrets.py` reports no potential secrets.

## Validation and demo

- [x] Local CI-equivalent checks pass: backend/benchmark tests, frontend
      lint/build, secret scan, and Compose configuration. Hosted GitHub Actions
      status was not checked.
- [x] `.\scripts\demo.ps1` completed a Docker-sandbox run with 10 passing tests;
      the run summary was visible in the dashboard.
- [ ] Record the three-minute walkthrough from [demo-script.md](./demo-script.md).
- [ ] If using live model results, record provider/model configuration
      privately and verify actual browser-enabled healing separately from mock
      mode.
- [x] Report remaining gaps honestly: fresh-clone validation, live model/SAM
      validation, benchmark effectiveness with live providers, and a recorded
      walkthrough. The 42-case worker-browser matrix has been run in mock mode.
- [ ] Do not include `.env`, provider credentials, local database files, or
      generated run artifacts in the submission.
