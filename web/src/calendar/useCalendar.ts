/**
 * Calendar's one read of `GET /market/calendar`.
 *
 * One request for the whole window. The hook owns three things the page must
 * not guess at: whether the answer has landed, whether it failed, and how to
 * ask again. Parsing — including the "no past days" rule — happens on arrival,
 * against the day the page considers today.
 */

import * as React from "react";

import { ApiError, getJson } from "@/lib/api";

import {
  CALENDAR_DAYS,
  CALENDAR_REGION,
  CalendarMalformed,
  calendarPath,
  readCalendar,
  todayUtc,
  type CalendarKind,
  type CalendarReport,
} from "./calendar";

export type CalendarStatus = "loading" | "ready" | "unavailable" | "error";

export interface CalendarView {
  readonly status: CalendarStatus;
  readonly report: CalendarReport | null;
  readonly error: string | null;
  readonly retry: () => void;
}

export interface CalendarQuery {
  readonly region?: string;
  readonly days?: number;
  /** Today, as `YYYY-MM-DD`; omitted in the app, pinned by tests and previews. */
  readonly today?: string;
  readonly kind?: CalendarKind;
}

export function useCalendar(query: CalendarQuery = {}): CalendarView {
  const region = query.region ?? CALENDAR_REGION;
  const days = query.days ?? CALENDAR_DAYS;
  const kind = query.kind ?? "earnings";
  const today = query.today;
  const path = React.useMemo(() => calendarPath(region, days, kind), [region, days, kind]);

  const [state, setState] = React.useState<Omit<CalendarView, "retry">>({
    status: "loading",
    report: null,
    error: null,
  });
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setState((previous) => ({ ...previous, status: "loading", error: null }));

    getJson(path, controller.signal)
      .then((body) => {
        if (!live) return;
        const report = readCalendar(body, { today: today ?? todayUtc() });
        setState({
          status: report.available ? "ready" : "unavailable",
          report,
          error: null,
        });
      })
      .catch((cause: unknown) => {
        if (!live || controller.signal.aborted) return;
        const status = cause instanceof ApiError ? cause.status : 0;
        setState({
          status: "error",
          report: null,
          error:
            cause instanceof CalendarMalformed
              ? "The calendar payload could not be read."
              : status === 404
                ? "This deployment does not serve an earnings calendar yet."
                : cause instanceof Error
                  ? cause.message
                  : "Request failed",
        });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [path, attempt, today]);

  const retry = React.useCallback(() => setAttempt((value) => value + 1), []);
  return { ...state, retry };
}
