"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { api, Project } from "../../../lib/api";

const sampleProjects = [
  { name: "Todo app", path: "/sample_apps/todo/tests" },
  { name: "Shop app", path: "/sample_apps/shop/tests" },
  { name: "Multistep form", path: "/sample_apps/multistep-form/tests" },
];

export default function NewProjectPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [appUrl, setAppUrl] = useState("http://sample-apps:8901");
  const [testPath, setTestPath] = useState("");
  const [retryLimit, setRetryLimit] = useState("3");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function chooseSample(path: string) {
    setTestPath(path);
    if (!name.trim()) {
      const sample = sampleProjects.find((item) => item.path === path);
      if (sample) setName(sample.name);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const project = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          app_url: appUrl.trim(),
          test_path: testPath.trim(),
          retry_limit: Number(retryLimit),
        }),
      });
      router.push("/");
      router.refresh();
      void project;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create the project.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <Link className="back-link" href="/">← Back to overview</Link>
          <p className="eyebrow">PROJECT SETUP</p>
          <h1>Connect a test suite</h1>
          <p className="page-subtitle">Tell HealTest where your browser app and pytest suite live.</p>
        </div>
      </div>
      <div className="form-layout">
        <form className="form-panel" onSubmit={(event) => void submit(event)}>
          <h2>Project details</h2>
          <p>These settings are used when the worker runs your suite.</p>
          {error && <div className="inline-error" role="alert" style={{ marginBottom: 16 }}>{error}</div>}
          <div className="form-fields">
            <div className="field">
              <label htmlFor="project-name">Project name</label>
              <input id="project-name" autoComplete="organization" required maxLength={200} value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Checkout flow" />
            </div>
            <div className="field">
              <label htmlFor="sample-suite">Start with a sample app (optional)</label>
              <select id="sample-suite" defaultValue="" onChange={(event) => event.target.value && chooseSample(event.target.value)}>
                <option value="">Choose a sample suite…</option>
                {sampleProjects.map((sample) => <option key={sample.path} value={sample.path}>{sample.name}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="app-url">Application URL</label>
              <input id="app-url" type="url" required maxLength={500} value={appUrl} onChange={(event) => setAppUrl(event.target.value)} placeholder="http://sample-apps:8901" />
              <span className="field-hint">Use the address the worker can reach. Compose sample apps use <code>http://sample-apps:8901</code>.</span>
            </div>
            <div className="field">
              <label htmlFor="test-path">Pytest directory</label>
              <input id="test-path" required maxLength={500} value={testPath} onChange={(event) => setTestPath(event.target.value)} placeholder="/sample_apps/your-app/tests" />
              <span className="field-hint">This path must exist inside the worker container or execution environment.</span>
            </div>
            <div className="field">
              <label htmlFor="retry-limit">Maximum repair attempts</label>
              <select id="retry-limit" value={retryLimit} onChange={(event) => setRetryLimit(event.target.value)}>
                <option value="1">1 attempt</option>
                <option value="2">2 attempts</option>
                <option value="3">3 attempts</option>
              </select>
              <span className="field-hint">A lower limit may reduce model usage. Unsafe patches are never applied.</span>
            </div>
          </div>
          <div className="form-actions">
            <Link className="button button-secondary" href="/">Cancel</Link>
            <button className="button button-primary" type="submit" disabled={saving}>{saving ? "Connecting…" : "Connect project →"}</button>
          </div>
        </form>
        <aside className="hint-card">
          <h3>Before you connect</h3>
          <p>HealTest executes pytest in a worker and captures browser evidence when a test fails.</p>
          <ul>
            <li>Ensure the target app is reachable from the worker.</li>
            <li>Use an absolute test directory path in the worker.</li>
            <li>In Docker sandbox mode, allowlist the target hostname.</li>
            <li>Generated edits are limited to test files and reviewed before approval.</li>
          </ul>
        </aside>
      </div>
    </>
  );
}
