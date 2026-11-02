/**
 * Export is one request: a body naming the rows, and the file as the answer.
 *
 * It used to be a job — find a screen id, enqueue a bundle, poll the request,
 * then follow the artifact link — and the first hop never worked: `GET /screens`
 * answers a snapshot document with no `screen_id`, so the dialog could only
 * fail. The route is now synchronous (`POST /screens/export`), which makes the
 * client's job small enough to state in one sentence: build the body, POST it,
 * hand the response's bytes to the browser.
 *
 * Two boundaries are deliberate and tested:
 *
 * * **The rows are the server's.** Nothing here reads the DOM, a row object or
 *   a column label. The file is whatever `POST /screens/export` wrote, which is
 *   the selection pipeline (filters, ordering, column resolver, stores) — so a
 *   venue is `XNAS` in the file even while the grid draws `NASDAQ`. A client
 *   that serialised its own rows would publish the labels.
 * * **The cap is 200, and the client says so before it asks.** `clampRows`
 *   clamps the input and reports that it did, so a reader who types 500 sees
 *   200 and a sentence — not a silent 200-row file they believe holds 500.
 *   The API refuses >200 with a 422 rather than clamping, because a server that
 *   silently truncates has told the caller the file is complete when it is a
 *   prefix.
 */

import type { ScreenFilter } from "@/data/screens";
import { postFile, readErrorMessage } from "@/lib/api";

/** The one export route. Absolute from the origin, like every path in `lib/api`. */
export const EXPORT_URL = "/screens/export";

/** The three file formats, in the menu's order. */
export const EXPORT_FORMATS = ["csv", "xlsx", "json"] as const;
export type ExportFormat = (typeof EXPORT_FORMATS)[number];

/** The toolbar's four choices: a file format, or the URL the call uses. */
export type ExportChoice = ExportFormat | "api";

/** What a file covers. */
export const EXPORT_SCOPES = ["screen", "page", "selected", "tickers"] as const;
export type ExportScope = (typeof EXPORT_SCOPES)[number];

export const SCOPE_LABELS: Record<ExportScope, string> = {
  screen: "Whole screen",
  page: "This page",
  selected: "Selected rows",
  tickers: "Tickers",
};

/** The server's cap, mirrored so the dialog can clamp before it asks. */
export const MAX_EXPORT_ROWS = 200;
export const DEFAULT_EXPORT_ROWS = MAX_EXPORT_ROWS;

/** What the dialog says when it clamps the reader's number. */
export const EXPORT_ROW_CLAMP = `Rows are capped at ${MAX_EXPORT_ROWS}: this file will hold the first ${MAX_EXPORT_ROWS} matches.`;

/** The criteria half of a screen, exactly as the grid already holds it. */
export interface ExportCriteria {
  filters: readonly ScreenFilter[];
  sort?: string;
  view?: string;
}

export interface ExportInput {
  scope: ExportScope;
  format: ExportFormat;
  /** The requested row count; clamped to the cap by `buildExportBody`. */
  rows: number;
  region?: string;
  criteria?: ExportCriteria;
  /** The tickers the scope names (page, selection or the typed list). */
  symbols?: readonly string[];
  /** The cells the reader wants; empty asks for the row's own shape. */
  columns?: readonly string[];
}

export interface ExportBody {
  mode: "criteria" | "symbols";
  region?: string;
  criteria?: { filters: unknown[]; sort?: string; view?: string };
  symbols?: string[];
  columns?: string[];
  format: ExportFormat;
  limit: number;
}

export interface ExportFile {
  blob: Blob;
  /** The server's `Content-Disposition` name, or a dated fallback. */
  filename: string;
  /** The server's `X-Row-Count`: how many rows the body actually holds. */
  rowCount: number;
}

/** A refusal the reader can act on: the status, and the house message. */
export class ExportError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ExportError";
    this.status = status;
  }
}

/**
 * The row limit, clamped to the cap.
 *
 * A blank, zero, negative or unparseable value means the default — asking for
 * "no rows" is asking for the file, and 0 is how a number input spells empty.
 * A value over the cap is *clamped and reported*: `clamped` is what turns the
 * dialog's note on, so the correction is visible rather than silent.
 */
export function clampRows(value: string | number): { rows: number; clamped: boolean } {
  const parsed =
    typeof value === "number" ? value : Number.parseInt(value.replace(/[^\d-]/g, ""), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return { rows: DEFAULT_EXPORT_ROWS, clamped: false };
  }
  const whole = Math.trunc(parsed);
  if (whole > MAX_EXPORT_ROWS) return { rows: MAX_EXPORT_ROWS, clamped: true };
  return { rows: whole, clamped: false };
}

/** "aapl, msft brk.b" → `["AAPL", "MSFT", "BRK.B"]`, each ticker once. */
export function parseTickers(text: string): string[] {
  const seen = new Set<string>();
  const symbols: string[] = [];
  for (const piece of text.split(/[\s,;]+/)) {
    const symbol = piece.trim().toUpperCase();
    if (!symbol || seen.has(symbol)) continue;
    seen.add(symbol);
    symbols.push(symbol);
  }
  return symbols;
}

/** The request body `POST /screens/export` reads, from the dialog's choices. */
export function buildExportBody(input: ExportInput): ExportBody {
  const { rows } = clampRows(input.rows);
  const columns = input.columns && input.columns.length > 0 ? [...input.columns] : undefined;
  const base = {
    ...(input.region ? { region: input.region } : {}),
    ...(columns ? { columns } : {}),
    format: input.format,
    limit: rows,
  };

  if (input.scope === "screen") {
    const criteria = input.criteria ?? { filters: [] };
    return {
      mode: "criteria",
      ...base,
      criteria: {
        filters: [...criteria.filters],
        ...(criteria.sort ? { sort: criteria.sort } : {}),
        ...(criteria.view ? { view: criteria.view } : {}),
      },
    };
  }
  return { mode: "symbols", ...base, symbols: [...(input.symbols ?? [])] };
}

/**
 * The live count line: "5,120 match — exporting up to 200".
 *
 * It states two different numbers on purpose. `matched` is how many rows the
 * screen matches; the cap is how many of them this file can hold. One number
 * alone is how "exported 200 rows" gets read as "the screen has 200 rows".
 */
export function matchSummary(matched: number, rows: number): string {
  const count = Number.isFinite(matched) && matched > 0 ? Math.trunc(matched) : 0;
  return `${count.toLocaleString("en-US")} match — exporting up to ${rows}`;
}

/** The server's download name, when it sent one. */
export function filenameFrom(disposition: string | null): string | null {
  if (!disposition) return null;
  const quoted = /filename="([^"]+)"/i.exec(disposition);
  if (quoted?.[1]) return quoted[1];
  const bare = /filename=([^;]+)/i.exec(disposition);
  return bare?.[1]?.trim() ?? null;
}

/** One POST; the answer is the file. A non-2xx rejects with the server's words. */
export async function requestExport(
  body: ExportBody,
  signal?: AbortSignal,
): Promise<ExportFile> {
  const answer = await postFile(EXPORT_URL, body, signal);

  if (!answer.ok || answer.blob === null) {
    throw new ExportError(
      answer.status,
      readErrorMessage(answer.body, answer.status),
    );
  }

  return {
    blob: answer.blob,
    filename: filenameFrom(answer.contentDisposition) ?? `ztockz-export.${body.format}`,
    rowCount: answer.rowCount ?? 0,
  };
}

/**
 * Hand the server's bytes to the browser as a download.
 *
 * This is the only side effect in the export flow, and it is deliberately a
 * function of a `Blob` the server produced: no row, label or cell reaches it.
 * The object URL is revoked on a timer — revoking immediately can cancel the
 * download in some browsers, and never revoking leaks the blob for the life of
 * the tab.
 */
export function downloadFile(file: ExportFile): void {
  if (typeof document === "undefined") return;
  const href = URL.createObjectURL(file.blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = file.filename;
  anchor.rel = "noopener";
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 1_000);
}
