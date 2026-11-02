/**
 * Export: pick what, pick how many, pick a format — then the file arrives.
 *
 * The screen's rows belong to the server (see `exportJob.ts`), so this dialog
 * never serialises anything: it collects four answers, POSTs one body and hands
 * the response's Blob to the browser. The four answers are:
 *
 * * **scope** — the whole screen, this page, the ticked rows, or a typed list.
 *   A page and a selection are ticker lists, and they travel as `mode:
 *   "symbols"`; the screen is its filters, and travels as `mode: "criteria"`.
 *   Sending the page as "the screen" is the bug this distinction exists to
 *   prevent: two different populations under one word.
 * * **rows** — 200 by default and 200 at most. Typing 500 clamps the input to
 *   200 *and says so*; a silent clamp would hand a reader a 200-row file they
 *   believe holds 500. The API refuses a request over the cap instead, so the
 *   two limits are one number in two places (the client's note and the 422).
 * * **format** — CSV, XLSX, JSON, or the API pane (the URL, and nothing else:
 *   there is no file to download for a URL).
 * * **columns** — the grid's visible cells, so the file holds what the reader
 *   is looking at rather than an arbitrary default.
 *
 * Every outcome is visible in place: a spinner while the request is open, a red
 * failure with a **Retry** that resends the same body, and a green success that
 * names the row count and the file. The download fires on success only — a
 * failed request must never leave an empty file in the reader's Downloads.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

import { DialogShell } from "./DialogShell";
import {
  DEFAULT_EXPORT_ROWS,
  EXPORT_FORMATS,
  EXPORT_ROW_CLAMP,
  EXPORT_URL,
  MAX_EXPORT_ROWS,
  SCOPE_LABELS,
  buildExportBody,
  clampRows,
  downloadFile,
  matchSummary,
  parseTickers,
  requestExport,
  type ExportBody,
  type ExportChoice,
  type ExportCriteria,
  type ExportFormat,
  type ExportScope,
} from "./exportJob";

export type { ExportChoice } from "./exportJob";

export interface ExportDialogProps {
  open: boolean;
  onClose: () => void;
  /** The format the toolbar's menu opened with (or `api`). */
  choice: ExportChoice;
  /** The screen's filters and ordering; ignored for the ticker scopes. */
  criteria: ExportCriteria;
  /** The grid's visible cells, in the reader's order. */
  columns?: readonly string[];
  region?: string;
  /** How many rows the screen matches — the live count line. */
  matched: number;
  /** The symbols on the page the reader is looking at. */
  pageSymbols?: readonly string[];
  /** The rows ticked in compare mode. */
  selectedSymbols?: readonly string[];
}

type Phase = "idle" | "loading" | "error" | "done";

const FORMAT_LABELS: Record<ExportChoice, string> = {
  csv: "CSV",
  xlsx: "XLSX",
  json: "JSON",
  api: "API",
};

const SCOPE_HINTS: Record<ExportScope, string> = {
  screen: "Every row the screen matches, in its own order.",
  page: "Only the rows on this page, in the order they are shown.",
  selected: "Only the rows ticked for comparison.",
  tickers: "The tickers you type, in the order you type them.",
};

function isFormat(value: ExportChoice): value is ExportFormat {
  return value !== "api";
}

export function ExportDialog({
  open,
  onClose,
  choice,
  criteria,
  columns = [],
  region = "us",
  matched,
  pageSymbols = [],
  selectedSymbols = [],
}: ExportDialogProps) {
  const [format, setFormat] = React.useState<ExportChoice>(choice);
  const [scope, setScope] = React.useState<ExportScope>("screen");
  /** What the reader typed; the effective limit is derived and clamped from it. */
  const [rowsText, setRowsText] = React.useState<string>(String(DEFAULT_EXPORT_ROWS));
  const [tickerText, setTickerText] = React.useState("");
  const [phase, setPhase] = React.useState<Phase>("idle");
  const [error, setError] = React.useState<string | null>(null);
  const [success, setSuccess] = React.useState<string | null>(null);
  /** The body behind the last attempt, so Retry resends exactly it. */
  const [attempt, setAttempt] = React.useState<ExportBody | null>(null);

  React.useEffect(() => {
    if (!open) return;
    setFormat(choice);
    setScope("screen");
    setRowsText(String(DEFAULT_EXPORT_ROWS));
    setTickerText("");
    setPhase("idle");
    setError(null);
    setSuccess(null);
    setAttempt(null);
  }, [open, choice]);

  // The clamp is derived, not stored: a number typed past the cap is clamped
  // *and* announced (the note below), while the field keeps the reader's digits
  // until they leave it — correcting a keystroke under their cursor is worse
  // than correcting it on blur.
  const limit = clampRows(rowsText);
  const rows = limit.rows;
  const clamped = limit.clamped && rowsText.trim() !== String(rows);

  const typed = React.useMemo(() => parseTickers(tickerText), [tickerText]);
  const symbols = React.useMemo<readonly string[]>(() => {
    if (scope === "page") return pageSymbols;
    if (scope === "selected") return selectedSymbols;
    if (scope === "tickers") return typed;
    return [];
  }, [scope, pageSymbols, selectedSymbols, typed]);

  const count = scope === "screen" ? matched : symbols.length;
  const canRun =
    isFormat(format) && phase !== "loading" && (scope === "screen" || symbols.length > 0);

  const run = React.useCallback(
    async (body: ExportBody) => {
      setAttempt(body);
      setPhase("loading");
      setError(null);
      setSuccess(null);
      try {
        const file = await requestExport(body);
        // Only after the server answered with bytes: a failed request must not
        // leave an empty download behind.
        downloadFile(file);
        setPhase("done");
        setSuccess(
          `Exported ${file.rowCount.toLocaleString("en-US")} ${
            file.rowCount === 1 ? "row" : "rows"
          } to ${file.filename}.`,
        );
      } catch (cause) {
        setPhase("error");
        setError(cause instanceof Error ? cause.message : "The export could not be written");
      }
    },
    [],
  );

  const submit = () => {
    if (!isFormat(format)) return;
    const body = buildExportBody({
      scope,
      format,
      rows,
      region,
      criteria,
      symbols,
      columns,
    });
    void run(body);
  };

  const retry = () => {
    if (attempt) void run(attempt);
  };

  return (
    <DialogShell
      open={open}
      onClose={onClose}
      title={format === "api" ? "Export API" : `Export ${FORMAT_LABELS[format]}`}
      description="A file of the server's rows — the same rows the screen shows, written by the screener."
      size="md"
    >
      <div className="flex flex-col gap-3">
        <div role="group" aria-label="Format" className="flex flex-wrap items-center gap-1">
          {[...EXPORT_FORMATS, "api" as const].map((option) => (
            <button
              key={option}
              type="button"
              data-testid={`export-format-${option}`}
              aria-pressed={format === option}
              onClick={() => {
                setFormat(option);
                setPhase("idle");
                setError(null);
                setSuccess(null);
                setAttempt(null);
              }}
              className={cn(
                "h-7 rounded-sm border px-2 text-ui transition-colors",
                format === option
                  ? "border-brand text-brand"
                  : "border-line-control text-fg-muted hover:border-brand hover:text-fg",
              )}
            >
              {FORMAT_LABELS[option]}
            </button>
          ))}
        </div>

        {format === "api" ? (
          <div className="flex flex-col gap-2" data-testid="export-api-pane">
            <pre
              data-testid="export-api-url"
              className="overflow-auto rounded-sm border border-line bg-surface-sunken p-2 font-mono text-data text-fg"
            >
              {EXPORT_URL}
            </pre>
            <p className="text-data text-fg-subtle">
              POST. This pane names the call; it never downloads a file.
            </p>
          </div>
        ) : (
          <>
            <div role="group" aria-label="Scope" className="flex flex-wrap items-center gap-1">
              {(Object.keys(SCOPE_LABELS) as ExportScope[]).map((option) => {
                const size =
                  option === "page"
                    ? pageSymbols.length
                    : option === "selected"
                      ? selectedSymbols.length
                      : option === "tickers"
                        ? typed.length
                        : null;
                return (
                  <button
                    key={option}
                    type="button"
                    data-testid={`export-scope-${option}`}
                    aria-pressed={scope === option}
                    onClick={() => {
                      setScope(option);
                      setPhase("idle");
                      setError(null);
                      setSuccess(null);
                    }}
                    className={cn(
                      "h-7 rounded-sm border px-2 text-ui transition-colors",
                      scope === option
                        ? "border-brand text-brand"
                        : "border-line-control text-fg-muted hover:border-brand hover:text-fg",
                    )}
                  >
                    {SCOPE_LABELS[option]}
                    {size === null ? null : (
                      <span className="ml-1 font-mono text-data text-fg-subtle">{size}</span>
                    )}
                  </button>
                );
              })}
            </div>

            <p className="text-data text-fg-subtle">{SCOPE_HINTS[scope]}</p>

            {scope === "tickers" ? (
              <label className="flex flex-col gap-1">
                <span className="text-data text-fg-muted">Tickers</span>
                <textarea
                  data-testid="export-tickers"
                  aria-label="Tickers to export"
                  value={tickerText}
                  onChange={(event) => setTickerText(event.target.value)}
                  rows={2}
                  placeholder="AAPL, MSFT, BRK.B"
                  className="rounded-sm border border-line-control bg-surface-sunken p-2 font-mono text-data text-fg"
                />
              </label>
            ) : null}

            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1">
                <span className="text-data text-fg-muted">Rows</span>
                <input
                  data-testid="export-rows"
                  aria-label="Rows to export"
                  type="number"
                  min={1}
                  max={MAX_EXPORT_ROWS}
                  step={1}
                  value={rowsText}
                  onChange={(event) => setRowsText(event.target.value)}
                  onBlur={() => setRowsText(String(rows))}
                  className="h-7 w-20 rounded-sm border border-line-control bg-surface-sunken px-1.5 font-mono text-data text-fg"
                />
              </label>
              <p data-testid="export-match" className="pb-1 text-data text-fg-muted">
                {matchSummary(count, rows)}
              </p>
            </div>

            {clamped ? (
              <p data-testid="export-rows-note" className="text-data text-alert">
                {EXPORT_ROW_CLAMP}
              </p>
            ) : null}

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                data-testid="export-run"
                onClick={submit}
                disabled={!canRun}
                className={cn(
                  "h-7 rounded-sm border border-brand px-2 text-ui text-fg transition-colors hover:bg-row-selected",
                  !canRun && "opacity-50",
                )}
              >
                Export {FORMAT_LABELS[format]}
              </button>

              {phase === "loading" ? (
                <span className="flex items-center gap-1.5 text-data text-fg-muted">
                  <span
                    data-testid="export-spinner"
                    aria-hidden="true"
                    className="size-3 animate-spin rounded-full border-2 border-line border-t-brand"
                  />
                  <span role="status">Writing the file…</span>
                </span>
              ) : null}
            </div>

            {error ? (
              <div
                role="alert"
                data-testid="export-error"
                className="flex items-start justify-between gap-2 rounded-sm border border-down bg-surface-raised px-2 py-1.5"
              >
                <p className="text-data text-down">{error}</p>
                {attempt ? (
                  <button
                    type="button"
                    data-testid="export-retry"
                    onClick={retry}
                    className="shrink-0 rounded-sm border border-down px-1.5 text-data text-down hover:bg-row-selected"
                  >
                    Retry
                  </button>
                ) : null}
              </div>
            ) : null}

            {success ? (
              <p
                role="status"
                data-testid="export-success"
                className="rounded-sm border border-up bg-surface-raised px-2 py-1.5 text-data text-up"
              >
                {success}
              </p>
            ) : null}

            <p className="text-data text-fg-subtle">
              The file is written by the server from the screen's own rows. A venue is
              exported as its code (<span className="font-mono">XNAS</span>), never as the
              label the table draws.
            </p>
          </>
        )}
      </div>
    </DialogShell>
  );
}
