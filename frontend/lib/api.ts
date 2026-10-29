export function apiUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${apiUrl()}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`API ${res.status} on ${path}`);
  return (await res.json()) as T;
}
