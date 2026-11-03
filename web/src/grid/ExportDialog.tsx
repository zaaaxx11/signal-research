/**
 * Export: guided step-by-step selects that build filter criteria, plus a
 * stepper for how many rows, and one button that hands the file to the browser.
 *
 * The dialog asks for three optional dimensions — sector, industry, country —
 * each backed by the live option census at ``GET /screens/options/{key}``.
 * The options are fetched once when the dialog opens and cached for the
 * session.  Every non-empty selection ANDs an ``eq`` filter into the criteria
 * sent to ``POST /screens/export``; an "All" row removes that dimension.
 *
 * The row count is 1–200, stepped with +/- buttons so the reader cannot type a
 * non-number.  The cap is 200 and the clamp is visible — a note appears when
 * the count is at the ceiling.
 *
 * Every outcome is visible in place: a spinner while the request is open, a
 * red failure with **Retry**, and a green success naming the row count and
 * the file.
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
  buildGuidedExportBody,
  downloadFile,
  fetchOptions,
  requestExport,
  type ExportBody,
  type ExportChoice,
  type ExportFormat,
  type OptionItem,
} from "./exportJob";

export type { ExportChoice } from "./exportJob";

export interface ExportDialogProps {
  open: boolean;
  onClose: () => void;
  /** The format the toolbar's menu opened with (or `api`). */
  choice: ExportChoice;
  /** The grid's visible cells, in the reader's order. */
  columns?: readonly string[];
  region?: string;
}

type Phase = "idle" | "loading" | "error" | "done";

const FORMAT_LABELS: Record<ExportChoice, string> = {
  csv: "CSV",
  xlsx: "XLSX",
  json: "JSON",
  api: "API",
};

const OPTION_KEYS = ["sector", "industry", "country"] as const;
type OptionKey = (typeof OPTION_KEYS)[number];

const OPTION_LABELS: Record<OptionKey, string> = {
  sector: "Sector",
  industry: "Industry",
  country: "Country",
};

/** True when the choice is a file format (not the API pane). */
function isFormat(value: ExportChoice): value is ExportFormat {
  return value !== "api";
}

/** Clamp a count to 1–200, returning the clamped value and whether it moved. */
function clampCount(value: number): { count: number; clamped: boolean } {
  if (!Number.isFinite(value) || value < 1) return { count: 1, clamped: true };
  if (value > MAX_EXPORT_ROWS) return { count: MAX_EXPORT_ROWS, clamped: true };
  return { count: Math.trunc(value), clamped: false };
}

export function ExportDialog({
  open,
  onClose,
  choice,
  columns = [],
  region = "us",
}: ExportDialogProps) {
  const [format, setFormat] = React.useState<ExportChoice>(choice);
  const [sector, setSector] = React.useState("");
  const [industry, setIndustry] = React.useState("");
  const [country, setCountry] = React.useState("");
  const [count, setCount] = React.useState(DEFAULT_EXPORT_ROWS);
  const [phase, setPhase] = React.useState<Phase>("idle");
  const [error, setError] = React.useState<string | null>(null);
  const [success, setSuccess] = React.useState<string | null>(null);
  /** The body behind the last attempt, so Retry resends exactly it. */
  const [attempt, setAttempt] = React.useState<ExportBody | null>(null);

  /** Option lists, keyed by dimension. */
  const [options, setOptions] = React.useState<
    Record<OptionKey, OptionItem[] | null>
  >({ sector: null, industry: null, country: null });
  /** Non-null when fetching options failed. */
  const [optionsError, setOptionsError] = React.useState<string | null>(null);

  // ------------------------------------------------------------------
  // Fetch option lists once the dialog opens.
  // ------------------------------------------------------------------
  React.useEffect(() => {
    if (!open) return;
    let cancelled = false;

    async function load() {
      setOptionsError(null);
      const result: Record<OptionKey, OptionItem[] | null> = {
        sector: null,
        industry: null,
        country: null,
      };
      for (const key of OPTION_KEYS) {
        try {
          result[key] = await fetchOptions(key);
        } catch (cause) {
          if (!cancelled) {
            setOptionsError(
              `Could not load ${OPTION_LABELS[key]} options: ${cause instanceof Error ? cause.message : "unknown error"}`,
            );
          }
          return;
        }
      }
      if (!cancelled) setOptions(result);
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [open]);

  // ------------------------------------------------------------------
  // Reset local state every time the dialog opens.
  // ------------------------------------------------------------------
  React.useEffect(() => {
    if (!open) return;
    setFormat(choice);
    setSector("");
    setIndustry("");
    setCountry("");
    setCount(DEFAULT_EXPORT_ROWS);
    setPhase("idle");
    setError(null);
    setSuccess(null);
    setAttempt(null);
  }, [open, choice]);

  const clamped = clampCount(count);

  const canExport =
    isFormat(format) &&
    phase !== "loading" &&
    options.sector !== null; // options must be loaded

  // ------------------------------------------------------------------
  // Run
  // ------------------------------------------------------------------
  const run = React.useCallback(
    async (body: ExportBody) => {
      setAttempt(body);
      setPhase("loading");
      setError(null);
      setSuccess(null);
      try {
        const file = await requestExport(body);
        downloadFile(file);
        setPhase("done");
        setSuccess(
          `Exported ${file.rowCount.toLocaleString("en-US")} ${
            file.rowCount === 1 ? "row" : "rows"
          } to ${file.filename}.`,
        );
      } catch (cause) {
        setPhase("error");
        setError(
          cause instanceof Error ? cause.message : "The export could not be written",
        );
      }
    },
    [],
  );

  const submit = () => {
    if (!isFormat(format)) return;
    const body = buildGuidedExportBody({
      sector,
      industry,
      country,
      count: clamped.count,
      format,
      region,
      columns,
    });
    void run(body);
  };

  const retry = () => {
    if (attempt) void run(attempt);
  };

  // ------------------------------------------------------------------
  // Number stepper helpers
  // ------------------------------------------------------------------
  const stepUp = () =>
    setCount((prev) => clampCount(prev + 1).count);
  const stepDown = () =>
    setCount((prev) => clampCount(prev - 1).count);

  return (
    <DialogShell
      open={open}
      onClose={onClose}
      title={
        format === "api" ? "Export API" : `Export ${FORMAT_LABELS[format]}`
      }
      description="Pick the dimensions, choose how many rows, and the server writes the file."
      size="md"
    >
      <div className="flex flex-col gap-3">
        {/* Format picker */}
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
          /* API pane — identical to the old dialog */
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
            {/* Options error banner */}
            {optionsError ? (
              <div
                role="alert"
                data-testid="export-options-error"
                className="rounded-sm border border-down bg-surface-raised px-2 py-1.5 text-data text-down"
              >
                {optionsError}
              </div>
            ) : null}

            {/* Step-by-step selects */}
            {OPTION_KEYS.map((key) => {
              const items = options[key];
              const loading = items === null && !optionsError;
              const value =
                key === "sector"
                  ? sector
                  : key === "industry"
                    ? industry
                    : country;
              const setter =
                key === "sector"
                  ? setSector
                  : key === "industry"
                    ? setIndustry
                    : setCountry;

              return (
                <label
                  key={key}
                  className="flex flex-col gap-1"
                >
                  <span className="text-data text-fg-muted">
                    {OPTION_LABELS[key]}
                  </span>
                  <select
                    data-testid={`export-select-${key}`}
                    aria-label={OPTION_LABELS[key]}
                    value={value}
                    disabled={loading}
                    onChange={(event) => {
                      setter(event.target.value);
                      setPhase("idle");
                      setError(null);
                      setSuccess(null);
                    }}
                    className={cn(
                      "h-7 rounded-sm border border-line-control bg-surface-sunken px-1.5 text-data text-fg",
                      loading && "opacity-50",
                    )}
                  >
                    <option value="">
                      All {OPTION_LABELS[key]}s
                    </option>
                    {items?.map((item) => (
                      <option key={item.id} value={item.predicate}>
                        {item.label} ({item.count.toLocaleString("en-US")})
                      </option>
                    ))}
                  </select>
                </label>
              );
            })}

            {/* Number stepper: +/- buttons, NO text input */}
            <div className="flex flex-col gap-1">
              <span className="text-data text-fg-muted">Rows</span>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  data-testid="export-count-down"
                  aria-label="Fewer rows"
                  disabled={count <= 1}
                  onClick={stepDown}
                  className={cn(
                    "h-7 w-7 rounded-sm border border-line-control text-ui transition-colors hover:border-brand hover:text-fg",
                    count <= 1 && "opacity-40",
                  )}
                >
                  −
                </button>
                <span
                  data-testid="export-count-value"
                  aria-live="polite"
                  aria-label={`${clamped.count} rows`}
                  className="inline-flex h-7 min-w-[3ch] items-center justify-center rounded-sm border border-line-control bg-surface-sunken px-2 font-mono text-data text-fg"
                >
                  {clamped.count}
                </span>
                <button
                  type="button"
                  data-testid="export-count-up"
                  aria-label="More rows"
                  disabled={count >= MAX_EXPORT_ROWS}
                  onClick={stepUp}
                  className={cn(
                    "h-7 w-7 rounded-sm border border-line-control text-ui transition-colors hover:border-brand hover:text-fg",
                    count >= MAX_EXPORT_ROWS && "opacity-40",
                  )}
                >
                  +
                </button>
              </div>
              {clamped.clamped ? (
                <p data-testid="export-rows-note" className="text-data text-alert">
                  {EXPORT_ROW_CLAMP}
                </p>
              ) : null}
            </div>

            {/* Action row */}
            <div className="flex flex-wrap items-center gap-2">
              {/* Primary: Export button that follows the format choice */}
              <button
                type="button"
                data-testid="export-run"
                onClick={submit}
                disabled={!canExport}
                className={cn(
                  "h-7 rounded-sm border border-brand px-2 text-ui text-fg transition-colors hover:bg-row-selected",
                  !canExport && "opacity-50",
                )}
              >
                Export {FORMAT_LABELS[format]}
              </button>

              {/* Quick-format: Export CSV button */}
              {format !== "csv" ? (
                <button
                  type="button"
                  data-testid="export-quick-csv"
                  onClick={() => {
                    setFormat("csv");
                    // Submit on next tick so setFormat settles
                    window.setTimeout(() => {
                      const body = buildGuidedExportBody({
                        sector,
                        industry,
                        country,
                        count: clamped.count,
                        format: "csv",
                        region,
                        columns,
                      });
                      void run(body);
                    }, 0);
                  }}
                  disabled={!canExport}
                  className={cn(
                    "h-7 rounded-sm border border-line-control px-2 text-ui text-fg-muted transition-colors hover:border-brand hover:text-fg",
                    !canExport && "opacity-50",
                  )}
                >
                  Export CSV
                </button>
              ) : null}

              {/* Quick-format: Export JSON button */}
              {format !== "json" ? (
                <button
                  type="button"
                  data-testid="export-quick-json"
                  onClick={() => {
                    setFormat("json");
                    window.setTimeout(() => {
                      const body = buildGuidedExportBody({
                        sector,
                        industry,
                        country,
                        count: clamped.count,
                        format: "json",
                        region,
                        columns,
                      });
                      void run(body);
                    }, 0);
                  }}
                  disabled={!canExport}
                  className={cn(
                    "h-7 rounded-sm border border-line-control px-2 text-ui text-fg-muted transition-colors hover:border-brand hover:text-fg",
                    !canExport && "opacity-50",
                  )}
                >
                  Export JSON
                </button>
              ) : null}

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

            {/* Error */}
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

            {/* Success */}
            {success ? (
              <p
                role="status"
                data-testid="export-success"
                className="rounded-sm border border-up bg-surface-raised px-2 py-1.5 text-data text-up"
              >
                {success}
              </p>
            ) : null}
          </>
        )}
      </div>
    </DialogShell>
  );
}