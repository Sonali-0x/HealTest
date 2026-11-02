"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import StatusBadge from "../components/StatusBadge";
import { api, Project, Run } from "../lib/api";

function projectName(projects: Project[], projectId: number): string {
  return projects.find((project) => project.id === projectId)?.name ?? `Project ${projectId}`;
}

function projectUrl(projects: Project[], projectId: number): string {
  return projects.find((project) => project.id === projectId)?.app_url ?? "—";
}

function completion(run: Run): { count: number; total: number } {
  if (run.status === "running") {
    return { count: Math.max(0, run.total_tests - run.failed_tests), total: run.total_tests };
  }
  return { count: run.total_tests, total: run.total_tests };
}

export default function Dashboard() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [startingProject, setStartingProject] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextProjects, nextRuns] = await Promise.all([
        api<Project[]>("/projects"),
        api<Run[]>("/runs"),
      ]);
      setProjects(nextProjects);
      setRuns([...nextRuns].sort((a, b) => b.id - a.id));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load workspace data.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function startRun(projectId: number) {
    setStartingProject(projectId);
    setError("");
    try {
      const run = await api<Run>(`/projects/${projectId}/runs`, { method: "POST" });
      await refresh();
      router.push(`/runs/${run.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start the run.");
    } finally {
      setStartingProject(null);
    }
  }

  const successful = runs.filter((run) => ["healed", "passed"].includes(run.status)).length;
  const review = runs.filter((run) => ["needs review", "error"].includes(run.status)).length;
  const active = runs.filter((run) => run.status === "running").length;

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">OVERVIEW</p>
          <h1>Your testing workspace</h1>
          <p className="page-subtitle">A clear view of your UI test health and recent repair activity.</p>
        </div>
        <Link className="button button-primary" href="/projects/new"><span aria-hidden="true">＋</span> Add a project</Link>
      </div>

      {error && (
        <div className="error-box" role="alert" style={{ marginBottom: 16 }}>
          <p>{error}</p>
          <button className="button button-secondary button-small" onClick={() => void refresh()}>Try again</button>
        </div>
      )}

      <section className="metric-grid" aria-label="Workspace metrics">
        <Metric label="Projects" value={loading ? "—" : projects.length} icon="◫" caption="Connected test suites" />
        <Metric label="Runs started" value={loading ? "—" : runs.length} icon="↗" caption={active ? `${active} currently running` : "Across your workspace"} />
        <Metric label="Healthy runs" value={loading ? "—" : successful} icon="✓" caption="Verified or passing" />
        <Metric label="Need attention" value={loading ? "—" : review} icon="!" caption="Review escalated runs" />
      </section>

      <div className="dashboard-grid">
        <section className="panel" aria-labelledby="recent-runs-title">
          <div className="panel-header">
            <div>
              <h2 className="panel-title" id="recent-runs-title">Recent runs</h2>
              <p className="panel-caption">Latest activity across your test projects</p>
            </div>
            <button className="button button-secondary button-small" onClick={() => void refresh()} disabled={loading}>
              {loading ? "Refreshing…" : "↻ Refresh"}
            </button>
          </div>
          {loading && runs.length === 0 ? (
            <div className="loading-box" style={{ margin: 16 }}>Loading your runs…</div>
          ) : runs.length === 0 ? (
            <div className="empty-state">
              <span className="empty-icon" aria-hidden="true">↗</span>
              <strong>No runs yet</strong>
              <p>Connect a test suite and start a run to see its results here.</p>
              <Link className="button button-primary button-small" href="/projects/new">Set up a project</Link>
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table className="runs-table">
                <thead>
                  <tr><th>Run</th><th>Project</th><th>Target</th><th>Tests</th><th>Status</th><th aria-label="Open run" /></tr>
                </thead>
                <tbody>
                  {runs.slice(0, 8).map((run) => {
                    const progress = completion(run);
                    return (
                      <tr key={run.id}>
                        <td><Link className="run-id" href={`/runs/${run.id}`}>RUN-{String(run.id).padStart(4, "0")}</Link></td>
                        <td><div className="run-project"><strong>{projectName(projects, run.project_id)}</strong><small>Project #{run.project_id}</small></div></td>
                        <td><span title={projectUrl(projects, run.project_id)}>{shortUrl(projectUrl(projects, run.project_id))}</span></td>
                        <td>
                          {progress.total ? (
                            <span className="mini-progress">
                              <span className="progress-track"><span style={{ width: `${Math.min(100, (progress.count / progress.total) * 100)}%` }} /></span>
                              <small>{progress.count}/{progress.total}</small>
                            </span>
                          ) : <span aria-label="No test results yet">—</span>}
                        </td>
                        <td><StatusBadge status={run.status} /></td>
                        <td><Link className="text-link" href={`/runs/${run.id}`} aria-label={`View run ${run.id}`}>→</Link></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="panel" aria-labelledby="projects-title">
          <div className="panel-header">
            <div>
              <h2 className="panel-title" id="projects-title">Your projects</h2>
              <p className="panel-caption">Recently connected test suites</p>
            </div>
            <Link className="text-link" href="/projects/new">Add project →</Link>
          </div>
          <div className="panel-body">
            {projects.length === 0 && !loading ? (
              <p className="project-empty">No projects connected. Add your first suite to get started.</p>
            ) : (
              <div className="project-list">
                {[...projects].sort((a, b) => b.id - a.id).slice(0, 5).map((project) => (
                  <div className="project-row" key={project.id}>
                    <span className="project-mark">{project.name.slice(0, 2)}</span>
                    <span className="project-row-info">
                      <strong>{project.name}</strong>
                      <small>{shortUrl(project.app_url)}</small>
                    </span>
                    <button
                      className="button button-secondary button-small"
                      disabled={startingProject === project.id}
                      onClick={() => void startRun(project.id)}
                      aria-label={`Run tests for ${project.name}`}
                    >
                      {startingProject === project.id ? "Starting…" : "Run"}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>
      <p className="page-footnote">HealTest reviews proposed test changes before you approve them. Your application code is never modified.</p>
    </>
  );
}

function Metric({ label, value, icon, caption }: { label: string; value: string | number; icon: string; caption: string }) {
  return (
    <article className="metric-card">
      <div className="metric-top"><span>{label}</span><span className="metric-icon" aria-hidden="true">{icon}</span></div>
      <p className="metric-value">{value}</p>
      <p className="metric-caption">{caption}</p>
    </article>
  );
}

function shortUrl(value: string): string {
  try {
    const url = new URL(value);
    return `${url.hostname}${url.pathname === "/" ? "" : url.pathname}`;
  } catch {
    return value;
  }
}
