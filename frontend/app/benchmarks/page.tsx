"use client";

import { useCallback, useEffect, useState } from "react";
import { api, Benchmark, BenchmarkArm, BenchmarkMetrics, BenchmarkReport } from "../../lib/api";

const ARM_LABELS: Record<BenchmarkArm, string> = {
  no_healing: "No healing",
  heuristic: "Heuristic",
  healtest: "HealTest",
};

const EMPTY_METRICS: BenchmarkMetrics = {
  total: 0,
  healed: 0,
  heal_rate: 0,
  avg_attempts: 0,
  median_seconds: 0,
  unsafe_fixes: 0,
  tokens: 0,
  tokens_per_heal: null,
  cost_per_heal: null,
};

function percentage(value: number | undefined): string {
  return `${Math.round((value ?? 0) * 100)}%`;
}

export default function BenchmarksPage() {
  const [benchmark, setBenchmark] = useState<Benchmark | null>(null);
  const [selectedId, setSelectedId] = useState<number | undefined>();
  const [seed, setSeed] = useState("42");
  const [starting, setStarting] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async (id?: number) => {
    try {
      const targetId = id ?? selectedId;
      if (!targetId) {
        const rows = await api<Array<Pick<Benchmark, "id" | "status" | "seed" | "started_at" | "heal_rate">>>(
          "/benchmarks",
        );
        if (!rows.length) {
          setBenchmark(null);
          setLoading(false);
          return;
        }
        const latest = rows[0];
        setSelectedId(latest.id);
        setBenchmark(await api<Benchmark>(`/benchmarks/${latest.id}`));
      } else {
        setBenchmark(await api<Benchmark>(`/benchmarks/${targetId}`));
      }
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load benchmark results.");
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!benchmark || !["queued", "running"].includes(benchmark.status)) return;
    const timer = window.setInterval(() => void refresh(benchmark.id), 2000);
    return () => window.clearInterval(timer);
  }, [benchmark, refresh]);

  async function startBenchmark() {
    const parsedSeed = Number(seed);
    if (!Number.isInteger(parsedSeed) || parsedSeed < 0 || parsedSeed > 2_147_483_647) {
      setError("Enter a whole-number seed between 0 and 2,147,483,647.");
      return;
    }
    setStarting(true);
    setError("");
    try {
      const created = await api<{ id: number }>("/benchmarks", {
        method: "POST",
        body: JSON.stringify({ seed: parsedSeed }),
      });
      setSelectedId(created.id);
      await refresh(created.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start the benchmark.");
    } finally {
      setStarting(false);
    }
  }

  const report: Partial<BenchmarkReport> = benchmark?.report ?? {};
  const arms = report.arms;
  const failures = (report.failures ?? []).filter((item) => item.arm === "healtest");

  return (
    <>
      <div className="page-heading benchmark-heading">
        <div>
          <p className="eyebrow">BENCHMARKS</p>
          <h1>Measure repair quality</h1>
          <p className="page-subtitle">
            Compare no healing, a deterministic locator heuristic, and HealTest on isolated,
            seeded UI mutations.
          </p>
        </div>
        <div className="benchmark-controls">
          <label className="benchmark-seed">
            <span>Seed</span>
            <input
              aria-label="Benchmark seed"
              type="number"
              min="0"
              max="2147483647"
              value={seed}
              onChange={(event) => setSeed(event.target.value)}
            />
          </label>
          <button
            className="button button-primary"
            disabled={starting || benchmark?.status === "running" || benchmark?.status === "queued"}
            onClick={() => void startBenchmark()}
          >
            {starting ? "Starting…" : "Run benchmark"}
          </button>
        </div>
      </div>

      {error && <div className="error-box" role="alert">{error}</div>}
      {loading ? (
        <div className="loading-box">Loading benchmark results…</div>
      ) : !benchmark ? (
        <section className="empty-state panel benchmark-empty">
          <strong>No benchmark runs yet</strong>
          <p>Run the seeded sample-app matrix to generate the comparison chart and failure report.</p>
        </section>
      ) : (
        <>
          <section className="panel benchmark-run-status" aria-live="polite">
            <div>
              <p className="eyebrow">BENCHMARK #{benchmark.id}</p>
              <strong>{benchmark.status === "completed" ? "Results ready" : benchmark.status}</strong>
              <span>
                Seed {benchmark.seed}
                {report.model_mode ? ` · ${report.model_mode} model mode` : ""}
                {report.model_mode === "live" && !report.providers_configured
                  ? " · provider configuration incomplete"
                  : ""}
              </span>
            </div>
            {benchmark.status === "running" || benchmark.status === "queued" ? (
              <p className="panel-caption">Running generated cases in isolated copies. This can take several minutes.</p>
            ) : null}
            {benchmark.status === "failed" && benchmark.error ? (
              <p className="inline-error" role="alert">{benchmark.error}</p>
            ) : null}
          </section>

          {benchmark.status === "completed" && arms ? (
            <>
              <section className="metric-grid benchmark-metrics" aria-label="HealTest metrics">
                <Metric label="Heal rate" value={percentage(arms.healtest?.heal_rate)} caption={`${arms.healtest?.healed ?? 0} / ${arms.healtest?.total ?? 0} cases`} />
                <Metric label="Avg attempts" value={(arms.healtest?.avg_attempts ?? 0).toFixed(2)} caption="Across HealTest cases" />
                <Metric label="Median seconds" value={(arms.healtest?.median_seconds ?? 0).toFixed(2)} caption="Per case" />
                <Metric label="Unsafe suggestions" value={String(arms.healtest?.unsafe_fixes ?? 0)} caption="Rejected by safety checks" />
              </section>

              <div className="dashboard-grid benchmark-results-grid">
                <section className="panel">
                  <div className="panel-header">
                    <div><h2 className="panel-title">Heal rate by strategy</h2><p className="panel-caption">Same generated cases for each arm</p></div>
                    <span className="summary-chip"><strong>{report.case_count ?? 0}</strong><span>mutations</span></span>
                  </div>
                  <div className="benchmark-chart" role="img" aria-label="Bar chart comparing benchmark healing rates">
                    {(["no_healing", "heuristic", "healtest"] as const).map((arm) => {
                      const metrics = arms[arm] ?? EMPTY_METRICS;
                      return (
                        <div className="benchmark-bar-row" key={arm}>
                          <span>{ARM_LABELS[arm]}</span>
                          <div className="benchmark-bar-track">
                            <div
                              className={`benchmark-bar benchmark-bar-${arm}`}
                              style={{ width: percentage(metrics.heal_rate) }}
                            />
                          </div>
                          <strong>{percentage(metrics.heal_rate)}</strong>
                          <small>{metrics.healed}/{metrics.total}</small>
                        </div>
                      );
                    })}
                  </div>
                  <div className="benchmark-token-note">
                    <span>Tokens / HealTest case: {arms.healtest?.tokens_per_heal?.toFixed(0) ?? "—"}</span>
                    <span>Cost / healed case: {arms.healtest?.cost_per_heal == null ? "Not configured" : `$${arms.healtest.cost_per_heal.toFixed(4)}`}</span>
                  </div>
                </section>

                <section className="panel">
                  <div className="panel-header"><div><h2 className="panel-title">By mutation type</h2><p className="panel-caption">Heal rate for each strategy</p></div></div>
                  <div className="table-scroll">
                    <table className="benchmark-table">
                      <thead><tr><th>Mutation</th><th>No healing</th><th>Heuristic</th><th>HealTest</th></tr></thead>
                      <tbody>
                        {Object.entries(report.by_mutation ?? {}).map(([mutation, metrics]) => (
                          <tr key={mutation}>
                            <td>{mutation.replaceAll("_", " ")}</td>
                            {(["no_healing", "heuristic", "healtest"] as const).map((arm) => (
                              <td key={arm}>{percentage(metrics[arm]?.heal_rate)} <small>({metrics[arm]?.healed ?? 0}/{metrics[arm]?.total ?? 0})</small></td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              </div>

              <section className="panel benchmark-failures">
                <div className="panel-header">
                  <div><h2 className="panel-title">Unhealed HealTest cases</h2><p className="panel-caption">Failures remain visible; only passing reruns count as healed.</p></div>
                  <span className="summary-chip"><strong>{failures.length}</strong><span>unhealed</span></span>
                </div>
                {failures.length ? (
                  <div className="table-scroll">
                    <table className="benchmark-table">
                      <thead><tr><th>Case</th><th>App</th><th>Mutation</th><th>Details</th></tr></thead>
                      <tbody>{failures.map((item, index) => (
                        <tr key={`${item.case_id}-${index}`}>
                          <td>{item.case_id}</td><td>{item.app}</td><td>{item.mutation_type.replaceAll("_", " ")}</td><td>{item.error || "No passing verification rerun."}</td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                ) : <p className="benchmark-all-healed">All HealTest cases passed verification.</p>}
              </section>
            </>
          ) : benchmark.status !== "failed" ? (
            <div className="loading-box">Waiting for benchmark report…</div>
          ) : null}
        </>
      )}
    </>
  );
}

function Metric({ label, value, caption }: { label: string; value: string; caption: string }) {
  return (
    <article className="metric-card">
      <div className="metric-top">{label}<span className="metric-icon" aria-hidden="true">⌁</span></div>
      <p className="metric-value">{value}</p>
      <p className="metric-caption">{caption}</p>
    </article>
  );
}
