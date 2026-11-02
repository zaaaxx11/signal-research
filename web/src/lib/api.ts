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

interface RequestOptions {
  signal?: AbortSignal;
  method?: "GET" | "POST";
  headers?: Record<string, string>;
  body?: string;
}

async function request(
  path: string,
  options: RequestOptions = {},
): Promise<{ status: number; ok: boolean; etag: string | null; body: unknown }> {
  const response = await fetch(path, {
    method: options.method ?? "GET",
    signal: options.signal ?? null,
    headers: { accept: "application/json", ...options.headers },
    body: options.body,
    // Same-origin by construction; `same-origin` makes that explicit and stops
    // a redirect from silently carrying the request somewhere else.
    credentials: "same-origin",
    mode: "same-origin",
    // The caller owns conditional requests (the selection route's weak ETag).
    // A browser cache answering with its own stale body would defeat that.
    cache: "no-store",
  });
  const body = await readJson(response);
  return { status: response.status, ok: response.ok, etag: response.headers.get("etag"), body };
}

export async function getJson(path: string, signal?: AbortSignal): Promise<unknown> {
  const { status, ok, body } = await request(path, { signal });
  if (!ok) throw new ApiError(status, messageOf(body, `HTTP ${status}`));
  return body;
}

/** The house envelope's message, for a caller that renders the failure. */
export function readErrorMessage(body: unknown, status: number): string {
  return messageOf(body, `HTTP ${status}`);
}

/** A POST whose answer is a file: the bytes plus the headers that name them. */
export interface FileAnswer {
  status: number;
  ok: boolean;
  /** The body, on success; `null` on a refusal. */
  blob: Blob | null;
  /** The server's `Content-Disposition`, unparsed — the caller owns the name. */
  contentDisposition: string | null;
  /** The server's `X-Row-Count`, when it sent one. */
  rowCount: number | null;
  /** The refusal envelope, when the server sent JSON. */
  body: unknown;
}

/**
 * One POST whose success is a file rather than a document.
 *
 * It lives here, beside `request`, because this module is the shell's **entire**
 * network surface — the mount test refuses a second `fetch(` anywhere in
 * `src/`, so a download that reached for the global would be a second, unowned
 * egress path. The transport policy is the same one `request` applies:
 * same-origin, `no-store`, and a refusal read as the house envelope.
 */
export async function postFile(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<FileAnswer> {
  const response = await fetch(path, {
    method: "POST",
    signal: signal ?? null,
    headers: { accept: "*/*", "content-type": "application/json" },
    body: JSON.stringify(body),
    credentials: "same-origin",
    mode: "same-origin",
    cache: "no-store",
  });

  if (!response.ok) {
    let refusal: unknown = null;
    try {
      refusal = await readJson(response);
    } catch {
      // A non-JSON refusal is still a refusal; the status sentence stands.
    }
    return {
      status: response.status,
      ok: false,
      blob: null,
      contentDisposition: null,
      rowCount: null,
      body: refusal,
    };
  }

  const rowCount = Number.parseInt(response.headers.get("x-row-count") ?? "", 10);
  return {
    status: response.status,
    ok: true,
    blob: await response.blob(),
    contentDisposition: response.headers.get("content-disposition"),
    rowCount: Number.isFinite(rowCount) ? rowCount : null,
    body: null,
  };
}

export interface ConditionalAnswer {
  status: number;
  etag: string | null;
  body: unknown;
}

/**
 * A JSON POST whose validator the caller owns.
 *
 * Unlike :func:`getJson` this does not throw on a non-2xx: a `304` is a success
 * with no body (the caller keeps its rows), and a `422`'s message is product
 * copy the grid shows. Transport failures still reject, so the caller's abort
 * and network handling stay in one place.
 */
export async function postJsonConditional(
  path: string,
  body: unknown,
  options: { signal?: AbortSignal; etag?: string | null } = {},
): Promise<ConditionalAnswer> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (options.etag) headers["if-none-match"] = options.etag;
  const answer = await request(path, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: options.signal,
  });
  return { status: answer.status, etag: answer.etag, body: answer.body };
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
  const { ok, status, body } = await request("/health", { signal });
  const isReport =
    typeof body === "object" &&
    body !== null &&
    typeof (body as Record<string, unknown>)["status"] === "string";

  if (!ok && !(status === 503 && isReport)) {
    throw new ApiError(status, messageOf(body, `HTTP ${status}`));
  }
  return asHealthReport(body);
}
