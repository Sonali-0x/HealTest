"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, apiUrl } from "../lib/api";
import {
  beginCognitoLogin,
  cognitoAccessToken,
  cognitoConfigured,
  signOutCognito,
} from "../lib/cognito";

const links = [
  { href: "/", label: "Overview", glyph: "◫" },
  { href: "/projects/new", label: "New project", glyph: "+" },
  { href: "/benchmarks", label: "Benchmarks", glyph: "▥" },
  { href: "/settings", label: "Settings", glyph: "⚙" },
];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [connected, setConnected] = useState<boolean | null>(null);
  const [authState, setAuthState] = useState<"checking" | "signed-in" | "signed-out">(
    "checking",
  );
  const [authError, setAuthError] = useState("");

  useEffect(() => {
    if (!cognitoConfigured() || pathname === "/auth/callback") {
      setAuthState("signed-in");
      return;
    }
    let active = true;
    cognitoAccessToken()
      .then((token) => {
        if (active) setAuthState(token ? "signed-in" : "signed-out");
      })
      .catch((reason: unknown) => {
        if (active) {
          setAuthError(reason instanceof Error ? reason.message : "Sign-in is unavailable.");
          setAuthState("signed-out");
        }
      });
    return () => {
      active = false;
    };
  }, [pathname]);

  useEffect(() => {
    let active = true;
    api<{ status: string }>("/health")
      .then(() => active && setConnected(true))
      .catch(() => active && setConnected(false));
    return () => {
      active = false;
    };
  }, []);

  if (
    cognitoConfigured() &&
    pathname !== "/auth/callback" &&
    authState !== "signed-in"
  ) {
    return (
      <main className="main-content">
        <section className="panel" style={{ maxWidth: 480, margin: "15vh auto" }}>
          <div className="panel-body">
            <p className="eyebrow">SECURE WORKSPACE</p>
            <h1>{authState === "checking" ? "Checking your session…" : "Sign in to HealTest"}</h1>
            <p>Your workspace data is available only after authentication.</p>
            {authError && <p role="alert">{authError}</p>}
            {authState === "signed-out" && (
              <button
                className="button button-primary"
                onClick={() => {
                  setAuthError("");
                  void beginCognitoLogin().catch((reason: unknown) =>
                    setAuthError(
                      reason instanceof Error ? reason.message : "Could not start sign-in.",
                    ),
                  );
                }}
              >
                Sign in
              </button>
            )}
          </div>
        </section>
      </main>
    );
  }

  return (
    <div className="app-frame">
      <aside className="sidebar">
        <Link className="brand" href="/" aria-label="HealTest home">
          <span className="brand-mark">H</span>
          <span>heal<span className="brand-light">test</span></span>
        </Link>
        <div className="workspace-switch">
          <span className="workspace-icon">HT</span>
          <span><strong>My workspace</strong><small>Personal</small></span>
          <span className="switch-chevron">⌄</span>
        </div>
        <p className="nav-caption">WORKSPACE</p>
        <nav className="main-nav" aria-label="Main navigation">
          {links.map((link) => {
            const active =
              link.href === "/"
                ? pathname === "/"
                : pathname === link.href || pathname.startsWith(`${link.href}/`);
            return (
              <Link
                className={`nav-link${active ? " active" : ""}`}
                href={link.href}
                key={link.href}
                aria-current={active ? "page" : undefined}
              >
                <span className="nav-glyph" aria-hidden="true">{link.glyph}</span>
                {link.label}
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-spacer" />
        <div className="sidebar-foot">
          <span className={`health-dot ${connected === false ? "offline" : ""}`} />
          <span>{connected === null ? "Connecting to API" : connected ? "API connected" : "API unavailable"}</span>
          <span className="health-url" title={apiUrl()}>↗</span>
        </div>
        <div className="profile-card">
          <span className="avatar">S</span>
          <span><strong>My workspace</strong><small>Signed-in account</small></span>
          {cognitoConfigured() && (
            <button
              className="profile-menu"
              aria-label="Sign out"
              onClick={signOutCognito}
            >
              Sign out
            </button>
          )}
        </div>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb"><span>Workspace</span><span className="crumb-slash">/</span><strong>{links.find((item) => item.href === pathname)?.label ?? (pathname.startsWith("/runs/") ? "Run details" : "HealTest")}</strong></div>
          <div className="topbar-actions">
            <span className="environment-tag"><span className="mini-dot" /> HEALTEST</span>
            <Link className="icon-button" href="/settings" aria-label="Open settings" title="Settings">⌘</Link>
          </div>
        </header>
        <div className="page-content">{children}</div>
      </main>
    </div>
  );
}
