"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, apiSettings, saveApiSettings } from "../../lib/api";
import { cognitoConfigured } from "../../lib/cognito";

export default function SettingsPage() {
  const managedByDeployment = cognitoConfigured();
  const [apiBaseUrl, setApiBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [status, setStatus] = useState<"idle" | "checking" | "connected" | "error">("idle");
  const [message, setMessage] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const settings = apiSettings();
    setApiBaseUrl(settings.url);
    setToken(settings.token);
  }, []);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    saveApiSettings(apiBaseUrl, token.trim());
    setSaved(true);
    setStatus("idle");
    setMessage("Settings saved in this browser.");
  }

  async function checkConnection() {
    saveApiSettings(apiBaseUrl, token.trim());
    setStatus("checking");
    setMessage("");
    try {
      await api<{ status: string }>("/health");
      setStatus("connected");
      setMessage("HealTest API is reachable.");
    } catch (reason) {
      setStatus("error");
      setMessage(reason instanceof Error ? reason.message : "Could not connect to the API.");
    }
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">PREFERENCES</p>
          <h1>Workspace settings</h1>
          <p className="page-subtitle">Configure how this browser connects to your HealTest API.</p>
        </div>
      </div>
      <div className="settings-grid">
        <form className="form-panel" onSubmit={submit}>
          <h2>API connection</h2>
          <p>
            {managedByDeployment
              ? "API access and authentication are managed by this deployment."
              : "These settings apply to this browser only."}
          </p>
          <div className="form-fields">
            <div className="field">
              <label htmlFor="api-url">API base URL</label>
              <input id="api-url" type="url" required value={apiBaseUrl} onChange={(event) => { setApiBaseUrl(event.target.value); setSaved(false); }} placeholder="http://localhost:8000" disabled={managedByDeployment} />
              <span className="field-hint">
                {managedByDeployment
                  ? "Configured by the deployment; browser overrides are disabled."
                  : "The address of the FastAPI service, without a trailing slash."}
              </span>
            </div>
            <div className="field">
              <label htmlFor="api-token">{cognitoConfigured() ? "Cognito access token" : "Local development API token"}</label>
              <input id="api-token" type="password" autoComplete="new-password" value={token} onChange={(event) => { setToken(event.target.value); setSaved(false); }} placeholder={cognitoConfigured() ? "Managed by Cognito sign-in" : "Enter the local token configured in .env"} disabled={cognitoConfigured()} />
              <span className="field-hint">Stored in this tab’s session storage and sent as a Bearer token. Never add credentials to the project repository.</span>
            </div>
          </div>
          {message && (
            <div className={`settings-status${status === "error" ? " offline" : ""}`} role={status === "error" ? "alert" : "status"} style={{ marginTop: 16 }}>
              <span className={`health-dot${status === "error" ? " offline" : ""}`} />
              {status === "checking" ? "Checking connection…" : message}
            </div>
          )}
          <div className="form-actions">
            <button className="button button-secondary" type="button" onClick={() => void checkConnection()} disabled={status === "checking"}>Test connection</button>
            {!managedByDeployment && (
              <button className="button button-primary" type="submit">
                {saved ? "Saved ✓" : "Save settings"}
              </button>
            )}
          </div>
        </form>
        <aside className="section-stack">
          <section className="panel">
            <div className="panel-header"><h2 className="panel-title">Safe by default</h2></div>
            <div className="panel-body">
              <div className="review-check"><span>✓</span><p>Generated changes are restricted to test files.</p></div>
              <div className="review-check"><span>✓</span><p>Each patch is verified against a temporary copy.</p></div>
              <div className="review-check"><span>✓</span><p>Approval never overwrites your original project.</p></div>
            </div>
          </section>
          <section className="hint-card">
            <h3>Model configuration</h3>
            <p>Muse Spark, Muse Code, and SAM endpoint URLs and credentials are configured on the backend/worker, not in this browser.</p>
            <p>Use mock mode for offline development; set provider endpoints and credentials in your local server environment for live models.</p>
          </section>
        </aside>
      </div>
    </>
  );
}
