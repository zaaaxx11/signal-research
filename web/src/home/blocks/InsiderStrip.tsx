/**
 * The insider strip: the latest five Form 4 filings, and the door to the rest.
 *
 * The link to `/insider` is in the card's header and is rendered in every state
 * — loaded, empty, missing, failed. That is deliberate: the strip is an index of
 * a page that exists whether or not today's batch arrived, and a reader who came
 * for insider activity should never be left with a dead end because one source
 * was down.
 *
 * A filing row is four facts: when it was filed, who filed it, their role, and
 * the symbol it concerns. Long officer names truncate in their own cell; the
 * full name stays in the title.
 */

import { Link } from "@/shell/routes";

import type { BlockState, HomeFiling } from "../home";
import { BLOCK_HEIGHT, HomeBlock } from "./HomeBlock";
import { SkeletonBar } from "./HomeBlock";

const COLUMNS = "grid-cols-1 min-[700px]:grid-cols-2 min-[1100px]:grid-cols-5";

export interface InsiderStripProps {
  readonly state: BlockState;
  readonly filings: readonly HomeFiling[] | null;
  readonly className?: string;
}

function InsiderCell({ filing }: { filing: HomeFiling }) {
  return (
    <div
      data-filing={filing.symbol || filing.owner}
      className="flex h-[52px] min-w-0 flex-col justify-center gap-0.5 rounded-sm border border-line bg-surface-sunken px-1.5"
    >
      <div className="flex min-w-0 items-baseline justify-between gap-1">
        <span className="shrink-0 text-[10px] text-fg-subtle" title={filing.filed ?? "filing date unavailable"}>
          {filing.filed ?? "—"}
        </span>
        {filing.symbol.length === 0 ? null : (
          <Link
            to={`/stock/${encodeURIComponent(filing.symbol)}`}
            className="truncate font-mono text-[11px] font-semibold text-accent-text hover:underline"
          >
            {filing.symbol}
          </Link>
        )}
      </div>
      <span className="min-w-0 truncate text-[11px] text-fg" title={filing.owner}>
        {filing.owner.length > 0 ? filing.owner : "Unnamed filer"}
      </span>
      <span className="min-w-0 truncate text-[10px] text-fg-subtle" title={filing.role}>
        {filing.role.length > 0 ? filing.role : "—"}
      </span>
    </div>
  );
}

function InsiderSkeleton() {
  return (
    <div aria-hidden="true" data-testid="insider-skeleton" className={`grid gap-1 p-2 ${COLUMNS}`}>
      {Array.from({ length: 5 }, (_, index) => (
        <div
          key={index}
          className="flex h-[52px] flex-col justify-center gap-1 rounded-sm border border-line bg-surface-sunken px-1.5"
        >
          <SkeletonBar width="40%" height={7} />
          <SkeletonBar width="72%" height={9} />
          <SkeletonBar width="54%" height={7} />
        </div>
      ))}
    </div>
  );
}

export function InsiderStrip({ state, filings, className }: InsiderStripProps) {
  const shown = filings === null ? [] : filings.slice(0, 5);

  return (
    <HomeBlock
      id="insider"
      title="Latest Form 4 filings"
      className={className}
      state={state}
      minHeight={BLOCK_HEIGHT.insider}
      skeleton={<InsiderSkeleton />}
      actions={
        <Link
          to="/insider"
          data-testid="insider-all"
          className="shrink-0 text-[10px] text-accent-text hover:underline"
        >
          All filings
        </Link>
      }
    >
      {shown.length === 0 ? (
        <p className="px-2 py-2 text-[11px] text-fg-subtle">
          No Form 4 filings in the latest window.
        </p>
      ) : (
        <div className={`grid gap-1 p-2 ${COLUMNS}`}>
          {shown.map((filing) => (
            <InsiderCell key={`${filing.symbol}:${filing.owner}:${filing.filed ?? ""}`} filing={filing} />
          ))}
        </div>
      )}
    </HomeBlock>
  );
}
