import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * The nine views of plan §6.1, in the order a session moves through them:
 * what is moving, why it is cheap, whether it is any good, then the rest.
 * `all` is not a tab — it is a destination with a 2,200 px floor, so it sits
 * apart from the strip rather than inside it.
 */
export const VIEWS = [
  { id: "today", label: "Today", columns: 11 },
  { id: "value", label: "Value", columns: 13 },
  { id: "quality", label: "Quality", columns: 12 },
  { id: "growth", label: "Growth", columns: 12 },
  { id: "momentum", label: "Momentum", columns: 10 },
  { id: "trend", label: "Trend", columns: 12 },
  { id: "ownership", label: "Ownership", columns: 10 },
  { id: "income", label: "Income", columns: 8 },
  { id: "profile", label: "Profile", columns: 12 },
] as const;

export type ViewId = (typeof VIEWS)[number]["id"];

export interface ViewTabsProps {
  active: ViewId;
  onChange: (id: ViewId) => void;
}

/**
 * Placeholder tab strip: real roving-tabindex semantics, no data behind it yet.
 *
 * Arrow keys move selection the way a real tablist must (WAI-ARIA APG), so the
 * keyboard contract is settled now and Phase 4 only has to feed it columns.
 */
export function ViewTabs({ active, onChange }: ViewTabsProps) {
  const refs = React.useRef<Array<HTMLButtonElement | null>>([]);

  const focusAt = (index: number) => {
    const target = VIEWS[(index + VIEWS.length) % VIEWS.length];
    if (!target) return;
    onChange(target.id);
    refs.current[VIEWS.indexOf(target)]?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    const index = VIEWS.findIndex((view) => view.id === active);
    switch (event.key) {
      case "ArrowRight":
        event.preventDefault();
        focusAt(index + 1);
        break;
      case "ArrowLeft":
        event.preventDefault();
        focusAt(index - 1);
        break;
      case "Home":
        event.preventDefault();
        focusAt(0);
        break;
      case "End":
        event.preventDefault();
        focusAt(VIEWS.length - 1);
        break;
      default:
        break;
    }
  };

  return (
    <div className="flex min-w-0 items-stretch justify-between gap-6">
      <div
        role="tablist"
        aria-label="Screener views"
        className="flex min-w-0 items-stretch gap-0.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {VIEWS.map((view, index) => {
          const selected = view.id === active;
          return (
            <button
              key={view.id}
              ref={(element) => {
                refs.current[index] = element;
              }}
              type="button"
              role="tab"
              aria-selected={selected}
              tabIndex={selected ? 0 : -1}
              onClick={() => onChange(view.id)}
              onKeyDown={onKeyDown}
              className={cn(
                "relative -mb-px shrink-0 px-2.5 pb-1.5 pt-2 text-ui transition-colors",
                "border-b-2",
                selected
                  ? "border-brand text-fg"
                  : "border-transparent text-fg-muted hover:text-fg",
              )}
            >
              {view.label}
              <span className="ml-1.5 font-mono text-label text-fg-subtle">
                {view.columns}
              </span>
            </button>
          );
        })}
      </div>

      {/* A native title, not the vendored Tooltip: Radix's popper machinery
          measured 21 KB gzip on this build (85 -> 106 KB) for one sentence.
          The tooltip stays vendored for Phase 2's column-header help, where a
          dozen hints share the cost. */}
      <button
        type="button"
        className="mb-1 shrink-0 self-end rounded-sm border border-line px-2 py-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
        title="All 116 catalogue columns — horizontal scroll is explicit here"
      >
        All 116
      </button>
    </div>
  );
}
