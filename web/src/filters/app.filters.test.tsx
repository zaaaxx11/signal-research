import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app";
import { resetFilterSchemaCache } from "./schema";
import { filterSchemaWire } from "./__fixtures__/schema";
import { MAX_PAGE } from "./urlState";

/**
 * The shell's integration promises: a shared link restores the screen it
 * describes, a stale filter key is ignored with a sentence, and ⌘K lands in the
 * drawer at the chosen control.
 *
 * The schema cache is module-level on purpose (one boot read), so each test
 * resets it before stubbing fetch.
 */

function page(count: number) {
  return {
    as_of: "2026-10-31T01:28:01Z",
    limit: 20,
    total: 5_600,
    universe_total: 5_600,
    returned: count,
    columns: [],
    rows: Array.from({ length: count }, (_, index) => ({ symbol: `S${index}`, price: index })),
    budget: { truncated: false, dropped_fields: [] },
  };
}

interface Harness {
  selects: Array<Record<string, unknown>>;
}

function stubApi(): Harness {
  const selects: Array<Record<string, unknown>> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/screens/select/schema") {
      return new Response(JSON.stringify(filterSchemaWire()), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (url === "/health") {
      return new Response(
        JSON.stringify({
          status: "ok",
          version: "0.1.0",
          checked_at: "2026-10-31T01:28:01Z",
          elapsed_ms: 1,
          startup_error: null,
          checks: {},
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    if (url === "/screens/select" && init?.method === "POST") {
      selects.push(JSON.parse(String(init.body)) as Record<string, unknown>);
      return new Response(JSON.stringify(page(20)), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { selects };
}

beforeEach(() => {
  resetFilterSchemaCache();
  window.history.replaceState(null, "", "/app/screener");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the shell and its filters", () => {
  it("(e) restores a shared link, ignores a dead key, and says so", async () => {
    const api = stubApi();
    window.history.replaceState(
      null,
      "",
      "/app/screener?view=momentum&sort=gainers&page=3&f=cap%3Agte%3A2000000000&f=idx%3Aeq%3A1",
    );

    render(<App />);

    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("3/5,600 Total"));
    expect(api.selects).toHaveLength(1);
    const body = api.selects[0] as {
      criteria: { offset: number; sort: string; filters: unknown[] };
    };
    expect(body.criteria.offset).toBe(40);
    expect(body.criteria.sort).toBe("gainers");
    expect(body.criteria.filters).toEqual([{ key: "cap", op: "gte", value: 2_000_000_000 }]);

    expect(screen.getByText(/Ignored 1 filter from this link: idx/)).toBeTruthy();

    // The URL is canonicalized: the dead key is gone, the live filter stays.
    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).getAll("f")).toEqual([
        "cap:gte:2000000000",
      ]),
    );
    expect(new URLSearchParams(window.location.search).get("page")).toBe("3");
  });

  it("⌘K opens the drawers at a filter found by label, then by alias", async () => {
    stubApi();
    const user = userEvent.setup();
    render(<App />);

    await waitFor(() => expect(screen.getByTestId("range")).toBeTruthy());

    fireEvent.keyDown(window, { key: "k", metaKey: true });
    let palette = await screen.findByRole("dialog", { name: "Find a filter" });
    let input = within(palette).getByRole("combobox");
    await user.type(input, "market_cap");
    expect(within(palette).getByRole("option").textContent).toContain("alias market_cap");
    await user.keyboard("{Enter}");

    const drawer = await screen.findByRole("dialog", { name: "Filters" });
    const capSection = within(drawer).getByTestId("preset-cap");
    expect(document.activeElement).toBe(within(capSection).getByRole("button", { name: "Under $2B" }));

    await user.click(within(drawer).getByRole("button", { name: "Close filters" }));

    fireEvent.keyDown(window, { key: "k", metaKey: true });
    palette = await screen.findByRole("dialog", { name: "Find a filter" });
    input = within(palette).getByRole("combobox");
    await user.type(input, "pe");
    expect(within(palette).getAllByRole("option").length).toBeGreaterThan(0);
    await user.keyboard("{Enter}");

    const reopened = await screen.findByRole("dialog", { name: "Filters" });
    const peSection = within(reopened).getByTestId("preset-fa_pe");
    expect(document.activeElement).toBe(
      within(peSection).getByRole("button", { name: /^Under 10/ }),
    );
  });

  // -------------------------------------------------------------------------
  // F3 — a hostile page number must land on a bounded page, not pin the panel
  // -------------------------------------------------------------------------

  it("F3: page=999999999999999 is clamped, sent bounded, and reconciled to the last page", async () => {
    const api = stubApi();
    window.history.replaceState(null, "", "/app/screener?page=999999999999999");

    render(<App />);

    await waitFor(() => expect(api.selects.length).toBeGreaterThan(0));
    const firstBody = api.selects[0] as { criteria: { offset: number } };
    expect(firstBody.criteria.offset).toBeLessThanOrEqual(MAX_PAGE * 20);
    expect(Number.isSafeInteger(firstBody.criteria.offset)).toBe(true);

    // 5,600 rows at 20 a page is 280 pages: once the total is known the panel
    // moves itself to page 280 instead of showing "…999999/280" with Next dead.
    await waitFor(() =>
      expect(screen.getByTestId("range").textContent).toBe("280/5,600 Total"),
    );
    expect(screen.getByText("280/280")).toBeTruthy();
    const reconciled = api.selects.at(-1) as { criteria: { offset: number } };
    expect(reconciled.criteria.offset).toBe(5_580);
    expect(new URLSearchParams(window.location.search).get("page")).toBe("280");
    expect(new URLSearchParams(window.location.search).get("page")).not.toBe("999999999999999");
  });

  it("F3: ordinary paging and the last page still work", async () => {
    const api = stubApi();
    window.history.replaceState(null, "", "/app/screener?page=280");

    render(<App />);

    await waitFor(() => expect(screen.getByText("280/280")).toBeTruthy());
    const body = api.selects[0] as { criteria: { offset: number } };
    expect(body.criteria.offset).toBe(5_580);
    // Exactly one request: a valid last page is not "reconciled" into another.
    expect(api.selects).toHaveLength(1);
  });

  // -------------------------------------------------------------------------
  // F12 — one live region, and it stops describing a state that changed
  // -------------------------------------------------------------------------

  it("F12: the ignored-link notice is one live region, cleared by the first edit", async () => {
    stubApi();
    const user = userEvent.setup();
    window.history.replaceState(
      null,
      "",
      "/app/screener?f=idx%3Aeq%3Asp500&f=cap%3Agte%3A2000000000",
    );

    render(<App />);
    await waitFor(() => expect(screen.getByTestId("range")).toBeTruthy());

    const notices = () =>
      screen
        .queryAllByRole("alert")
        .filter((element) => /Ignored 1 filter/.test(element.textContent ?? ""));
    expect(notices()).toHaveLength(1);

    // A dialog opening must not add a second live region. The drawer is
    // reachable only through ⌘K now, so that is the path taken.
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    const palette = await screen.findByRole("dialog", { name: "Find a filter" });
    expect(notices()).toHaveLength(1);

    await user.type(within(palette).getByRole("combobox"), "market cap");
    await user.keyboard("{Enter}");

    // A bucket press is a new screen: the sentence about the old link is no
    // longer describing anything on display.
    const drawer = await screen.findByRole("dialog", { name: "Filters" });
    await user.click(within(drawer).getByRole("button", { name: "$2–10B" }));
    await waitFor(() => expect(screen.queryByText(/Ignored 1 filter/)).toBeNull());
  });
});
