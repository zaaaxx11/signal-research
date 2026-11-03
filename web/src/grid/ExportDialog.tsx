/**
 * Export: guided selects that build filter criteria, a stepper for how many
 * rows, and one button that hands the file to the browser.
 *
 * The dialog asks for optional dimensions — sector, industry, country,
 * order-by, signal — each a labelled select backed by live option lists from
 * ``GET /screens/options/{key}`` or the screener's own filter document.
 * Options are fetched once when the dialog opens.
 *
 * Every non-empty selection ANDs an ``eq`` filter into the criteria sent to
 * ``POST /screens/export``; an "All" row removes that dimension.  The row
 * count is 1–200, stepped with +/- buttons — no text input.
 *
 * The file is always CSV.  No format picker, no scope, no ticker list.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

import { DialogShell } from "./DialogShell";
import {
  DEFAULT_EXPORT_ROWS,
  EXPORT_ROW_CLAMP,
  MAX_EXPORT_ROWS,
  buildGuidedExportBody,
  downloadFile,
  fetchOptions,
  requestExport,
  type ExportBody,
  type ExportFormat,
  type OptionItem,
} from "./exportJob";

export interface ExportDialogProps {
  open: boolean;
  onClose: () => void;
  /** The grid's visible cells, in the reader's order. */
  columns?: readonly string[];
  region?: string;
}

type Phase = "idle" | "loading" | "error" | "done";

/** The dimensions the reader may filter by.  Order matches the panel. */
const OPTION_KEYS = [
  "sector",
  "industry",
  "country",
  "order",
  "signal",
] as const;
type OptionKey = (typeof OPTION_KEYS)[number];

const OPTION_LABELS: Record<OptionKey, string> = {
  sector: "Sector",
  industry: "Industry",
  country: "Country",
  order: "Order by",
  signal: "Signal",
};

/** Clamp a count to 1–200, returning the clamped value and whether it moved. */
function clampCount(value: number): { count: number; clamped: boolean } {
  if (!Number.isFinite(value) || value < 1) return { count: 1, clamped: true };
  if (value > MAX_EXPORT_ROWS) return { count: MAX_EXPORT_ROWS, clamped: true };
  return { count: Math.trunc(value), clamped: false };
}

export function ExportDialog({
  open,
  onClose,
  columns = [],
  region = "us",
}: ExportDialogProps) {
  const [sector, setSector] = React.useState("");
  const [industry, setIndustry] = React.useState("");
  const [country, setCountry] = React.useState("");
  const [order, setOrder] = React.useState("");
  const [signal, setSignal] = React.useState("");
  const [format, setFormat] = React.useState<ExportFormat>("csv");
  const [count, setCount] = React.useState(DEFAULT_EXPORT_ROWS);
  const [phase, setPhase] = React.useState<Phase>("idle");
  const [error, setError] = React.useState<string | null>(null);
  const [success, setSuccess] = React.useState<string | null>(null);
  /** The body behind the last attempt, so Retry resends exactly it. */
  const [attempt, setAttempt] = React.useState<ExportBody | null>(null);

  /** Option lists, keyed by dimension. */
  const [options, setOptions] = React.useState<
    Record<OptionKey, OptionItem[] | null>
  >({ sector: null, industry: null, country: null, order: null, signal: null });
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
        order: null,
        signal: null,
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
    setSector("");
    setIndustry("");
    setCountry("");
    setOrder("");
    setSignal("");
    setFormat("csv");
    setCount(DEFAULT_EXPORT_ROWS);
    setPhase("idle");
    setError(null);
    setSuccess(null);
    setAttempt(null);
  }, [open]);

  const clamped = clampCount(count);

  const canExport =
    phase !== "loading" && options.sector !== null; // options must be loaded

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
    const body = buildGuidedExportBody({
      sector,
      industry,
      country,
      order,
      signal,
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
  const stepUp = () => setCount((prev) => clampCount(prev + 1).count);
  const stepDown = () => setCount((prev) => clampCount(prev - 1).count);

  /** The value and setter for one dimension. */
  function binding(key: OptionKey) {
    const value =
      key === "sector" ? sector
      : key === "industry" ? industry
      : key === "country" ? country
      : key === "order" ? order
      : signal;
    const setter =
      key === "sector" ? setSector
      : key === "industry" ? setIndustry
      : key === "country" ? setCountry
      : key === "order" ? setOrder
      : setSignal;
    return { value, setter };
  }

  return (
    <DialogShell
      open={open}
      onClose={onClose}
      title="Export"
      description="Pick the dimensions, choose how many rows, and the server writes the file."
      size="md"
    >
      <div className="flex flex-col gap-3">
        {/* Format selector */}
        <label className="flex flex-col gap-1">
          <span className="text-data text-fg-muted">Format</span>
          <select
            data-testid="export-select-format"
            aria-label="File format"
            value={format}
            onChange={(event) => setFormat(event.target.value as ExportFormat)}
            className="h-7 rounded-sm border border-line-control bg-surface-sunken px-1.5 text-data text-fg"
          >
            <option value="csv">CSV</option>
            <option value="xlsx">XLSX</option>
            <option value="json">JSON</option>
          </select>
        </label>

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
          const { value, setter } = binding(key);

          return (
            <label key={key} className="flex flex-col gap-1">
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
                    {item.label}
                    {item.count != null
                      ? ` (${item.count.toLocaleString("en-US")})`
                      : ""}
                  </option>
                ))}
              </select>
            </label>
          );
        })}

        {/* Number input with +/- stepper */}
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
            <input
              type="number"
              data-testid="export-count-value"
              aria-label={`${clamped.count} rows`}
              min={1}
              max={MAX_EXPORT_ROWS}
              value={count}
              onChange={(event) => {
                const parsed = Number.parseInt(event.target.value, 10);
                if (Number.isNaN(parsed)) return;
                setCount(clampCount(parsed).count);
              }}
              className="h-7 w-[5ch] rounded-sm border border-line-control bg-surface-sunken px-1.5 font-mono text-data text-fg [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
            />
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
            Export
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
      </div>
    </DialogShell>
  );
}