export type Project = {
  id: number;
  name: string;
  app_url: string;
  test_path: string;
  retry_limit: number;
};

export type Run = {
  id: number;
  project_id: number;
  status: string;
  total_tests: number;
  failed_tests: number;
  flaky_tests: number;
  error?: string;
};

export type Artifact = {
  id: number;
  type: string;
  path: string;
  url: string;
};

export type Attempt = {
  number: number;
  failure_class: string;
  confidence: number;
  outcome: string;
  explanation: string;
  model_tokens: number;
  duration_ms: number;
  artifacts: Artifact[];
};

export type Patch = {
  id: number;
  status: string;
  rationale: string;
  diff: string;
  approved_copy_path: string | null;
};

export type TestResult = {
  id: number;
  test_name: string;
  status: string;
  final_state: string;
  flaky: boolean;
  error_text: string;
  attempts: Attempt[];
  patches: Patch[];
};

export type RunDetail = {
  run: Run;
  results: TestResult[];
};

export type BenchmarkArm = "no_healing" | "heuristic" | "healtest";

export type BenchmarkMetrics = {
  total: number;
  healed: number;
  heal_rate: number;
  avg_attempts: number;
  median_seconds: number;
  unsafe_fixes: number;
  tokens: number;
  tokens_per_heal: number | null;
  cost_per_heal: number | null;
};

export type BenchmarkCase = {
  case_id: string;
  app: string;
  mutation_type: string;
  arm: BenchmarkArm;
  healed: boolean;
  attempts: number;
  seconds: number;
  unsafe_fixes: number;
  tokens: number;
  error: string;
  run_id: number | null;
};

export type BenchmarkReport = {
  seed: number;
  case_count: number;
  model_mode: "mock" | "live" | "invalid";
  providers_configured: boolean;
  arms: Record<BenchmarkArm, BenchmarkMetrics>;
  by_mutation: Record<string, Record<BenchmarkArm, BenchmarkMetrics>>;
  failures: Array<{
    case_id: string;
    app: string;
    mutation_type: string;
    arm: BenchmarkArm;
    error: string;
  }>;
  cases: BenchmarkCase[];
};

export type Benchmark = {
  id: number;
  status: string;
  seed: number;
  started_at: string;
  finished_at: string | null;
  error: string;
  heal_rate: number;
  avg_attempts: number;
  median_seconds: number;
  unsafe_fixes: number;
  report: Partial<BenchmarkReport>;
};

export type RunEvent = {
  run_id: number;
  event: "snapshot" | "error";
  status?: string;
  total_tests?: number;
  failed_tests?: number;
  flaky_tests?: number;
  error?: string;
};

const API_URL_KEY = "healtest.apiUrl";
const API_TOKEN_KEY = "healtest.apiToken";

export function apiUrl(): string {
  if (typeof window !== "undefined") {
    const saved = window.localStorage.getItem(API_URL_KEY)?.trim();
    if (saved) return saved.replace(/\/+$/, "");
  }
  return (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
}

function headers(init?: RequestInit): Headers {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (typeof window !== "undefined") {
    const token = window.sessionStorage.getItem(API_TOKEN_KEY);
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }
  return headers;
}

async function responseError(response: Response, path: string): Promise<Error> {
  const body = await response.text();
  let detail = body;
  try {
    const parsed: unknown = JSON.parse(body);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      "detail" in parsed &&
      typeof parsed.detail === "string"
    ) {
      detail = parsed.detail;
    }
  } catch {
    detail = body;
  }
  return new Error(detail || `API ${response.status} on ${path}`);
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiUrl()}${path}`, {
    ...init,
    headers: headers(init),
    cache: "no-store",
  });
  if (!response.ok) throw await responseError(response, path);
  return (await response.json()) as T;
}

export async function apiBlob(path: string): Promise<Blob> {
  const response = await fetch(`${apiUrl()}${path}`, {
    headers: headers(),
    cache: "no-store",
  });
  if (!response.ok) throw await responseError(response, path);
  return response.blob();
}

export function saveApiSettings(url: string, token: string): void {
  if (url.trim()) window.localStorage.setItem(API_URL_KEY, url.trim().replace(/\/+$/, ""));
  else window.localStorage.removeItem(API_URL_KEY);
  if (token) window.sessionStorage.setItem(API_TOKEN_KEY, token);
  else window.sessionStorage.removeItem(API_TOKEN_KEY);
}

export function apiSettings(): { url: string; token: string } {
  return {
    url: apiUrl(),
    token:
      typeof window === "undefined"
        ? ""
        : window.sessionStorage.getItem(API_TOKEN_KEY) ?? "",
  };
}

export async function watchRunEvents(
  runId: number,
  signal: AbortSignal,
  onEvent: (event: RunEvent) => void,
): Promise<void> {
  const path = `/runs/${runId}/events`;
  const response = await fetch(`${apiUrl()}${path}`, {
    headers: headers(),
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw await responseError(response, path);
  if (!response.body) throw new Error("Run event stream is unavailable");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim())
          .join("\n");
        if (data) onEvent(JSON.parse(data) as RunEvent);
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}
