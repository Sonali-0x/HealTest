import { apiUrl } from "../lib/api";

export default function Home() {
  return (
    <main style={{ fontFamily: "sans-serif", padding: 24 }}>
      <h1>HealTest (Phase 0 scaffold)</h1>
      <p>Dashboard lands in Phase 4. API: {apiUrl()}/health</p>
    </main>
  );
}
