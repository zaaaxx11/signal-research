/**
 * Fundamentals: all 43 catalogue rows, grouped, each with its unit.
 *
 * The endpoint's payload is consulted, not trusted for shape: a key it omits
 * still draws a row with the em dash, because "ASML reported three fields" and
 * "ASML has no assets" are different sentences and only the first is true. The
 * group is editorial (the API carries no category); the values are exactly what
 * the API sent, formatted by unit.
 */

import * as React from "react";

import type { Resource } from "./useDetail";
import { formatDate } from "./format";
import { groupMetricRows, metricCell, METRIC_GROUP_LABELS } from "./metrics";
import { MetricRowView, TabEmpty, TabError, TabSkeleton } from "./TabStates";

import type { FundamentalsData } from "./useDetail";

export interface FundamentalsTabProps {
  symbol: string;
  state: Resource<FundamentalsData>;
  onRetry: () => void;
}

export function FundamentalsTab({
  symbol,
  state,
  onRetry,
}: FundamentalsTabProps): React.ReactElement {
  const baseId = React.useId();

  const sections = React.useMemo(
    () => (state.data ? groupMetricRows(state.data.metrics) : []),
    [state.data],
  );

  if (state.status === "loading") {
    return <TabSkeleton label={`fundamentals for ${symbol}`} testId="fundamentals-loading" rows={10} />;
  }

  if (state.status === "error") {
    return (
      <TabError
        label="fundamentals"
        testId="fundamentals-error"
        message={state.error}
        onRetry={onRetry}
      />
    );
  }

  if (state.status === "empty" || !state.data) {
    return (
      <TabEmpty testId="fundamentals-empty">
        No fundamentals reported for {symbol}.
      </TabEmpty>
    );
  }

  const rows = sections.flatMap((section) => section.rows);
  const reported = rows.filter((row) => !metricCell(row.value, row.unit).absent).length;
  const facts = [`${rows.length} fields`, `${reported} reported`];
  if (state.data.asOf) facts.push(`as of ${formatDate(state.data.asOf)}`);

  return (
    <div data-testid="fundamentals-loaded" className="flex flex-col gap-3">
      <p className="nums text-label uppercase tracking-[0.08em] text-fg-subtle">
        {facts.join(" · ")}
      </p>

      {sections.map((section) => {
        const headingId = `${baseId}-${section.group}`;
        return (
          <section key={section.group} aria-labelledby={headingId}>
            <h3
              id={headingId}
              className="border-b border-line pb-1 text-label uppercase tracking-[0.08em] text-fg-subtle"
            >
              {METRIC_GROUP_LABELS[section.group]}
            </h3>
            <dl className="mt-0.5">
              {section.rows.map((row) => (
                <MetricRowView
                  key={row.key}
                  label={row.label}
                  cell={metricCell(row.value, row.unit)}
                  testId={`metric-${row.key}`}
                />
              ))}
            </dl>
          </section>
        );
      })}
    </div>
  );
}
