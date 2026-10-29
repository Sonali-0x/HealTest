export default function StatusBadge({ status }: { status: string }) {
  return <span data-status={status}>{status}</span>;
}
