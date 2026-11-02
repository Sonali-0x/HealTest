const statusClass: Record<string, string> = {
  healed: "status-success",
  passed: "status-success",
  approved: "status-approved",
  queued: "status-running",
  running: "status-running",
  flaky: "status-escalated",
  proposed: "status-proposed",
  rejected: "status-rejected",
  error: "status-error",
  escalated: "status-escalated",
  "needs review": "status-escalated",
  "needs human review": "status-escalated",
  "suspected real bug": "status-escalated",
};

export default function StatusBadge({ status }: { status: string }) {
  const className = statusClass[status.toLowerCase()] ?? "status-neutral";
  return <span className={`status-pill ${className}`}>{status || "unknown"}</span>;
}
