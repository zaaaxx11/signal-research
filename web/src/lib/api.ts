/**
 * The shell's entire network surface.
 *
 * Every path here is same-origin and root-absolute (`/symbols/search`, not
 * `/app/symbols/search`): the API is mounted at the origin root, the SPA at
 * /app, and in dev a Vite proxy forwards the four API prefixes to the Python
 * process. Nothing in this module accepts an absolute URL, so a component
 * cannot accidentally call a third party.
 */

/** One row of GET /symbols/search. */
export interface SymbolHit {
  symbol: string;
  name: string;
  exchange: string;
}

export type HealthState = "ok" | "degraded" | "failing" | "unknown";

export interface HealthCheck {
  status: string;
  ok?: boolean;
  error?: string | null;
  [key: string]: unknown;
}

/** The document GET /health answers with (app/api/health.py:build_health_report). */
export interface HealthReport {
  status: string;
  version: string;
  checked_at: string;
  elapsed_ms: number;
  startup_error: string | null;
  checks: Record<string, HealthCheck>;
}

/** A non-2xx answer, keeping the house envelope's message when there is one. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function messageOf(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null) {
    const record = body as Record<string, unknown>;
    const detail = record["errorMessage"] ?? record["detail"];
    if (typeof detail === "string" && detail.length > 0) return detail;
  }
  return fallback;
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (text.length === 0) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ApiError(response.status, "Response was not JSON");
  }
}

async function request(
  path: string,
  signal?: AbortSignal,
): Promise<{ status: number; ok: boolean; body: unknown }> {
  const response = await fetch(path, {
    signal: signal ?? null,
    headers: { accept: "application/json" },
    // Same-origin by construction; `same-origin` makes that explicit and stops
    // a redirect from silently carrying the request somewhere else.
    credentials: "same-origin",
    mode: "same-origin",
  });
  const body = await readJson(response);
  return { status: response.status, ok: response.ok, body };
}

async function getJson(path: string, signal?: AbortSignal): Promise<unknown> {
  const { status, ok, body } = await request(path, signal);
  if (!ok) throw new ApiError(status, messageOf(body, `HTTP ${status}`));
  return body;
}

function isSymbolHit(value: unknown): value is SymbolHit {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record["symbol"] === "string" &&
    typeof record["name"] === "string" &&
    typeof record["exchange"] === "string"
  );
}

/**
 * Ranked symbol lookup. The API refuses a blank query with 422, so the caller
 * must not ask; we also drop non-rows rather than rendering `undefined`.
 */
export async function searchSymbols(
  query: string,
  signal?: AbortSignal,
  limit = 8,
): Promise<SymbolHit[]> {
  const q = query.trim();
  if (q.length === 0) return [];
  const params = new URLSearchParams({ q, limit: String(limit) });
  const body = await getJson(`/symbols/search?${params.toString()}`, signal);
  if (!Array.isArray(body)) throw new ApiError(200, "Unexpected search payload");
  return body.filter(isSymbolHit);
}

function asHealthReport(value: unknown): HealthReport {
  if (typeof value !== "object" || value === null) {
    throw new ApiError(200, "Unexpected health payload");
  }
  const record = value as Record<string, unknown>;
  const checks = record["checks"];
  return {
    status: typeof record["status"] === "string" ? record["status"] : "unknown",
    version: typeof record["version"] === "string" ? record["version"] : "—",
    checked_at: typeof record["checked_at"] === "string" ? record["checked_at"] : "",
    elapsed_ms: typeof record["elapsed_ms"] === "number" ? record["elapsed_ms"] : 0,
    startup_error:
      typeof record["startup_error"] === "string" ? record["startup_error"] : null,
    checks:
      typeof checks === "object" && checks !== null
        ? (checks as Record<string, HealthCheck>)
        : {},
  };
}

/**
 * Readiness. A degraded deployment answers 503 with the *same* document, so a
 * 503 carrying a health report is a successful read of a bad state, not a
 * transport failure — the status line must be able to say "failing" instead of
 * collapsing to "offline" whenever the deployment is unwell.
 */
export async function fetchHealth(signal?: AbortSignal): Promise<HealthReport> {
  const { ok, status, body } = await request("/health", signal);
  const isReport =
    typeof body === "object" &&
    body !== null &&
    typeof (body as Record<string, unknown>)["status"] === "string";

  if (!ok && !(status === 503 && isReport)) {
    throw new ApiError(status, messageOf(body, `HTTP ${status}`));
  }
  return asHealthReport(body);
}
