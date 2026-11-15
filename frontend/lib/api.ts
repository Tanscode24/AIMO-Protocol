// All network calls go to the AIMO backend only. The Valio API key never reaches the browser.
// Browser sessions use an HttpOnly cookie set by the backend (not readable by JavaScript, so it cannot be
// stolen by injected scripts). State-changing requests carry X-Requested-With, which the backend requires
// for cookie-authenticated writes (CSRF defence together with the CORS allow-list).
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export type SessionUser = { id: string; name: string; email: string; role: string; organization_id?: string };

export class ApiError extends Error {
  status: number;
  code?: string;
  correlationId?: string;
  constructor(status: number, message: string, code?: string, correlationId?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.correlationId = correlationId;
  }
}

export async function api<T = any>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    "X-Requested-With": "aimo-web",
    ...(init.headers as Record<string, string>),
  };
  let body = init.body;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers, body, cache: "no-store", credentials: "include" });
  } catch {
    throw new ApiError(0, `Cannot reach the AIMO backend at ${API_BASE}. Is it running?`, "NETWORK");
  }
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth/")) {
    if (!window.location.pathname.startsWith("/login")) window.location.href = "/login";
  }
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const msg = (data && (data.error || data.detail)) || `Request failed (${res.status})`;
    const details = data?.details ? ` — ${data.details.map((d: any) => `${d.field}: ${d.message}`).join("; ")}` : "";
    const cid = data?.correlation_id || res.headers.get("X-Correlation-ID") || undefined;
    throw new ApiError(res.status, `${typeof msg === "string" ? msg : JSON.stringify(msg)}${details}`, data?.code, cid);
  }
  return data as T;
}

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
