/**
 * The view strip. It renders whatever `GET /screens/select/schema` served —
 * names, labels, order, and the column count shown beside each tab. Nothing
 * here spells a view or a column; that is the Phase 0 F1 lesson.
 *
 * Roving tabindex and arrow keys are the WAI-ARIA tablist contract, carried
 * over from the Phase 1 placeholder because the grid is operable without a
 * mouse and the tab strip is the first control in that path. The other half of
 * that contract is the panel: each tab carries `aria-controls` pointing at
 * :data:`VIEW_PANEL_ID`, and `ScreenPanel` (the element with that id) names the
 * selected tab back through `aria-labelledby`. A `role="tab"` with no
 * `role="tabpanel"` announces a relationship that does not exist.
 */

import * as React from "react";

import type { ViewDefinition } from "@/data/screens";
import { cn } from "@/lib/utils";

export interface ViewTabsProps {
  views: ViewDefinition[];
  active: string;
  onChange: (name: string) => void;
}

/** The single panel the strip controls; `ScreenPanel` renders it. */
export const VIEW_PANEL_ID = "view-panel";

/** The tab's element id, so the panel can name the tab that selected it. */
export const viewTabId = (name: string): string => `view-tab-${name}`;

export function ViewTabs({ views, active, onChange }: ViewTabsProps) {
  const refs = React.useRef<Array<HTMLButtonElement | null>>([]);
  const current = Math.max(
    0,
    views.findIndex((view) => view.name === active),
  );
  /**
   * A tablist with no selected tab (the all-columns destination is open) still
   * owes the keyboard one stop: otherwise the strip becomes unreachable by Tab
   * exactly while it is the way back to the nine views.
   */
  const anySelected = views.some((view) => view.name === active);

  const focusAt = (index: number) => {
    if (views.length === 0) return;
    const target = views[(index + views.length) % views.length];
    if (!target) return;
    onChange(target.name);
    refs.current[views.indexOf(target)]?.focus();
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
        focusAt(views.length - 1);
        break;
      default:
        break;
    }
  };

  return (
    <div
      role="tablist"
      aria-label="Screener views"
      className="flex min-w-0 items-stretch gap-0.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      {views.map((view, index) => {
        const selected = view.name === active;
        return (
          <button
            key={view.name}
            ref={(element) => {
              refs.current[index] = element;
            }}
            type="button"
            id={viewTabId(view.name)}
            role="tab"
            aria-selected={selected}
            aria-controls={VIEW_PANEL_ID}
            // The count is a column count, not part of the name: a tab read as
            // "Today 11" is a sentence fragment. An explicit name is used rather
            // than a hidden suffix because Chrome joins separate text nodes with
            // a space, which produced "Today , 11 columns".
            aria-label={`${view.label}, ${view.columns.length} columns`}
            tabIndex={selected || (!anySelected && index === 0) ? 0 : -1}
            onClick={() => onChange(view.name)}
            onKeyDown={onKeyDown}
            className={cn(
              "relative -mb-px shrink-0 px-2.5 pb-1.5 pt-2 text-ui transition-colors",
              "border-b-2",
              selected ? "border-brand text-fg" : "border-transparent text-fg-muted hover:text-fg",
            )}
          >
            {view.label}
            {/* The count is sighted-only: the accessible name above already
                says "11 columns". */}
            <span aria-hidden="true" className="ml-1.5 font-mono text-label text-fg-subtle">
              {view.columns.length}
            </span>
          </button>
        );
      })}
    </div>
  );
}
