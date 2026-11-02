"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import StatusBadge from "../../../components/StatusBadge";
import {
  api,
  apiBlob,
  Artifact,
  Patch,
  RunDetail,
  RunEvent,
  watchRunEvents,
} from "../../../lib/api";

export default function RunDetailPage() {
  const params = useParams<{ id: string }>();
  const runId = Number(params.id);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busyPatch, setBusyPatch] = useState<number | null>(null);
  const [streamConnected, setStreamConnected] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const next = await api<RunDetail>(`/runs/${runId}`);
      setDetail(next);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load run details.");
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    if (!Number.isInteger(runId) || runId < 1) {
      setError("This run URL is not valid.");
      setLoading(false);
      return;
    }
    void refresh();
    const timer = window.setInterval(() => {
      if (detail?.run.status === "queued" || detail?.run.status === "running") void refresh();
    }, 2500);
    const controller = new AbortController();
    setStreamConnected(true);
    watchRunEvents(runId, controller.signal, (event: RunEvent) => {
      if (event.event === "error") {
        setActionError(event.error ?? "The run event stream reported an error.");
      } else {
        void refresh();
      }
    })
      .catch(() => {
        if (!controller.signal.aborted) setStreamConnected(false);
      });
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [detail?.run.status, refresh, runId]);

  const results = useMemo(() => detail?.results ?? [], [detail?.results]);
  const attempts = useMemo(
    () => results.flatMap((result) => result.attempts),
    [results],
  );
  const patches = useMemo(
    () => results.flatMap((result) => result.patches),
    [results],
  );
  const run = detail?.run;

  async function patchAction(patch: Patch, action: "approve" | "reject") {
    const prompt =
      action === "approve"
        ? "Approve this patch? HealTest will apply it to a separate test copy. Your original files will not be changed."
        : "Reject this patch?";
    if (!window.confirm(prompt)) return;
    setBusyPatch(patch.id);
    setActionError("");
    try {
      await api<Patch>(`/patches/${patch.id}/${action}`, { method: "POST" });
      await refresh();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : `Could not ${action} this patch.`);
    } finally {
      setBusyPatch(null);
    }
  }

  async function downloadPatch(patch: Patch) {
    setBusyPatch(patch.id);
    setActionError("");
    try {
      const blob = await apiBlob(`/patches/${patch.id}/download`);
      downloadBlob(blob, `patch-${patch.id}.diff`);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Could not download the diff.");
    } finally {
      setBusyPatch(null);
    }
  }

  if (loading) {
    return <div className="loading-box" role="status">Loading run details…</div>;
  }
  if (!run) {
    return (
      <>
        <Link className="back-link" href="/">← Back to overview</Link>
        <div className="error-box" role="alert">{error || "This run could not be found."}</div>
      </>
    );
  }

  return (
    <>
      <div className="page-heading detail-heading">
        <div>
          <Link className="back-link" href="/">← Back to overview</Link>
          <p className="eyebrow">RUN-{String(run.id).padStart(4, "0")}</p>
          <h1>Run details</h1>
          <div className="detail-meta">
            <span>Project <strong>#{run.project_id}</strong></span>
            <span>·</span>
            <StatusBadge status={run.status} />
            {(run.status === "queued" || run.status === "running") && <span className="live-indicator"><i /> {streamConnected ? "Live updates" : "Polling for updates"}</span>}
          </div>
        </div>
        <div className="detail-actions">
          <button className="button button-secondary" onClick={() => void refresh()}>↻ Refresh</button>
        </div>
      </div>

      {error && <div className="error-box" role="alert" style={{ marginBottom: 15 }}>{error}</div>}
      {actionError && <div className="error-box" role="alert" style={{ marginBottom: 15 }}>{actionError}</div>}

      <section className="detail-summary" aria-label="Run summary">
        <Summary label="Run status" value={run.status} />
        <Summary label="Tests collected" value={run.total_tests || (run.status === "running" ? "Running…" : 0)} />
        <Summary label="Original failures" value={run.failed_tests} />
        <Summary label="Flaky verifications" value={run.flaky_tests} />
        <Summary label="Repair attempts" value={attempts.length} />
      </section>

      {run.error && <div className="inline-error" role="status" style={{ marginBottom: 15 }}>{run.error}</div>}

      {(run.status === "queued" || run.status === "running") && results.length === 0 && (
        <section className="panel" style={{ marginBottom: 15 }}>
          <div className="empty-state">
            <span className="empty-icon" aria-hidden="true">↻</span>
            <strong>{run.status === "queued" ? "Run is queued" : "Run is in progress"}</strong>
            <p>We’ll update this view as results and repair attempts arrive.</p>
          </div>
        </section>
      )}

      <div className="detail-columns">
        <div className="section-stack">
          <section className="panel" aria-labelledby="test-results-title">
            <div className="panel-header">
              <div>
                <h2 className="panel-title" id="test-results-title">Test results &amp; repair timeline</h2>
                <p className="panel-caption">Diagnosis is shown before the proposed change.</p>
              </div>
              <span className="panel-caption">{results.length} {results.length === 1 ? "test" : "tests"}</span>
            </div>
            {results.length === 0 ? (
              <div className="empty-state">
                <span className="empty-icon" aria-hidden="true">✓</span>
                <strong>{run.status === "running" ? "Collecting results" : "No failed tests"}</strong>
                <p>{run.status === "running" ? "Test results will appear here shortly." : "This run finished without failures that need repair."}</p>
              </div>
            ) : results.map((result) => (
              <article className="result-card" key={result.id}>
                <div className="result-card-heading">
                  <h3 title={result.test_name}>{result.test_name}</h3>
                  <StatusBadge status={result.final_state || result.status} />
                </div>
                <pre className="result-error">{result.error_text || "No failure output was recorded."}</pre>
                <div className="timeline" aria-label={`Repair timeline for ${result.test_name}`}>
                  <TimelineItem title="Test failed" tone="failed" description="Original pytest failure captured." />
                  {result.attempts.map((attempt) => (
                    <div className="timeline-item" key={`${result.id}-${attempt.number}`}>
                      <span className={`timeline-node ${attempt.outcome === "healed" ? "" : "failed"}`} />
                      <div className="timeline-content">
                        <div className="timeline-top">
                          <strong>Attempt {attempt.number}: {attempt.failure_class || "Diagnosis"}</strong>
                          <StatusBadge status={attempt.outcome} />
                        </div>
                        <p>{attempt.explanation || "No diagnosis explanation was recorded."}</p>
                        <div className="timeline-details">
                          Confidence {Math.round(attempt.confidence * 100)}% · {attempt.duration_ms} ms · {attempt.model_tokens} tokens
                        </div>
                        {attempt.outcome === "healed" ? (
                          <div className="verification-card" style={{ marginTop: 10 }}>
                            <span className="verification-icon" aria-hidden="true">✓</span>
                            <div><strong>Verification passed</strong><p>The selected test passed when rerun against the temporary patched copy.</p></div>
                          </div>
                        ) : attempt.outcome === "verification failed" || attempt.outcome === "unsafe patch" || attempt.outcome === "fix failed" ? (
                          <div className="verification-card verification-failed" style={{ marginTop: 10 }}>
                            <span className="verification-icon" aria-hidden="true">!</span>
                            <div><strong>{attempt.outcome === "unsafe patch" ? "Safety check rejected this patch" : attempt.outcome === "fix failed" ? "Patch generation failed" : "Verification did not pass"}</strong><p>{attempt.explanation.split("\n").slice(1).join(" ") || "A later attempt may incorporate this failure."}</p></div>
                          </div>
                        ) : null}
                        {attempt.artifacts.length > 0 && <ArtifactList artifacts={attempt.artifacts} />}
                      </div>
                    </div>
                  ))}
                  {result.attempts.length === 0 && <TimelineItem title="Awaiting diagnosis" description="The repair workflow has not produced an attempt yet." />}
                </div>
              </article>
            ))}
          </section>
        </div>

        <aside className="section-stack">
          <section className="panel" aria-labelledby="patches-title">
            <div className="panel-header">
              <div>
                <h2 className="panel-title" id="patches-title">Proposed changes</h2>
                <p className="panel-caption">Review each test-only diff before approval.</p>
              </div>
            </div>
            <div className="panel-body">
              {patches.length === 0 ? (
                <p className="project-empty">No patch has been proposed for this run.</p>
              ) : patches.map((patch) => (
                <article className="patch-card" key={patch.id}>
                  <div className="patch-card-top">
                    <strong className="mono" style={{ fontSize: 10 }}>PATCH-{String(patch.id).padStart(4, "0")}</strong>
                    <StatusBadge status={patch.status} />
                  </div>
                  <p>{patch.rationale}</p>
                  <DiffBlock diff={patch.diff} />
                  {patch.approved_copy_path && (
                    <div className="approved-copy">
                      Approved test copy<br />{patch.approved_copy_path}
                    </div>
                  )}
                  <div className="patch-buttons">
                    <button className="button button-secondary button-small" disabled={busyPatch === patch.id} onClick={() => void downloadPatch(patch)}>↓ Export diff</button>
                    {patch.status === "proposed" && (
                      <>
                        <button className="button button-primary button-small" disabled={busyPatch === patch.id} onClick={() => void patchAction(patch, "approve")}>{busyPatch === patch.id ? "Working…" : "Approve"}</button>
                        <button className="button button-danger button-small" disabled={busyPatch === patch.id} onClick={() => void patchAction(patch, "reject")}>Reject</button>
                      </>
                    )}
                  </div>
                </article>
              ))}
            </div>
          </section>
          <section className="panel" aria-labelledby="safety-note-title">
            <div className="panel-header"><h2 className="panel-title" id="safety-note-title">Review before approval</h2></div>
            <div className="panel-body">
              <div className="review-check"><span>✓</span><p>Only test files are eligible for a patch.</p></div>
              <div className="review-check"><span>✓</span><p>Assertions and newly introduced locators are checked.</p></div>
              <div className="review-check"><span>✓</span><p>Approval saves a copy; original project files stay untouched.</p></div>
            </div>
          </section>
        </aside>
      </div>
    </>
  );
}

function Summary({ label, value }: { label: string; value: string | number }) {
  return <div className="summary-chip"><span>{label}</span><strong>{value}</strong></div>;
}

function TimelineItem({ title, description, tone }: { title: string; description: string; tone?: "failed" }) {
  return (
    <div className="timeline-item">
      <span className={`timeline-node ${tone === "failed" ? "failed" : ""}`} />
      <div className="timeline-content"><div className="timeline-top"><strong>{title}</strong></div><p>{description}</p></div>
    </div>
  );
}

function ArtifactList({ artifacts }: { artifacts: Artifact[] }) {
  const [preview, setPreview] = useState<{
    id: number;
    type: "screenshot" | "mask";
    imageUrl: string;
    screenshotUrl: string;
  } | null>(null);
  const [error, setError] = useState("");

  async function openArtifact(artifact: Artifact) {
    setError("");
    try {
      if (artifact.type === "screenshot") {
        const screenshot = URL.createObjectURL(await apiBlob(artifact.url));
        setPreview({
          id: artifact.id,
          type: "screenshot",
          imageUrl: screenshot,
          screenshotUrl: screenshot,
        });
      } else if (artifact.type === "mask") {
        const screenshotArtifact = artifacts.find((item) => item.type === "screenshot");
        if (!screenshotArtifact) {
          throw new Error("The screenshot needed to preview this visual mask is unavailable.");
        }
        const [maskBlob, screenshotBlob] = await Promise.all([
          apiBlob(artifact.url),
          apiBlob(screenshotArtifact.url),
        ]);
        setPreview({
          id: artifact.id,
          type: "mask",
          imageUrl: URL.createObjectURL(maskBlob),
          screenshotUrl: URL.createObjectURL(screenshotBlob),
        });
      } else {
        const blob = await apiBlob(artifact.url);
        downloadBlob(blob, `${artifact.type}-${artifact.id}${extension(artifact.type)}`);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load evidence.");
    }
  }

  useEffect(() => () => {
    if (!preview) return;
    URL.revokeObjectURL(preview.imageUrl);
    if (preview.screenshotUrl !== preview.imageUrl) {
      URL.revokeObjectURL(preview.screenshotUrl);
    }
  }, [preview]);

  return (
    <>
      <div className="artifact-list" aria-label="Captured evidence">
        {artifacts.map((artifact) => (
          <button className="artifact-chip" key={artifact.id} onClick={() => void openArtifact(artifact)}>
            {artifact.type === "screenshot" || artifact.type === "mask" ? "▧" : "↓"} {formatArtifactType(artifact.type)}
          </button>
        ))}
      </div>
      {error && <div className="inline-error" role="alert" style={{ marginTop: 8 }}>{error}</div>}
      {preview && (
        <div className="visual-preview" aria-label={preview.type === "mask" ? "SAM visual mask over the captured screenshot" : "Captured browser screenshot"}>
          <img
            src={preview.screenshotUrl}
            alt="Captured browser screenshot from the failing test"
          />
          {preview.type === "mask" && (
            <img className="mask-overlay" src={preview.imageUrl} alt="" aria-hidden="true" />
          )}
        </div>
      )}
    </>
  );
}

function DiffBlock({ diff }: { diff: string }) {
  return (
    <pre className="diff-block" aria-label="Proposed unified diff">
      {diff.split("\n").map((line, index) => (
        <span className={line.startsWith("+") ? "diff-line-add" : line.startsWith("-") ? "diff-line-remove" : ""} key={`${index}-${line}`}>{line}{"\n"}</span>
      ))}
    </pre>
  );
}

function formatArtifactType(type: string): string {
  return type.replaceAll("_", " ");
}

function extension(type: string): string {
  const extensions: Record<string, string> = {
    screenshot: ".png",
    mask: ".png",
    dom: ".html",
    elements: ".json",
    manifest: ".json",
    console: ".log",
    error_trace: ".txt",
    failing_location: ".txt",
    page_url: ".txt",
  };
  return extensions[type] ?? ".bin";
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
