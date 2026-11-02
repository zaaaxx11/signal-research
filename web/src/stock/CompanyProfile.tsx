/**
 * The company profile: what the business does, and who runs it.
 *
 * One read of `GET /engine/company/{symbol}`, mounted by Overview and by no
 * other tab. The description is one paragraph clamped to four lines — long
 * enough to place the company, short enough that the snapshot stays above the
 * fold — with a "More" button that is only offered when the text is actually
 * longer than the clamp (a character threshold, because jsdom has no line
 * boxes and a button that expands nothing is worse than no button).
 *
 * The officers are a compact two-column list: each entry is `Title — Name`,
 * the title in muted small caps (the label), the name in body ink. Two columns
 * on desktop, one below 700 px, 20 px rows.
 *
 * Four states, never an empty heading: a skeleton while the read is open, the
 * sentence "No company profile available." for a 404 or an empty document, a
 * retry for a transport failure, and the profile itself.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

import { useCompany } from "./useTicker";

/** Four lines at the density's 12 px/20 px, roughly; the clamp is CSS' job. */
export const COMPANY_CLAMP_CHARS = 280;

export interface CompanyProfileProps {
  readonly symbol: string;
  readonly className?: string;
}

function Heading(): React.ReactElement {
  return (
    <h2
      id="company-profile-heading"
      className="text-label font-medium uppercase tracking-[0.08em] text-fg-subtle"
    >
      Company profile
    </h2>
  );
}

export function CompanyProfile({ symbol, className }: CompanyProfileProps): React.ReactElement {
  const state = useCompany(symbol);
  const [expanded, setExpanded] = React.useState(false);
  const [overflowing, setOverflowing] = React.useState(false);
  const paragraphRef = React.useRef<HTMLParagraphElement | null>(null);
  const paragraphId = React.useId();
  const description = state.data?.description ?? null;

  /**
   * Does the text actually overflow four lines?
   *
   * A browser can answer this (`scrollHeight` > `clientHeight`); jsdom cannot
   * (every box is 0), so it falls back to a character estimate. The measurement
   * runs only while clamped — expanding changes the box and would otherwise
   * erase the reason the button exists.
   */
  React.useLayoutEffect(() => {
    if (expanded) return;
    const node = paragraphRef.current;
    if (node && node.clientHeight > 0) {
      setOverflowing(node.scrollHeight > node.clientHeight + 1);
      return;
    }
    setOverflowing((description?.length ?? 0) > COMPANY_CLAMP_CHARS);
  }, [description, expanded]);

  return (
    <section
      data-testid="company-profile"
      data-status={state.status}
      aria-labelledby="company-profile-heading"
      className={cn("min-w-0", className)}
    >
      <Heading />

      {state.status === "loading" ? (
        <div
          data-testid="company-loading"
          aria-busy="true"
          aria-live="polite"
          className="mt-1 flex flex-col gap-1.5"
        >
          <span className="sr-only">Loading the company profile for {symbol}…</span>
          {Array.from({ length: 4 }, (_, index) => (
            <div
              key={index}
              className={cn("h-3 rounded-sm bg-surface-sunken", index === 3 ? "w-2/3" : "w-full")}
            />
          ))}
        </div>
      ) : state.status === "error" ? (
        <div className="mt-1" role="alert" data-testid="company-error">
          <p className="text-data text-fg-muted">The company profile could not be loaded.</p>
          {state.error ? (
            <p className="mt-0.5 break-words text-label text-fg-subtle">{state.error}</p>
          ) : null}
          <button
            type="button"
            onClick={state.retry}
            className="focus-ring mt-1 h-6 rounded-sm border border-line-control px-2 text-data text-fg-muted hover:text-fg"
          >
            Retry
          </button>
        </div>
      ) : !state.data ? (
        <p data-testid="company-empty" className="mt-1 text-data text-fg-muted">
          No company profile available.
        </p>
      ) : (
        <div className="mt-1 flex flex-col gap-2">
          {state.data.description ? (
            <div>
              <p
                id={paragraphId}
                ref={paragraphRef}
                data-testid="company-description"
                className={cn(
                  "text-data leading-5 text-fg-muted",
                  !expanded && "line-clamp-4",
                )}
              >
                {state.data.description}
              </p>
              {overflowing ? (
                <button
                  type="button"
                  aria-expanded={expanded}
                  aria-controls={paragraphId}
                  data-testid="company-more"
                  onClick={() => setExpanded((value) => !value)}
                  className="focus-ring mt-0.5 h-5 text-data text-accent-text hover:text-brand"
                >
                  {expanded ? "Less" : "More"}
                </button>
              ) : null}
            </div>
          ) : null}

          {state.data.officers.length > 0 ? (
            <div>
              <h3 className="text-label uppercase tracking-[0.06em] text-fg-subtle">Key people</h3>
              <ul
                data-testid="company-officers"
                className="mt-0.5 grid grid-cols-1 gap-x-6 min-[700px]:grid-cols-2"
              >
                {state.data.officers.map((officer, index) => (
                  <li
                    key={`${officer.name}-${index}`}
                    className="flex h-5 min-w-0 items-baseline gap-1.5 border-b border-line/60 text-data last:border-b-0"
                  >
                    {officer.title ? (
                      <>
                        <span
                          className="shrink-0 text-label uppercase tracking-[0.04em] text-fg-subtle"
                          title={officer.title}
                        >
                          {officer.title}
                        </span>
                        <span aria-hidden="true" className="text-fg-subtle">
                          —
                        </span>
                      </>
                    ) : null}
                    <span className="truncate text-fg" title={officer.name}>
                      {officer.name}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
