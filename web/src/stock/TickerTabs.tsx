/**
 * The ticker tab strip: eight sections, one seam, one keyboard contract.
 *
 * The address bar is the source of truth (`?tab=statements`), not local state:
 * a shared link opens the same section, Back returns to the previous one, and
 * a reload lands where the reader was. The strip is a real `role="tablist"`
 * with roving `tabindex`, `aria-selected` and `aria-controls`; ArrowLeft/Right
 * wrap, Home/End jump, and the panel is the element with the id each tab names.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

import type { TickerTabId } from "./useTicker";

export interface TickerTab {
  readonly id: TickerTabId;
  readonly label: string;
}

export const TICKER_TABS: readonly TickerTab[] = [
  { id: "overview", label: "Overview" },
  { id: "statements", label: "Financials" },
  { id: "peers", label: "Peers" },
  { id: "insider", label: "Insider" },
  { id: "news", label: "News" },
];

export const DEFAULT_TAB: TickerTabId = "overview";

/** `?tab=…` → a tab id; anything unknown is the overview. */
export function tabFromSearch(search: string): TickerTabId {
  const raw = new URLSearchParams(search.startsWith("?") ? search : `?${search}`).get("tab");
  const found = TICKER_TABS.find((tab) => tab.id === raw);
  return found?.id ?? DEFAULT_TAB;
}

/** The canonical href for a tab, mount included. */
export function tabHref(symbol: string, tab: TickerTabId): string {
  return `/stock/${encodeURIComponent(symbol)}?tab=${tab}`;
}

export const tabId = (tab: TickerTabId): string => `ticker-tab-${tab}`;

/**
 * The one panel the strip controls. Every tab names it, exactly as the
 * screener's view strip names its grid panel; the panel names the active tab
 * back through `aria-labelledby`, so the relationship is never one-way.
 */
export const TICKER_PANEL_ID = "ticker-panel";

export interface TickerTabsProps {
  readonly active: TickerTabId;
  readonly onChange: (tab: TickerTabId) => void;
}

export function TickerTabs({ active, onChange }: TickerTabsProps): React.ReactElement {
  const refs = React.useRef<Array<HTMLButtonElement | null>>([]);
  const current = Math.max(
    0,
    TICKER_TABS.findIndex((tab) => tab.id === active),
  );

  const focusAt = (index: number) => {
    const target = TICKER_TABS[(index + TICKER_TABS.length) % TICKER_TABS.length];
    if (target === undefined) return;
    onChange(target.id);
    refs.current[TICKER_TABS.indexOf(target)]?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    switch (event.key) {
      case "ArrowRight":
        event.preventDefault();
        focusAt(current + 1);
        break;
      case "ArrowLeft":
        event.preventDefault();
        focusAt(current - 1);
        break;
      case "Home":
        event.preventDefault();
        focusAt(0);
        break;
      case "End":
        event.preventDefault();
        focusAt(TICKER_TABS.length - 1);
        break;
      default:
        break;
    }
  };

  return (
    <div
      role="tablist"
      aria-label="Ticker sections"
      className="flex min-w-0 items-stretch gap-0.5 overflow-x-auto border-b border-line [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      {TICKER_TABS.map((tab, index) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            ref={(element) => {
              refs.current[index] = element;
            }}
            type="button"
            id={tabId(tab.id)}
            role="tab"
            aria-selected={selected}
            aria-controls={TICKER_PANEL_ID}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            onKeyDown={onKeyDown}
            className={cn(
              "relative -mb-px shrink-0 border-b-2 px-2.5 pb-1.5 pt-2 text-data transition-colors",
              selected
                ? "border-brand text-brand"
                : "border-transparent text-fg-muted hover:text-fg",
            )}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
