# Benchmark method (stub — runner lands in Phase 6)

- 3 sample apps × 8–10 tests each; ~30–60 injected breakages (PRD §11).
- Mutation types: renamed id/class, button text, moved element, new wrapper,
  cookie banner/modal, extra flow step, button→link.
- Arms: no healing (0%) vs heuristic fallback locators vs HealTest.
- Metrics: heal rate, avg attempts, median seconds, unsafe fixes, cost/heal.
- One command, fixed seed, reproducible JSON report.
