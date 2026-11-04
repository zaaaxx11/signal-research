import { type CSSProperties, type ReactNode } from "react";

/**
 * A solid-background section title — one distinct fill per section kind.
 *
 * Every boxed section on every page headlines itself with one of the eight
 * title tokens (`--title-1` … `--title-8`), so a reader can tell sections
 * apart by the heading's colour alone. The fill prints its own ink token
 * (`--title-ink`), restated per theme in tokens.css and measured there.
 *
 * `hint` renders a muted explainer after the label; `count` a right-aligned
 * quantity (40, "83 filters"), both inside the same solid block so the
 * heading stays one object.
 */
export function SectionTitle({
  variant,
  children,
  hint,
  count,
  right,
  testId,
}: {
  /** Which section's fill. Mapped 1:1 onto the page that owns the section. */
  variant: 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8;
  children: ReactNode;
  hint?: string;
  count?: string;
  /** Arbitrary controls rendered right-aligned outside the fill (a link, switch). */
  right?: ReactNode;
  testId?: string;
}) {
  const style: CSSProperties = {
    background: `var(--title-${variant})`,
    color: "var(--title-ink)",
  };
  return (
    <div className="mb-2 flex items-center justify-between gap-2" data-testid={testId}>
      <h2
        style={style}
        className="inline-flex min-h-7 items-center gap-2 rounded-[6px] px-3 py-1 text-[13px] font-semibold tracking-wide"
      >
        {children}
        {hint ? <span className="text-[11px] font-normal opacity-75">{hint}</span> : null}
        {count ? <span className="text-[11px] font-medium opacity-85">{count}</span> : null}
      </h2>
      {right}
    </div>
  );
}

/** The token name for a variant, for callers that paint their own solid block. */
export function titleVar(variant: 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8): string {
  return `var(--title-${variant})`;
}
