import { type ReactNode } from "react";

/**
 * A section title — the page's own background behind it, the section's own
 * colour on the words. Every section headline carries one of the eight title
 * tokens (`--title-1` … `--title-8`) as its TEXT colour, so sections are told
 * apart by the words' colour alone; no chip of paint, no bar.
 *
 * `hint` renders a muted explainer after the label; `count` a trailing
 * quantity; `right` any right-aligned control outside the heading itself.
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
  return (
    <div className="mb-2 flex items-center justify-between gap-2" data-testid={testId}>
      <h2
        style={{ color: `var(--title-${variant})` }}
        className="inline-flex w-max items-center gap-1.5 text-[13px] font-semibold uppercase tracking-wide"
      >
        {children}
        {hint ? <span className="text-[10px] font-normal opacity-70">{hint}</span> : null}
        {count ? <span className="text-[10px] font-medium opacity-85">{count}</span> : null}
      </h2>
      {right}
    </div>
  );
}

/** The token name for a variant, for callers that paint their own solid block. */
export function titleVar(variant: 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8): string {
  return `var(--title-${variant})`;
}
