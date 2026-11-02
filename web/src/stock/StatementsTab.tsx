/**
 * The Statements tab: one read, then three tables.
 *
 * `GET /engine/statements/{symbol}` is asked for only while this tab is
 * mounted, so opening a ticker never pays for a statement collector it may not
 * read. The document carries annual and quarterly columns together; the
 * table's toggle filters them. The four answers are kept distinct:
 *
 *   * **404** — the API says why (unknown symbol, no filings). That sentence
 *     is printed in place of the table, never an empty frame.
 *   * **200 with no periods** — the collector's own sentence (`no CIK`, or no
 *     annual facts). Same rule: the reason, not a zero-row table.
 *   * **transport/5xx** — a retry, inside this tab only.
 *   * **ready** — `StatementsTable`, three panels, em dashes for gaps.
 */

import * as React from "react";

import { ApiError, getJson } from "@/lib/api";

import {
  parseStatements,
  statementsReason,
  type StatementMode,
  type StatementsDocument,
} from "./statements";
import { StatementsTable } from "./StatementsTable";

export function statementsPath(symbol: string): string {
  return `/engine/statements/${encodeURIComponent(symbol)}`;
}

export interface StatementsTabProps {
  readonly symbol: string;
}

type State =
  | { status: "loading"; document: null; reason: null; error: null; statusCode: null }
  | { status: "ready"; document: StatementsDocument; reason: null; error: null; statusCode: 200 }
  | {
      status: "empty";
      document: StatementsDocument | null;
      reason: string | null;
      error: null;
      statusCode: number | null;
    }
  | { status: "error"; document: null; reason: null; error: string; statusCode: number | null };

const LOADING: State = {
  status: "loading",
  document: null,
  reason: null,
  error: null,
  statusCode: null,
};

export function StatementsTab({ symbol }: StatementsTabProps): React.ReactElement {
  const [state, setState] = React.useState<State>(LOADING);
  const [mode, setMode] = React.useState<StatementMode>("annual");
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setState(LOADING);

    getJson(statementsPath(symbol), controller.signal)
      .then((body) => {
        if (!live) return;
        const document = parseStatements(body);
        if (document === null) {
          setState({
            status: "error",
            document: null,
            reason: null,
            error: "The statements document could not be read.",
            statusCode: 200,
          });
          return;
        }
        setState(
          document.periods.length === 0
            ? {
                status: "empty",
                document,
                reason: statementsReason(document),
                error: null,
                statusCode: 200,
              }
            : { status: "ready", document, reason: null, error: null, statusCode: 200 },
        );
      })
      .catch((cause: unknown) => {
        if (!live || controller.signal.aborted) return;
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        const statusCode = cause instanceof ApiError ? cause.status : null;
        const message =
          cause instanceof Error && cause.message.length > 0
            ? cause.message
            : "The statements request failed.";
        if (statusCode === 404) {
          // The 404's envelope message is the reason — keep it, do not replace
          // it with "not found".
          setState({
            status: "empty",
            document: null,
            reason: message,
            error: null,
            statusCode,
          });
          return;
        }
        setState({ status: "error", document: null, reason: null, error: message, statusCode });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [symbol, attempt]);

  const retry = React.useCallback(() => setAttempt((value) => value + 1), []);

  if (state.status === "loading") {
    return (
      <div aria-busy="true" data-testid="statements-loading" className="flex flex-col gap-1">
        <span className="sr-only">Loading statements for {symbol}…</span>
        {Array.from({ length: 12 }, (_, index) => (
          <div key={index} className="h-5 border-b border-line/60 last:border-b-0" />
        ))}
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div
        role="alert"
        data-testid="statements-error"
        className="rounded-sm border border-line border-l-2 border-l-alert bg-surface-sunken p-2"
      >
        <p className="text-data font-medium text-fg">Could not load statements.</p>
        <p className="mt-1 break-words text-data text-fg-muted">{state.error}</p>
        <button
          type="button"
          onClick={retry}
          className="focus-ring mt-2 h-6 rounded-sm border border-line-control px-2 text-data text-fg-muted hover:text-fg"
        >
          Retry
        </button>
      </div>
    );
  }

  if (state.status === "empty") {
    return (
      <div
        data-testid={state.statusCode === 404 ? "statements-404" : "statements-empty"}
        className="rounded-sm border border-line bg-surface-sunken px-3 py-2"
      >
        <p className="text-data text-fg-muted">
          {state.reason ?? `No statements are published for ${symbol}.`}
        </p>
        {state.statusCode === 404 ? (
          <p className="mt-1 text-label text-fg-subtle">
            Nothing is drawn: a statement table with no periods would claim the
            company reports nothing.
          </p>
        ) : null}
      </div>
    );
  }

  return <StatementsTable document={state.document} mode={mode} onModeChange={setMode} />;
}
