import * as React from "react";

import { fetchHealth, type HealthReport, type HealthState } from "@/lib/api";

export interface HealthView {
  state: HealthState;
  report: HealthReport | null;
  error: string | null;
  reload: () => void;
}

/** Map the API's status word onto the four states the dot can paint. */
export function healthStateOf(status: string | undefined): HealthState {
  switch (status) {
    case "ok":
    case "degraded":
    case "failing":
      return status;
    default:
      return "unknown";
  }
}

/**
 * One readiness read per mount, with an explicit retry.
 *
 * No polling: a shell that pings /health every 30 s is a monitoring system
 * pretending to be a page, and /health is not cheap (it touches the depth
 * store and the SQLite file). The operator's real probe belongs in systemd.
 */
export function useHealth(): HealthView {
  const [report, setReport] = React.useState<HealthReport | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [nonce, setNonce] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    fetchHealth(controller.signal)
      .then((next) => {
        if (cancelled) return;
        setReport(next);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled || controller.signal.aborted) return;
        setReport(null);
        setError(cause instanceof Error ? cause.message : "Health unavailable");
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [nonce]);

  const reload = React.useCallback(() => setNonce((value) => value + 1), []);

  return {
    state: error ? "unknown" : healthStateOf(report?.status),
    report,
    error,
    reload,
  };
}
