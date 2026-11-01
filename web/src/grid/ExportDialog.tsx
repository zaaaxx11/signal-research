/**
 * Export, as the API does it.
 *
 * The menu offers CSV, XLSX, JSON and API. The three files are **server jobs**:
 * this dialog looks up the saved screen for the current market and ordering,
 * enqueues an export, polls the request until it says Success, and then hands
 * the reader the artifact link the job published. Nothing is serialised in the
 * browser — see `exportJob.ts` for why that boundary is deliberate.
 *
 * The API choice shows the same request the grid sends, with a copy button, so
 * a reader who wants the data live rather than as a file has the exact call.
 */

import * as React from "react";

import { postJsonConditional } from "@/lib/api";
import { cn } from "@/lib/utils";

import { DialogShell } from "./DialogShell";
import {
  EXPORT_FORMATS,
  fileHref,
  findScreenId,
  isReady,
  isSettled,
  readExportJob,
  startExport,
  type ExportFormat,
  type ExportJob,
} from "./exportJob";

export type ExportChoice = ExportFormat | "api";

/** How often the dialog asks a running job for its state. */
export const EXPORT_POLL_MS = 1_200;

export interface ExportDialogProps {
  open: boolean;
  onClose: () => void;
  choice: ExportChoice;
  region: string;
  sort: string;
  /** The wire body a copy of the API call carries. */
  apiBody: unknown;
  /** What the export covers, in the reader's words. */
  scopeNote: string;
}

export function ExportDialog({
  open,
  onClose,
  choice,
  region,
  sort,
  apiBody,
  scopeNote,
}: ExportDialogProps) {
  const [job, setJob] = React.useState<ExportJob | null>(null);
  const [phase, setPhase] = React.useState<"idle" | "working" | "ready" | "error">("idle");
  const [error, setError] = React.useState<string | null>(null);
  const [copied, setCopied] = React.useState(false);

  React.useEffect(() => {
    if (!open) {
      setJob(null);
      setPhase("idle");
      setError(null);
      setCopied(false);
    }
  }, [open]);

  const apiText = React.useMemo(
    () => `POST /screens/select\ncontent-type: application/json\n\n${JSON.stringify(apiBody, null, 2)}`,
    [apiBody],
  );

  /** Enqueue, then poll until the job settles or the dialog closes. */
  const start = React.useCallback(
    async (format: ExportFormat) => {
      setPhase("working");
      setError(null);
      try {
        const screenId = await findScreenId(region, sort);
        const started = await startExport(screenId, format);
        setJob(started);
      } catch (cause) {
        setPhase("error");
        setError(cause instanceof Error ? cause.message : "The export could not be started");
      }
    },
    [region, sort],
  );

  React.useEffect(() => {
    if (!open || job === null || isSettled(job)) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      readExportJob(job.id)
        .then((next) => {
          if (!alive) return;
          setJob(next);
          if (isReady(next)) setPhase("ready");
          else if (isSettled(next)) {
            setPhase("error");
            setError(next.error ?? "The export job failed");
          }
        })
        .catch((cause: unknown) => {
          if (!alive) return;
          setPhase("error");
          setError(cause instanceof Error ? cause.message : "The export job could not be read");
        });
    }, EXPORT_POLL_MS);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [open, job]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(apiText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  const format = choice === "api" ? null : choice;
  const href = job && format ? fileHref(job, format) : null;

  return (
    <DialogShell
      open={open}
      onClose={onClose}
      title={choice === "api" ? "API request" : `Export ${choice.toUpperCase()}`}
      description={choice === "api" ? "The exact call this screen makes." : scopeNote}
    >
      {choice === "api" ? (
        <div className="flex flex-col gap-2">
          <pre
            data-testid="export-api-body"
            className="max-h-[320px] overflow-auto rounded-sm border border-line bg-surface-sunken p-2 font-mono text-data text-fg-muted"
          >
            {apiText}
          </pre>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void copy()}
              className="h-7 rounded-sm border border-line-control px-2 text-ui text-fg transition-colors hover:border-brand"
            >
              {copied ? "Copied" : "Copy"}
            </button>
            <span aria-live="polite" className="text-data text-fg-subtle">
              {copied ? "The request is on the clipboard." : ""}
            </span>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="text-data text-fg-muted">
            {phase === "idle"
              ? `The server writes the ${choice.toUpperCase()} file from the saved screen for this market and ordering.`
              : phase === "working"
                ? "Writing the file on the server…"
                : phase === "ready"
                  ? "The file is ready."
                  : "The export did not finish."}
          </p>

          {error ? (
            <p role="alert" className="text-data text-alert">
              {error}
            </p>
          ) : null}

          {job ? (
            <p className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
              job {job.id} · {job.status}
            </p>
          ) : null}

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => format && void start(format)}
              disabled={phase === "working"}
              className={cn(
                "h-7 rounded-sm border border-line-control px-2 text-ui text-fg transition-colors hover:border-brand",
                phase === "working" && "opacity-50",
              )}
            >
              {phase === "idle" ? "Prepare file" : "Prepare again"}
            </button>

            {href ? (
              <a
                href={href}
                data-testid="export-link"
                className="h-7 rounded-sm border border-brand px-2 leading-7 text-ui text-fg transition-colors hover:bg-row-selected"
              >
                Get the {format?.toUpperCase()} file
              </a>
            ) : null}

            <select
              aria-label="Export format"
              value={format ?? "csv"}
              onChange={(event) => {
                setJob(null);
                setPhase("idle");
                setError(null);
                void start(event.target.value as ExportFormat);
              }}
              className="h-7 rounded-sm border border-line-control bg-surface-sunken px-1.5 text-data text-fg"
            >
              {EXPORT_FORMATS.map((option) => (
                <option key={option} value={option}>
                  {option.toUpperCase()}
                </option>
              ))}
            </select>
          </div>

          <p className="text-data text-fg-subtle">
            A screen export is the server's snapshot for this market and ordering; the grid's
            filters are not part of the file.
          </p>
        </div>
      )}
    </DialogShell>
  );
}

/** One POST, for the API pane's "send it now" affordance. */
export async function sendSelection(body: unknown): Promise<unknown> {
  const answer = await postJsonConditional("/screens/select", body);
  return answer.body;
}
