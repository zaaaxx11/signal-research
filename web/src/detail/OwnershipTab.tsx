/**
 * Ownership: the cells a 100-row grid cannot afford to show.
 *
 * These six values come from `POST /screens/select` in symbols mode — the same
 * row the grid paints, read for one ticker — because the fundamentals endpoint
 * owns the statements and the facts store owns ownership. A cell the store does
 * not hold is `—`, and a snapshot that no longer exists is an empty state.
 */

import * as React from "react";

import { formatShares } from "./format";
import { metricCell, type MetricCell } from "./metrics";
import { MetricRowView, TabEmpty, TabError, TabSkeleton } from "./TabStates";

import type { OwnershipData, Resource } from "./useDetail";

export interface OwnershipTabProps {
  symbol: string;
  state: Resource<OwnershipData>;
  onRetry: () => void;
}

interface CellSpec {
  key: string;
  label: string;
  kind: "shares" | "pct" | "ratio";
}

const CELLS: readonly CellSpec[] = [
  { key: "shares_outstanding", label: "Shares outstanding", kind: "shares" },
  { key: "float_shares", label: "Float", kind: "shares" },
  { key: "insider_own", label: "Insider ownership", kind: "pct" },
  { key: "inst_own", label: "Institutional ownership", kind: "pct" },
  { key: "short_float", label: "Short float", kind: "pct" },
  { key: "short_ratio", label: "Short ratio", kind: "ratio" },
];

function cellFor(row: OwnershipData, spec: CellSpec): MetricCell {
  const value = row[spec.key];
  if (spec.kind === "shares") {
    const absent = typeof value !== "number" || !Number.isFinite(value);
    return { text: absent ? "—" : formatShares(value), unit: "sh", absent };
  }
  return metricCell(value, spec.kind);
}

export function OwnershipTab({
  symbol,
  state,
  onRetry,
}: OwnershipTabProps): React.ReactElement {
  if (state.status === "loading") {
    return <TabSkeleton label={`ownership for ${symbol}`} testId="ownership-loading" rows={6} />;
  }

  if (state.status === "error") {
    return (
      <TabError
        label="ownership"
        testId="ownership-error"
        message={state.error}
        onRetry={onRetry}
      />
    );
  }

  if (state.status === "empty" || !state.data) {
    return (
      <TabEmpty testId="ownership-empty">No ownership data for {symbol}.</TabEmpty>
    );
  }

  const row = state.data;

  return (
    <div data-testid="ownership-loaded" className="flex flex-col gap-2">
      <p className="text-label uppercase tracking-[0.08em] text-fg-subtle">
        From the screen snapshot
      </p>
      <dl>
        {CELLS.map((spec) => (
          <MetricRowView
            key={spec.key}
            label={spec.label}
            cell={cellFor(row, spec)}
            testId={`ownership-${spec.key}`}
          />
        ))}
      </dl>
      <p className="text-label text-fg-subtle">
        Institutional and insider percentages are of shares outstanding; short
        ratio is days to cover.
      </p>
    </div>
  );
}
