"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, apiUrl } from "../lib/api";

const links = [
  { href: "/", label: "Overview", glyph: "◫" },
  { href: "/projects/new", label: "New project", glyph: "+" },
  { href: "/benchmarks", label: "Benchmarks", glyph: "▥" },
  { href: "/settings", label: "Settings", glyph: "⚙" },
];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [connected, setConnected] = useState<boolean | null>(null);

  useEffect(() => {
    let active = true;
    api<{ status: string }>("/health")
      .then(() => active && setConnected(true))
      .catch(() => active && setConnected(false));
    return () => {
      active = false;
    };
  }, []);

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
          <span><strong>Sonali</strong><small>Workspace owner</small></span>
          <span className="profile-menu">···</span>
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
