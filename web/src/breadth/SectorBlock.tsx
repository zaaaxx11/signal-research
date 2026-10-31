import * as React from "react";

import { cn } from "@/lib/utils";

import { formatCount, signedPercent, type SignedPercent } from "./format";
import { sortSectors, type SectorBucket } from "./report";

/**
 * The sector block: every provider bucket as a compact grid of labelled cells.
 *
 * A canvas heatmap was rejected on purpose (plan §8): it defeats text
 * selection, screen readers and Ctrl-F. This is a real list — `<ul>`/`<li>`
 * with a `<dl>` of labels inside each cell — so every value is text and the
 * structure is the structure, not a drawing of one.
 *
 * Layout decision: the sector name owns the first line and the three figures
 * share the second (`-1.10%  1,610 · 452 / 1,039`). A 150 px tile cannot hold
 * "Health Technology" and three visible labels at once; truncating the name
 * instead would make several sectors indistinguishable. So the labels are
 * `<dt class="sr-only">` — every cell still announces "names / advancing /
 * declining" — and the visible legend above the list states the column order
 * once, exactly like a table header.
 *
 * Two invariants:
 *
 * * **Sorted by average change, strongest first.** Sorting happens here, once,
 *   over the averages the endpoint returned; nothing is averaged in the browser.
 * * **Colour repeats the sign, never carries it alone.** The cell prints
 *   `+0.48%` / `-1.10%` / `n/a`, and only that printed string decides the tone.
 */

export interface SectorBlockProps {
  readonly sectors: readonly SectorBucket[];
  /** Rows the scan could not attribute to a provider sector, when it said. */
  readonly sectorMissing?: number | null;
  readonly className?: string;
}

function averageTitle(name: string, average: SignedPercent): string {
  return average.absent
    ? `${name}: no row in this sector carried a reported change, so the scan states no average`
    : `${name}: arithmetic mean over the rows of this sector carrying a numeric change`;
}

function SectorCell({ sector }: { readonly sector: SectorBucket }) {
  const average = signedPercent(sector.avg_change_pct);
  const name = sector.name ?? "Unspecified";
  const tone =
    average.sign === 1
      ? "text-up"
      : average.sign === -1
        ? "text-down"
        : average.sign === 0
          ? "text-flat"
          : // An absent average is not a flat market: neutral ink, and the text
            // says `n/a` so the reader is not told "unchanged".
            "text-fg-subtle";

  return (
    <li
      data-testid="sector-cell"
      data-sign={average.sign === null ? "absent" : average.sign}
      className="min-w-0 overflow-hidden rounded-sm border border-line bg-surface px-1.5 py-0.5"
    >
      {/* A long provider label is clipped, never allowed to widen the strip;
          the full text stays reachable as the element's title. */}
      <span
        data-testid="sector-name"
        className="block truncate text-data leading-tight text-fg"
        title={name}
      >
        {name}
      </span>

      <div className="mt-px flex min-w-0 items-baseline justify-between gap-1.5 font-mono text-label leading-tight">
        <span
          data-testid="sector-avg"
          className={cn("nums shrink-0", tone)}
          title={averageTitle(name, average)}
        >
          {average.text}
        </span>
        <dl className="flex min-w-0 items-baseline gap-1 text-fg-subtle">
          <div className="flex items-baseline">
            <dt className="sr-only">names</dt>
            <dd data-testid="sector-count" className="nums text-fg-muted">
              {formatCount(sector.count)}
            </dd>
          </div>
          <span aria-hidden="true">·</span>
          <div className="flex items-baseline">
            <dt className="sr-only">advancing</dt>
            <dd data-testid="sector-adv" className="nums text-fg-muted">
              {formatCount(sector.advancing)}
            </dd>
          </div>
          <span aria-hidden="true">/</span>
          <div className="flex items-baseline">
            <dt className="sr-only">declining</dt>
            <dd data-testid="sector-dec" className="nums text-fg-muted">
              {formatCount(sector.declining)}
            </dd>
          </div>
        </dl>
      </div>
    </li>
  );
}

export function SectorBlock({ sectors, sectorMissing = null, className }: SectorBlockProps) {
  const headingId = React.useId();
  const rows = React.useMemo(() => sortSectors(sectors), [sectors]);
  const unattributed = sectorMissing !== null && sectorMissing > 0;

  return (
    <section aria-labelledby={headingId} data-testid="sector-block" className={cn("min-w-0", className)}>
      <div className="flex min-w-0 items-baseline justify-between gap-2">
        <h3 id={headingId} className="shrink-0 text-label uppercase tracking-[0.1em] text-fg-subtle">
          Sectors
        </h3>
        <p className="truncate font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
          {rows.length} sectors · sorted by average change · avg · names · adv / dec
          {unattributed ? ` · ${formatCount(sectorMissing)} unattributed` : ""}
        </p>
      </div>

      {rows.length === 0 ? (
        <p data-testid="sector-empty" className="mt-1.5 text-data text-fg-muted">
          No sector detail in this snapshot — the scan returned no sector buckets.
        </p>
      ) : (
        <ul
          data-testid="sector-grid"
          className="mt-1 grid min-w-0 grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] gap-1"
        >
          {rows.map((sector, index) => (
            // The index is part of the key because the server is allowed to
            // repeat a label (and does, for the null bucket); order is stable
            // after `sortSectors`, so the key is stable too.
            <SectorCell key={`${sector.name ?? "unspecified"}:${index}`} sector={sector} />
          ))}
        </ul>
      )}
    </section>
  );
}
