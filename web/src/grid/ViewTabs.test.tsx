import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ViewDefinition } from "@/data/screens";

import { ScreenPanel } from "./ScreenPanel";
import { VIEW_PANEL_ID, ViewTabs, viewTabId } from "./ViewTabs";

/**
 * F14 — `role="tablist"` + `role="tab"` with no `role="tabpanel"` and no
 * `aria-controls`: the tab strip announced a control relationship that did not
 * exist. This asserts the whole wiring, the way a screen reader follows it:
 * each tab names the panel, and the panel names the tab that is selected.
 */

const VIEWS: ViewDefinition[] = [
  { name: "today", label: "Today", job: "What is moving right now?", columns: ["price"] },
  { name: "value", label: "Value", job: "Is it cheap?", columns: ["market_cap"] },
];

function page(view: string): Response {
  return new Response(
    JSON.stringify({
      as_of: "2026-10-31T01:28:01Z",
      limit: 100,
      total: 1,
      universe_total: 1,
      returned: 1,
      columns: [],
      rows: [{ symbol: `${view}1`, price: 1, market_cap: 1 }],
      budget: { truncated: false, dropped_fields: [] },
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

function Harness() {
  const [active, setActive] = React.useState("today");
  const view = VIEWS.find((entry) => entry.name === active) ?? VIEWS[0]!;
  return (
    <>
      <ViewTabs views={VIEWS} active={active} onChange={setActive} />
      <ScreenPanel
        view={view}
        onActivate={vi.fn()}
        containerWidth={1280}
      />
    </>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the tab strip and the panel it controls (F14)", () => {
  it("points every tab at a real tabpanel, and the panel back at its tab", () => {
    vi.stubGlobal("fetch", vi.fn(async () => page("today")));
    render(<Harness />);

    const panel = screen.getByRole("tabpanel");
    expect(panel.id).toBe(VIEW_PANEL_ID);
    expect(document.querySelectorAll('[role="tabpanel"]')).toHaveLength(1);

    for (const tab of screen.getAllByRole("tab")) {
      expect(tab.getAttribute("aria-controls")).toBe(VIEW_PANEL_ID);
      expect(document.getElementById(tab.id)).toBe(tab);
    }

    const selectedTab = screen.getByRole("tab", { selected: true });
    expect(selectedTab.id).toBe(viewTabId("today"));
    expect(panel.getAttribute("aria-labelledby")).toBe(selectedTab.id);
  });

  it("follows the selection: the panel names the tab that is now selected", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => page("value")));
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole("tab", { name: /Value/ }));

    await waitFor(() =>
      expect(screen.getByRole("tabpanel").getAttribute("aria-labelledby")).toBe(
        viewTabId("value"),
      ),
    );
    expect(screen.getByRole("tab", { selected: true }).id).toBe(viewTabId("value"));
    expect(screen.getByRole("tab", { selected: true }).getAttribute("aria-controls")).toBe(
      VIEW_PANEL_ID,
    );
  });
});
