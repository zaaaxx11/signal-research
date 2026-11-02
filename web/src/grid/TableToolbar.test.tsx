import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ViewDefinition } from "@/data/screens";

import { ScreenPanel } from "./ScreenPanel";

/**
 * The toolbar and the pager, at the panel: the count readout, Refresh, the
 * auto-refresh cadence, the export menu, and the page arithmetic.
 *
 * These are the promises a reader makes about the chrome without thinking:
 * "the number is the filtered total", "refresh asks once", "the menu really
 * has four formats", "the pager does not print 281 buttons". Each one is
 * asserted against the request that goes out or the DOM that is drawn.
 */

const VIEW: ViewDefinition = {
  name: "today",
  label: "Today",
  job: "What is moving right now?",
  columns: ["price", "change", "volume"],
};

function answer(count: number, total: number) {
  return new Response(
    JSON.stringify({
      as_of: "2026-11-01T10:00:00Z",
      limit: 20,
      total,
      universe_total: 5_600,
      returned: count,
      columns: VIEW.columns,
      rows: Array.from({ length: count }, (_, index) => ({
        symbol: `S${String(index).padStart(3, "0")}`,
        price: 100 + index,
        change: 1,
        volume: 1_000 * (index + 1),
      })),
      budget: { truncated: false, dropped_fields: [] },
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

function bodyOf(call: unknown): { criteria: { offset: number; limit: number } } {
  const [, init] = call as [string, RequestInit];
  return JSON.parse(String(init.body));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderPanel() {
  return render(<ScreenPanel view={VIEW} onActivate={vi.fn()} containerWidth={1280} />);
}

describe("the count readout", () => {
  it("prints the page position over the filtered total, not the page size", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(20, 5_600)));
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));
  });

  it("follows a filtered total the API reports", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(20, 137)));
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/137 Total"));
    // 137 rows at 20 a page is seven pages — the readout and the pager agree.
    expect(screen.getByTestId("page-7")).toBeTruthy();
    expect(screen.queryByTestId("page-8")).toBeNull();
  });

  it("says nothing rather than zero when the answer has no rows", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(0, 0)));
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("—"));
  });
});

describe("Refresh", () => {
  it("issues exactly one request per press", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(answer(20, 5_600))
      .mockResolvedValue(answer(20, 5_600));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPanel();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "Refresh the screen" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    // The button is disabled while the request is out: a double click cannot
    // queue a second ask for the same page.
    await user.click(screen.getByRole("button", { name: "Refresh the screen" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(bodyOf(fetchMock.mock.calls[2]).criteria.offset).toBe(0);
    expect(bodyOf(fetchMock.mock.calls[2]).criteria.limit).toBe(20);
  });
});

describe("the auto-refresh select", () => {
  it("offers Off and a five-minute cadence, and nothing invented", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(20, 5_600)));
    renderPanel();

    const select = (await screen.findByTestId("auto-refresh")) as HTMLSelectElement;
    expect([...select.options].map((option) => option.textContent)).toEqual(["Off", "Every 5 min"]);
    expect([...select.options].map((option) => option.value)).toEqual(["0", "300000"]);
    expect(select.value).toBe("0");
    expect(screen.queryByTestId("auto-refresh-live")).toBeNull();
  });

  it("shows that the timer is live once a cadence is chosen", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(20, 5_600)));
    const user = userEvent.setup();
    renderPanel();

    const select = (await screen.findByTestId("auto-refresh")) as HTMLSelectElement;
    await user.selectOptions(select, "300000");

    expect(select.value).toBe("300000");
    expect(screen.getByTestId("auto-refresh-live").textContent).toContain("live");
    expect(screen.getByTestId("toolbar-notice").textContent).toContain("Auto refresh every 5 min");
  });
});

describe("the export menu", () => {
  it("lists CSV, XLSX, JSON and API, and closes on Escape", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(20, 5_600)));
    const user = userEvent.setup();
    renderPanel();

    const button = await screen.findByTestId("export-button");
    expect(button.getAttribute("aria-expanded")).toBe("false");
    await user.click(button);

    const menu = screen.getByTestId("export-menu");
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "CSV",
      "XLSX",
      "JSON",
      "API",
    ]);

    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("export-menu")).toBeNull();
    expect(button.getAttribute("aria-expanded")).toBe("false");
  });

  it("opens the export dialog for the format that was chosen", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(20, 5_600)));
    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByTestId("export-button"));
    await user.click(within(screen.getByTestId("export-menu")).getByRole("menuitem", { name: "XLSX" }));

    const dialog = await screen.findByRole("dialog", { name: /Export/i });
    expect(dialog.textContent?.toLowerCase()).toContain("xlsx");
    // The menu is gone once a choice was made: it acts, it does not linger.
    expect(screen.queryByTestId("export-menu")).toBeNull();
  });
});

describe("the pager", () => {
  it("truncates 281 pages to a window instead of printing every button", async () => {
    // 5 620 rows at 20 a page is 281 pages — the shape the requirement names.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(20, 5_620)));
    const user = userEvent.setup();
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("page-1")).toBeTruthy());
    await user.type(await screen.findByTestId("go-to-page"), "5{Enter}");

    const pager = screen.getByTestId("pager");
    await waitFor(() => expect(within(pager).getByTestId("page-5").getAttribute("aria-current")).toBe("page"));
    const numbers = [...pager.querySelectorAll("button[data-testid^='page-']")].map((button) =>
      button.getAttribute("data-testid"),
    );
    expect(numbers).toEqual(["page-1", "page-4", "page-5", "page-6", "page-281"]);
    // Two elisions: one after the first page, one before the last.
    expect([...pager.querySelectorAll("[aria-hidden='true']")].filter((node) => node.textContent === "…")).toHaveLength(
      2,
    );
  });

  it("navigates on a typed page, and clamps one that cannot exist", async () => {
    const fetchMock = vi.fn().mockResolvedValue(answer(20, 5_600));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("page-1")).toBeTruthy());

    await user.type(screen.getByTestId("go-to-page"), "999{Enter}");
    await waitFor(() => expect(screen.getByTestId("page-280").getAttribute("aria-current")).toBe("page"));
    // The last page, at the offset the API expects — 279 zero-based pages.
    expect(bodyOf(fetchMock.mock.calls.at(-1)).criteria.offset).toBe(279 * 20);

    await user.type(screen.getByTestId("go-to-page"), "3{Enter}");
    await waitFor(() => expect(screen.getByTestId("page-3").getAttribute("aria-current")).toBe("page"));
    expect(bodyOf(fetchMock.mock.calls.at(-1)).criteria.offset).toBe(2 * 20);
  });

  it("does not fire for a page already on screen", async () => {
    const fetchMock = vi.fn().mockResolvedValue(answer(20, 5_600));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("page-1")).toBeTruthy());
    const before = fetchMock.mock.calls.length;

    await user.type(screen.getByTestId("go-to-page"), "1{Enter}");
    // No request: page 1 is where the reader already is.
    expect(fetchMock.mock.calls.length).toBe(before);
  });
});

describe("the chrome the toolbar replaced", () => {
  it("has no view label, no FILTERS/COLUMNS/SECTOR controls and no exchange menu", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(20, 5_600)));
    renderPanel();

    const toolbar = await screen.findByTestId("table-toolbar");
    // The toolbar says counts, refresh, actions, export and paging — not which
    // view is on screen (the masthead's tabs say that) and not filters.
    expect(toolbar.textContent).not.toContain("Today");
    for (const legacy of ["Filters", "Columns", "Sector", "Exchange"]) {
      expect(within(toolbar).queryByRole("button", { name: new RegExp(`^${legacy}`, "i") })).toBeNull();
    }
    expect(screen.queryByTestId("exchange-trigger")).toBeNull();
    expect(screen.queryByTestId("sector-source")).toBeNull();
    expect(screen.queryByTestId("control-chip-bar")).toBeNull();
  });
});
