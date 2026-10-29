# HealTest architecture (stub — full diagram in Phase 7)

## Components (PRD §6.1)

- `frontend/` — Next.js dashboard.
- `backend/app/main.py` — FastAPI REST + SSE.
- `backend/app/orchestrator.py` — agent state machine (one failing test).
- `worker/` — Docker sandbox: pytest + Selenium + headless Chrome.
- `backend/app/ai_gateway/` — model calls, retries, logging, cost tracking.
- Storage — SQLite file at `backend/data/healtest.db` + file store `backend/data/`.
- Queue — Redis + RQ, inline fallback when `REDIS_URL` unset.

## Agent state machine (PRD §6.3)

`COLLECT → DIAGNOSE → LOCATE (if needs_visual) → FIX → CHECK → VERIFY → DONE`

- `VERIFY` fail → back to `DIAGNOSE` while attempts remain (max 3).
- Attempts exhausted → `ESCALATE` (needs human review).
- `DIAGNOSE == suspected real bug` → straight to `ESCALATE`, no patch.
