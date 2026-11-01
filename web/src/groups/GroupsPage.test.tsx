import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { formatMoney } from "@/grid/format";

import { GROUPS_RESERVED_HEIGHT, GROUPS_SKELETON_ROWS, GroupsPage } from "./GroupsPage";

/**
 * The Groups page's promises, on the rendered page:
 *
 * * one request for the whole table;
 * * every group row draws, in change-descending order;
 * * the change cell carries an arrow, an explicit sign and a spoken word — the
 *   colour is never the only channel;
 * * a row leads to the screener with the label the server sent, encoded, and
 *   the address bar decodes back to it;
 * * the breadth bar's width is the count ratio, not a measured pixel;
 * * loading, empty and error are states of one box, so the answer cannot move
 *   the page.
 */

const GROUPS = [
  {
    id: "energy",
    label: "Energy",
    count: 300,
    avg_change: -1.2,
    adv: 90,
    dec: 200,
    unchanged: 10,
    market_cap: 2_300_000_000_000,
  },
  {
    id: "technology",
    label: "Technology",
    count: 700,
    avg_change: 1.42,
    adv: 400,
    dec: 300,
    unchanged: 0,
    market_cap: 15_200_000_000_000,
  },
  {
    id: "health",
    label: "Health Care",
    count: 600,
    avg_change: 0,
    adv: 210,
    dec: 380,
    unchanged: 10,
    market_cap: 8_100_000_000_000,
  },
];

function groupsDocument(overrides: Record<string, unknown> = {}) {
  return {
    as_of: "2026-11-02T14:30:00Z",
    region: "us",
    universe_total: 5_678,
    groups: GROUPS,
    ...overrides,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stub(body: unknown = groupsDocument()) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL) => json(body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GroupsPage", () => {
  it("asks for groups exactly once and draws every row", async () => {
    const fetchMock = stub();
    render(<GroupsPage />);

    expect(await screen.findByTestId("groups-table")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/market/groups?region=us");

    const rows = screen.getAllByTestId(/^group-row-/);
    expect(rows).toHaveLength(3);
    expect(rows.map((row) => row.getAttribute("data-sector-row"))).toEqual([
      "Technology",
      "Health Care",
      "Energy",
    ]);
  });

  it("prints every column of every row", async () => {
    stub();
    render(<GroupsPage />);
    await screen.findByTestId("groups-table");

    const technology = screen.getByTestId("group-row-technology");
    expect(technology.textContent).toContain("Technology");
    expect(technology.textContent).toContain("+1.42%");
    expect(technology.textContent).toContain("400");
    expect(technology.textContent).toContain("300");
    expect(technology.textContent).toContain("700");
    expect(technology.textContent).toContain(formatMoney(15_200_000_000_000, true));
    expect(technology.textContent).toContain("$15.2T");
  });

  it("keeps every row reachable with the keyboard", async () => {
    stub();
    render(<GroupsPage />);
    await screen.findByTestId("groups-table");

    const link = screen.getByRole("link", { name: "Energy" });
    link.focus();
    expect(document.activeElement).toBe(link);
  });

  it("never lets colour carry the direction alone", async () => {
    stub();
    render(<GroupsPage />);
    await screen.findByTestId("groups-table");

    const up = screen.getByTestId("group-change-technology");
    expect(up.textContent).toContain("▲");
    expect(up.textContent).toContain("+1.42%");
    expect(up.querySelector(".sr-only")?.textContent?.trim()).toBe("up");

    const down = screen.getByTestId("group-change-energy");
    expect(down.textContent).toContain("▼");
    expect(down.textContent).toContain("-1.20%");
    expect(down.querySelector(".sr-only")?.textContent?.trim()).toBe("down");

    const flat = screen.getByTestId("group-change-health");
    expect(flat.textContent).toContain("0.00%");
    expect(flat.querySelector(".sr-only")?.textContent?.trim()).toBe("unchanged");
  });

  it("builds a screener href whose sector decodes back to the server's label", async () => {
    stub();
    render(<GroupsPage />);
    await screen.findByTestId("groups-table");

    const link = screen.getByRole("link", { name: "Health Care" });
    expect(link.getAttribute("href")).toBe("/app/screener?f=sec:eq:Health%20Care");

    const [path, query = ""] = (link.getAttribute("href") ?? "").split("?");
    expect(path).toBe("/app/screener");
    expect(new URLSearchParams(query).get("f")?.replace(/^sec:eq:/, "")).toBe("Health Care");
  });

  it("navigates from the row itself, not only from the label", async () => {
    stub();
    const user = userEvent.setup();
    render(<GroupsPage />);
    await screen.findByTestId("groups-table");

    // The advancing count is a cell no anchor covers: a click there is the
    // row's own click, and it must still open the screener.
    await user.click(screen.getByTestId("group-adv-health"));

    expect(window.location.pathname).toBe("/app/screener");
    expect(new URLSearchParams(window.location.search).get("f")?.replace(/^sec:eq:/, "")).toBe("Health Care");

    await user.click(screen.getByRole("link", { name: "Technology" }));
    expect(new URLSearchParams(window.location.search).get("f")?.replace(/^sec:eq:/, "")).toBe("Technology");
  });

  it("draws the breadth bar from the counts, not from geometry", async () => {
    stub();
    render(<GroupsPage />);
    await screen.findByTestId("groups-table");

    // 400 / (400 + 300) = 57.1 %, 210 / (210 + 380) = 35.6 %, 90 / 290 = 31.0 %.
    const upWidth = (id: string) =>
      Number.parseFloat(screen.getByTestId(`group-bar-up-${id}`).style.width);
    expect(upWidth("technology")).toBeCloseTo(57.1, 5);
    expect(upWidth("health")).toBeCloseTo(35.6, 5);
    expect(upWidth("energy")).toBeCloseTo(31, 5);

    // One decimal, not the float the division produced: the drawing claims the
    // precision the counts support and no more.
    for (const id of ["technology", "health", "energy"]) {
      expect(String(upWidth(id)).split(".")[1]?.length ?? 0).toBeLessThanOrEqual(1);
    }

    // The two segments tile the track exactly, so a rounding step cannot leave
    // a gap that reads as a third bucket.
    for (const id of ["technology", "health", "energy"]) {
      const track = screen.getByTestId(`group-bar-${id}`);
      const up = track.querySelector<HTMLElement>(`[data-testid="group-bar-up-${id}"]`);
      const down = track.querySelector<HTMLElement>(`[data-testid="group-bar-down-${id}"]`);
      const upWidth = Number.parseFloat(up?.style.width ?? "NaN");
      const downWidth = Number.parseFloat(down?.style.width ?? "NaN");
      expect(Number.isFinite(upWidth)).toBe(true);
      expect(Number.isFinite(downWidth)).toBe(true);
      expect(Math.round((upWidth + downWidth) * 10) / 10).toBe(100);
    }

    // …and the bar speaks the two counts it was drawn from.
    expect(screen.getByTestId("group-bar-technology").getAttribute("aria-label")).toBe(
      "Technology: 400 advancing, 300 declining",
    );
  });

  it("reserves one box for loading and for the answer", async () => {
    let release: ((response: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const fetchMock = vi.fn(() => pending);
    vi.stubGlobal("fetch", fetchMock);

    render(<GroupsPage />);

    const skeleton = screen.getByTestId("groups-skeleton");
    expect(skeleton).toBeTruthy();
    expect(screen.getAllByTestId("groups-skeleton-row")).toHaveLength(GROUPS_SKELETON_ROWS);
    const frameWhileLoading = screen.getByTestId("groups-frame");
    expect(frameWhileLoading.style.minHeight).toBe(`${GROUPS_RESERVED_HEIGHT}px`);
    expect(GROUPS_RESERVED_HEIGHT).toBeGreaterThan(GROUPS_SKELETON_ROWS * 20);

    release?.(json(groupsDocument()));
    await screen.findByTestId("groups-table");
    expect(screen.getByTestId("groups-frame").style.minHeight).toBe(
      `${GROUPS_RESERVED_HEIGHT}px`,
    );
    expect(screen.queryByTestId("groups-skeleton")).toBeNull();
  });

  it("says the table is empty instead of drawing an unexplained blank", async () => {
    stub(groupsDocument({ groups: [], universe_total: 0 }));
    render(<GroupsPage />);

    const empty = await screen.findByTestId("groups-empty");
    expect(empty.textContent).toMatch(/no sector groups/i);
    expect(screen.queryByTestId("groups-table")).toBeNull();
    expect(screen.queryAllByTestId(/^group-row-/)).toHaveLength(0);
  });

  it("keeps a failure actionable: it says so and retries on demand", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ errorMessage: "upstream exploded" }, 502))
      .mockResolvedValueOnce(json(groupsDocument()));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<GroupsPage />);

    const failure = await screen.findByTestId("groups-error");
    expect(failure.getAttribute("role")).toBe("alert");
    expect(failure.textContent).toMatch(/did not load/i);
    expect(failure.textContent).toContain("upstream exploded");

    await user.click(screen.getByRole("button", { name: /retry/i }));
    await screen.findByTestId("groups-table");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps the server's bookkeeping out of the copy", async () => {
    stub();
    render(<GroupsPage />);
    await screen.findByTestId("groups-table");

    const page = screen.getByTestId("groups-page");
    const text = page.textContent ?? "";
    expect(text).not.toMatch(/as_of|as of/i);
    expect(text).not.toContain("2026-11-02T14:30:00Z");
    expect(text).not.toMatch(/\b(finviz|provider|source|region)\b/i);
  });
});
