/**
 * The sector control: a menu in the toolbar, and the chip that says what it did.
 *
 * The menu lists **All sectors** plus the market's sector labels — the 21 the
 * breadth route counted, or, when breadth cannot be read, the distinct `sector`
 * cells of the rows on screen. Which of the two is in use is printed beside the
 * menu, because those are different populations: 21 labels drawn from 5 678
 * names is not the same claim as 7 labels drawn from the hundred rows a page
 * happens to hold.
 *
 * The labels are the wire values. The API matches `sec` case-sensitively
 * against the Finviz spellings, so nothing here folds case, swaps `&` for `and`
 * or trims a name into a different one — the option value is the byte-for-byte
 * label the payload carried.
 *
 * A sector the scan could not label is a null bucket, not a sector; it never
 * becomes an option (see `sectors.ts`).
 */

/** Which population the offered names came from; printed beside the menu. */
export type SectorSource = "breadth" | "rows" | "loading" | "none";

export interface SectorSelectProps {
  /** The market's sector labels, already cleaned and sorted. */
  readonly names: readonly string[];
  readonly source: SectorSource;
  /** The sector being shown, or null for the unfiltered panel. */
  readonly value: string | null;
  readonly onChange: (name: string | null) => void;
}

export const ALL_SECTORS = "All sectors";

function sourceLabel(source: SectorSource, count: number): { text: string; title: string } {
  switch (source) {
    case "breadth":
      return {
        text: "from breadth",
        title: `The ${count} sector names are the ones GET /market/breadth counted over the whole universe.`,
      };
    case "rows":
      return {
        text: "from rows on screen",
        title: `Market breadth is unavailable: these ${count} names come from the sector cells of the rows on screen, not from the whole market.`,
      };
    case "loading":
      return { text: "sector list loading", title: "Reading the sector list from GET /market/breadth." };
    case "none":
      return {
        text: "no sector list",
        title: "No sector names are available yet: breadth did not answer and these rows carry no sector cell.",
      };
  }
}

export function SectorSelect({ names, source, value, onChange }: SectorSelectProps) {
  // A link may name a sector the scan no longer lists (or breadth may be down
  // while the link is opened). The chosen name is still offered, and still
  // selected, so the control never lies about what is being sent.
  const options = value !== null && !names.includes(value) ? [value, ...names] : names;
  const label = sourceLabel(source, options.length);

  return (
    <span className="flex min-w-0 items-center gap-1">
      <label className="flex items-center gap-1 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
        sector
        <select
          aria-label="Sector"
          data-testid="sector-select"
          value={value ?? ""}
          title={value ?? ALL_SECTORS}
          onChange={(event) => {
            const next = event.target.value;
            onChange(next.length > 0 ? next : null);
          }}
          className="max-w-[11rem] truncate rounded-sm border border-line bg-surface-sunken px-1 py-0.5 font-mono text-label tracking-[0.06em] text-fg-muted"
        >
          <option value="">{ALL_SECTORS}</option>
          {options.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <span
        data-testid="sector-source"
        data-source={source}
        title={label.title}
        className="hidden shrink-0 font-mono text-label tracking-[0.04em] text-fg-subtle xl:inline"
      >
        {label.text}
      </span>
    </span>
  );
}

export interface SectorChipProps {
  readonly name: string;
  readonly onClear: () => void;
  /**
   * Draw only the chip, without its own bar.
   *
   * `ScreenPanel` puts the sector chip and the exchange chip in one shared
   * `control-chip-bar`, so two controls do not stack two full-width rows. The
   * default (`false`) keeps the standalone markup — and every existing test —
   * byte-identical.
   */
  readonly bare?: boolean;
}

/**
 * The active sector, removable.
 *
 * One chip for the one predicate the menu owns; the chip bar above the table
 * draws every *other* predicate. Removing it is the same action as choosing
 * "All sectors": the `sec` equality leaves the request and everything else the
 * reader filtered on stays.
 */
export function SectorChip({ name, onClear, bare = false }: SectorChipProps) {
  const chip = (
    <span
      data-testid="sector-chip"
      className="inline-flex h-6 items-center gap-1 rounded-sm border border-line-strong bg-surface-raised pl-1.5 text-data text-fg"
    >
      <span className="text-fg-subtle">Sector:</span>{" "}
      <span
        data-testid="sector-chip-name"
        className="max-w-[18rem] truncate text-fg-muted"
        title={name}
      >
        {name}
      </span>
      <button
        type="button"
        aria-label={`Clear sector filter: ${name}`}
        onClick={onClear}
        className="h-5 w-5 rounded-sm text-fg-muted transition-colors hover:bg-row-hover hover:text-fg"
      >
        ×
      </button>
    </span>
  );

  if (bare) return chip;

  return (
    <div
      data-testid="sector-chip-bar"
      role="group"
      aria-label="Sector filter"
      className="flex flex-wrap items-center gap-1 border-b border-line bg-surface-sunken px-2 py-1"
    >
      {chip}
    </div>
  );
}
