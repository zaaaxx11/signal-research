/**
 * Exports are the server's, not the browser's.
 *
 * The product has one export path and it is a job: a screen's rows are written
 * by `app/export/writer.py` from the canonical column list, and the file is
 * served back by `GET /requests/{id}/export/{kind}`. The client never builds a
 * file, never serialises the DOM and never creates a Blob — that invariant is
 * tested in `export.test.ts`, and it exists because a client-side CSV would
 * publish the *labels* the grid draws instead of the codes the data carries.
 *
 * So this module does the three hops the API asks for:
 *
 *   GET  /screens?region&sort          the saved screen for this market
 *   POST /screens/{id}/export          enqueue a job (202 + job id)
 *   GET  /requests/{id}                poll until it says Success, then read
 *                                      the artifact href it publishes
 */

import { getJson, postJsonConditional, readErrorMessage } from "@/lib/api";

/** The three file formats the writer implements, in the menu's order. */
export const EXPORT_FORMATS = ["csv", "xlsx", "json"] as const;
export type ExportFormat = (typeof EXPORT_FORMATS)[number];

export interface ExportFile {
  /** The artifact's kind (`all_csv` for a screen bundle). */
  kind: string;
  href: string;
}

export interface ExportJob {
  id: string;
  status: string;
  /** The files the job has published so far, by kind. */
  files: Record<string, string>;
  error: string | null;
}

interface ScreenDocument {
  screen_id?: unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * The saved screen for a market and an ordering.
 *
 * `GET /screens` is the browse snapshot route; it answers with the screen id
 * the export route takes. One id per (region, sort), replaced as the snapshot
 * is refreshed — which is why it is looked up per export rather than cached.
 */
export async function findScreenId(
  region: string,
  sort: string,
  signal?: AbortSignal,
): Promise<string> {
  const params = new URLSearchParams({ region, sort, limit: "1" });
  const body = await getJson(`/screens?${params.toString()}`, signal);
  if (!isRecord(body) || typeof (body as ScreenDocument).screen_id !== "string") {
    throw new Error("The screen route answered without a screen id");
  }
  return (body as ScreenDocument).screen_id as string;
}

/** Enqueue one export job and return its id. */
export async function startExport(
  screenId: string,
  format: ExportFormat,
  signal?: AbortSignal,
): Promise<ExportJob> {
  const answer = await postJsonConditional(
    `/screens/${encodeURIComponent(screenId)}/export`,
    { formats: [format] },
    { signal },
  );
  if (answer.status < 200 || answer.status >= 300) {
    throw new Error(readErrorMessage(answer.body, answer.status));
  }
  if (!isRecord(answer.body) || typeof answer.body["job_id"] !== "string") {
    throw new Error("The export route answered without a job id");
  }
  return {
    id: answer.body["job_id"],
    status: typeof answer.body["status"] === "string" ? answer.body["status"] : "Pending",
    files: {},
    error: null,
  };
}

/** Read a job's state; the `files` map is the artifact hrefs it has published. */
export async function readExportJob(id: string, signal?: AbortSignal): Promise<ExportJob> {
  const body = await getJson(`/requests/${encodeURIComponent(id)}`, signal);
  if (!isRecord(body)) throw new Error("The request route answered with a non-object");
  const files: Record<string, string> = {};
  if (isRecord(body["files"])) {
    for (const [kind, href] of Object.entries(body["files"])) {
      if (typeof href === "string") files[kind] = href;
    }
  }
  const status = typeof body["status"] === "string" ? body["status"] : "Pending";
  const error =
    typeof body["error"] === "string"
      ? body["error"]
      : typeof body["errorMessage"] === "string"
        ? body["errorMessage"]
        : null;
  return { id, status, files, error };
}

/** The link for one format, when the job published it (`all_csv` first). */
export function fileHref(job: ExportJob, format: ExportFormat): string | null {
  return job.files[`all_${format}`] ?? job.files[format] ?? null;
}

export function isSettled(job: ExportJob): boolean {
  const status = job.status.toLowerCase();
  return status === "success" || status === "failure" || status === "failed" || status === "cancelled";
}

export function isReady(job: ExportJob): boolean {
  return job.status.toLowerCase() === "success";
}
