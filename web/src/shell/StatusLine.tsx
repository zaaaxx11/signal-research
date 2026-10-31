import { cn } from "@/lib/utils";

import type { HealthView } from "./useHealth";

export interface StatusLineProps {
  viewLabel: string;
  symbol: string | null;
  health: HealthView;
}

const LABELS: Record<string, string> = {
  ok: "OK",
  degraded: "DEGRADED",
  failing: "FAILING",
  unknown: "OFFLINE",
};

const TONE: Record<string, string> = {
  ok: "bg-up",
  degraded: "bg-alert",
  failing: "bg-down",
  unknown: "bg-fg-disabled",
};

/** "2026-10-30T16:13:15+00:00" -> "16:13:15Z"; anything else is passed through. */
export function clockOf(iso: string): string {
  const match = /T(\d{2}:\d{2}:\d{2})/.exec(iso);
  return match?.[1] ? `${match[1]}Z` : "—";
}

const WATCHED = ["depth_store", "snapshot", "screen_db"] as const;

/**
 * Statuses that are not a problem: a disabled collector and an unconfigured
 * snapshot are deployment choices, not failures. Painting them amber would
 * train the operator to ignore amber — the actual failure modes are `stale`,
 * `missing`, `unreadable`, `corrupt` and `invalid`.
 */
const NEUTRAL_STATUSES = new Set([
  "ok",
  "disabled",
  "not_configured",
  "not_applicable",
]);

function humanize(token: string): string {
  return token.replaceAll("_", " ");
}

/**
 * Breadcrumb + the one live fact in the shell: whether the deployment behind
 * this page is healthy. The values come from GET /health, not from a guess.
 */
export function StatusLine({ viewLabel, symbol, health }: StatusLineProps) {
  const { state, report, error } = health;
  const checked = report ? clockOf(report.checked_at) : "—";

  return (
    <div
      data-health-status={state}
      className="flex h-[28px] items-center justify-between gap-4 border-b border-line bg-surface-sunken px-3 text-label uppercase tracking-[0.1em] text-fg-subtle"
    >
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5">
        <span className="text-fg-muted">Screener</span>
        <span aria-hidden="true" className="text-fg-disabled">
          /
        </span>
        <span className="text-fg">{viewLabel}</span>
        {symbol ? (
          <>
            <span aria-hidden="true" className="text-fg-disabled">
              ·
            </span>
            <span className="rounded-sm border border-line px-1.5 font-mono tracking-[0.08em] text-brand">
              {symbol}
            </span>
          </>
        ) : null}
      </nav>

      <div className="flex shrink-0 items-center gap-3 font-mono tracking-[0.06em]">
        {WATCHED.map((name) => {
          // HealthCheck.status is a free-form word ("ok", "stale", "missing"…).
          const check = report?.checks[name];
          if (!check) return null;
          const neutral = NEUTRAL_STATUSES.has(check.status);
          return (
            <span
              key={name}
              className={cn(
                "hidden items-center gap-1.5 sm:flex",
                neutral ? "text-fg-subtle" : "text-alert",
              )}
              title={`${name}: ${check.status}`}
            >
              <span
                aria-hidden="true"
                className={cn(
                  "size-1.5 rounded-full",
                  check.status === "ok"
                    ? "bg-up/70"
                    : neutral
                      ? "bg-fg-disabled"
                      : "bg-alert",
                )}
              />
              {humanize(name)} {humanize(check.status)}
            </span>
          );
        })}

        <span
          className="flex items-center gap-1.5"
          title={error ?? `checked ${checked}`}
        >
          <span
            aria-hidden="true"
            className={cn("size-1.5 rounded-full", TONE[state] ?? "bg-fg-disabled")}
          />
          <span className={state === "ok" ? "text-fg-muted" : "text-alert"}>
            {LABELS[state] ?? "UNKNOWN"}
          </span>
        </span>

        <span className="text-fg-subtle">v{report?.version ?? "—"}</span>
        <span className="hidden text-fg-subtle md:inline">{checked}</span>
      </div>
    </div>
  );
}
