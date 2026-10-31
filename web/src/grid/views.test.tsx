import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app";
import { resetFilterSchemaCache } from "@/filters/schema";
import { filterSchemaWire, VIEW_NAMES } from "@/filters/__fixtures__/schema";
import { LIVE_BREADTH } from "@/breadth/__fixtures__/breadth";

import { COLUMN_CAP } from "./columnSelection";

/**
 * The view model, proved at the shell.
 *
 * Four promises, each one an audit finding or a task requirement:
 *
 * * a view switch is **one** request, at page 1 — never the new view at the
 *   previous view's offset followed by a correction;
 * * every view name, label and column list comes from the served catalogue —
 *   the strip renders what the schema said, including a renamed view;
 * * a custom column selection round-trips through the URL, is scoped to the
 *   view it was built for, and is capped at 40 with a sentence naming the cap;
 * * nothing in `web/src` spells a view name or a column list.
 */

interface Body {
  columns: string[];
  criteria: { offset: number; limit: number; sort?: string; filters: unknown[] };
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function pageFor(columns: readonly string[], limit: number) {
  return {
    as_of: "2026-10-31T01:28:01Z",
    limit,
    total: 5_600,
    universe_total: 5_600,
    returned: limit,
    columns: [...columns],
    rows: Array.from({ length: limit }, (_, index) => {
      const row: Record<string, unknown> = { symbol: `S${index}` };
      for (const key of columns) row[key] = index;
      return row;
    }),
    budget: { truncated: false, dropped_fields: [] },
  };
}

function stubApi(schema: Record<string, unknown> = filterSchemaWire()) {
  const selects: Body[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/screens/select/schema") return json(schema);
    if (url.startsWith("/market/breadth")) return json(LIVE_BREADTH);
    if (url === "/health") {
      return json({
        status: "ok",
        version: "0.1.0",
        checked_at: "2026-10-31T01:28:01Z",
        elapsed_ms: 1,
        startup_error: null,
        checks: {},
      });
    }
    if (url === "/screens/select" && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as Body;
      selects.push(body);
      return json(pageFor(body.columns, body.criteria.limit));
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { selects, fetchMock };
}

const todayColumns = (VIEW_NAMES.find(([name]) => name === "today")?.[3] ?? 0) as number;
const TODAY_KEYS = Array.from({ length: todayColumns }, (_, index) => `column_${index}`);

beforeEach(() => {
  resetFilterSchemaCache();
  window.history.replaceState(null, "", "/app/");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a view switch", () => {
  it("issues exactly one request, for the new view, at the first page", async () => {
    const api = stubApi();
    window.history.replaceState(null, "", "/app/?view=today&page=3");
    const user = userEvent.setup();

    render(<App />);
    await waitFor(
      () => expect(screen.getByTestId("range").textContent).toBe("201–300 of 5,600"),
      { timeout: 3_000 },
    );

    await user.click(screen.getByRole("tab", { name: /^Value/ }));
    const valueRequests = () => api.selects.filter((body) => body.columns.length === 13);
    await waitFor(() => expect(valueRequests()).toHaveLength(1));
    // Give a second, superseded request every chance to appear.
    await new Promise((resolve) => setTimeout(resolve, 120));

    // One request for the new view — a late-recorded answer for the *previous*
    // view is not a second request for this one.
    expect(valueRequests()).toHaveLength(1);
    expect(api.selects[api.selects.length - 1]?.columns).toEqual(
      Array.from({ length: 13 }, (_, index) => `column_${index}`),
    );
    expect(api.selects[api.selects.length - 1]?.criteria.offset).toBe(0);
    await waitFor(() => expect(new URLSearchParams(window.location.search).get("page")).toBe("1"));
  });
});

describe("the catalogue is the only source of views and columns", () => {
  it("follows a renamed view instead of a name this build knows", async () => {
    const wire = filterSchemaWire();
    wire["views"] = [
      { name: "steady", label: "Steady", job: "Nothing moved", columns: ["column_0"] },
      { name: "movers", label: "Movers", job: "What moved?", columns: ["column_3", "column_4"] },
      {
        name: "everything",
        label: "Every key",
        job: "The whole catalogue",
        columns: Array.from({ length: 116 }, (_, index) => `column_${index}`),
      },
    ];
    const api = stubApi(wire);
    const user = userEvent.setup();

    render(<App />);
    await waitFor(() => expect(screen.getByTestId("range")).toBeTruthy(), { timeout: 3_000 });

    // The tab strip is the served catalogue: a renamed view is renamed...
    const tab = screen.getByRole("tab", { name: /^Movers/ });
    expect(screen.queryByRole("tab", { name: /^Today/ })).toBeNull();
    expect(screen.queryByRole("tab", { name: /^Value/ })).toBeNull();

    // ...and its columns are the ones the served catalogue gives it.
    await user.click(tab);
    const movers = () => api.selects.filter((body) => body.columns.join() === "column_3,column_4");
    await waitFor(() => expect(movers()).toHaveLength(1));
    expect(movers()[0]?.columns).toEqual(["column_3", "column_4"]);

    // The wide destination is reckoned by width, not by a name it was told.
    const destination = screen.getByTestId("all-columns-toggle");
    expect(destination.textContent).toContain("Every key");
    expect(destination.textContent).toContain("116");
    expect(destination.getAttribute("role")).not.toBe("tab");
  });

  it("spells no view name and no column list anywhere in the screener surface", () => {
    const root = path.resolve(process.cwd(), "src");
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir)) {
        const full = path.join(dir, entry);
        if (statSync(full).isDirectory()) {
          // `detail/` and `breadth/` are other surfaces with vocabularies of
          // their own (`growth`, `quality`, `income` are detail metric groups,
          // not screener views). This assertion is about the code that draws
          // the view strip and builds the request — which is exactly the code
          // that must not know a view's name.
          if (entry === "detail" || entry === "breadth") continue;
          walk(full);
          continue;
        }
        if (!/\.(ts|tsx)$/.test(entry)) continue;
        if (/\.test\.(ts|tsx)$/.test(entry)) continue;
        if (full.includes(`${path.sep}__fixtures__${path.sep}`)) continue;
        files.push(full);
      }
    };
    walk(root);
    expect(files.length).toBeGreaterThan(10);

    // The names the server serves at this commit. A test may know them; the
    // client may not — that is the whole point of the served catalogue.
    const names = VIEW_NAMES.map(([name]) => name);
    expect(names).toContain("today");

    const quoted: Array<{ file: string; name: string }> = [];
    for (const file of files) {
      const text = readFileSync(file, "utf8");
      for (const name of names) {
        const literal = new RegExp(`["']${name}["']`);
        if (literal.test(text)) quoted.push({ file: path.relative(root, file), name });
      }
    }
    expect(quoted).toEqual([]);

    // And nothing constructs a view by filtering on its name.
    const app = readFileSync(path.join(root, "app.tsx"), "utf8");
    expect(app).not.toMatch(/\.name\s*!==?\s*["']/);
    expect(app).not.toMatch(/\.name\s*===?\s*["']/);

    // The grid never carries a column list of its own: two quoted snake_case
    // keys in one array literal would be exactly that.
    const lists: string[] = [];
    const listPattern = /\[\s*"[a-z][a-z0-9_]*"\s*,\s*"[a-z][a-z0-9_]*"/g;
    for (const file of files) {
      if (!file.includes(`${path.sep}grid${path.sep}`) && !file.endsWith("app.tsx")) continue;
      if (listPattern.test(readFileSync(file, "utf8"))) lists.push(path.relative(root, file));
    }
    expect(lists).toEqual([]);
  });
});

describe("a custom column selection", () => {
  const custom = Array.from({ length: 12 }, (_, index) => `column_${100 + index}`);
  const colsParam = `today:${custom.join(",")}`;

  it("round-trips through the URL and is the only thing requested", async () => {
    const api = stubApi();
    window.history.replaceState(null, "", `/app/?view=today&cols=${encodeURIComponent(colsParam)}`);

    const first = render(<App />);
    await waitFor(() => expect(api.selects.length).toBe(1), { timeout: 3_000 });

    expect(api.selects[0]?.columns).toEqual(custom);
    expect(screen.getByTestId("column-count").textContent).toContain("12 columns · custom");
    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get("cols")).toBe(colsParam),
    );

    // A reload is a fresh mount against the same address bar.
    first.unmount();
    resetFilterSchemaCache();
    api.selects.length = 0;
    render(<App />);
    await waitFor(() => expect(api.selects.length).toBe(1), { timeout: 3_000 });
    expect(api.selects[0]?.columns).toEqual(custom);
  });

  it("stays with its view and does not apply to the next one", async () => {
    const api = stubApi();
    window.history.replaceState(null, "", `/app/?view=today&cols=${encodeURIComponent(colsParam)}`);
    const user = userEvent.setup();

    render(<App />);
    await waitFor(() => expect(api.selects.length).toBe(1), { timeout: 3_000 });
    expect(api.selects[0]?.columns).toEqual(custom);

    await user.click(screen.getByRole("tab", { name: /^Value/ }));
    await waitFor(() => expect(api.selects.length).toBe(2));

    // Value's own 13 columns — not today's 12-key selection.
    expect(api.selects[1]?.columns).toEqual(
      Array.from({ length: 13 }, (_, index) => `column_${index}`),
    );
    expect(api.selects[1]?.columns).not.toEqual(custom);
    expect(screen.getByTestId("column-count").textContent).not.toContain("custom");
    // The selection is still in the address bar, waiting for its view.
    expect(new URLSearchParams(window.location.search).get("cols")).toBe(colsParam);
  });

  it("trims a 200-name link to 40 with a sentence naming the cap, and sends 40", async () => {
    const api = stubApi();
    const twoHundred = Array.from({ length: 200 }, (_, index) => `column_${index}`).join(",");
    window.history.replaceState(
      null,
      "",
      `/app/?view=today&cols=${encodeURIComponent(`today:${twoHundred}`)}`,
    );

    render(<App />);
    await waitFor(() => expect(api.selects.length).toBe(1), { timeout: 3_000 });

    expect(api.selects[0]?.columns).toHaveLength(COLUMN_CAP);
    expect(api.selects.every((body) => body.columns.length <= COLUMN_CAP)).toBe(true);
    expect(screen.getByTestId("column-notice").textContent).toMatch(/at most 40/);
    expect(screen.getByTestId("column-notice").textContent).toMatch(/200 columns/);
  });

  it("refuses the 41st key with the cap named and does not touch the request", async () => {
    const api = stubApi();
    const forty = Array.from({ length: 40 }, (_, index) => `column_${index}`);
    window.history.replaceState(
      null,
      "",
      `/app/?view=today&cols=${encodeURIComponent(`today:${forty.join(",")}`)}`,
    );
    const user = userEvent.setup();

    render(<App />);
    await waitFor(() => expect(api.selects.length).toBe(1), { timeout: 3_000 });
    const before = api.selects.length;

    await user.click(screen.getByTestId("columns-button"));
    const picker = await screen.findByTestId("column-picker");
    expect(within(picker).getByTestId("column-picker-count").textContent).toBe("40 / 40");

    const free = within(picker).getByRole("checkbox", { name: /^Column 100 \(column_100\)/ });
    await user.click(free);

    const notice = await screen.findByTestId("column-picker-notice");
    expect(notice.textContent).toMatch(/at most 40/);
    expect(api.selects.length).toBe(before);
    expect(screen.getByTestId("column-count").textContent).toContain("40 columns");
  });
});

describe("the all-columns destination", () => {
  it("is not a tab, states what it is, and fetches 116 keys in three requests", async () => {
    const api = stubApi();
    const user = userEvent.setup();

    render(<App />);
    await waitFor(() => expect(screen.getByTestId("range")).toBeTruthy(), { timeout: 3_000 });

    const toggle = screen.getByTestId("all-columns-toggle");
    expect(toggle.getAttribute("role")).not.toBe("tab");
    expect(screen.queryByRole("tabpanel")).not.toBeNull();

    const wide = () => api.selects.filter((body) => body.criteria.limit === 20);

    await user.click(toggle);

    await waitFor(() => expect(wide()).toHaveLength(3));
    expect(screen.getByTestId("all-columns-title").textContent).toBe(
      "All columns — 116, wide view, 20 rows",
    );
    // No longer pretending to be the tabpanel.
    expect(screen.queryByRole("tabpanel")).toBeNull();
    expect(screen.getByRole("region", { name: /wide view/ })).toBeTruthy();

    const asked = wide().flatMap((body) => body.columns);
    expect(new Set(asked).size).toBe(116);
    for (const body of wide()) {
      expect(body.columns.length).toBeLessThanOrEqual(40);
      expect(body.criteria.limit).toBe(20);
      expect(body.criteria.offset).toBe(0);
    }
    // The wide table itself (117 header cells, sticky lane, scroll) is proved
    // in `allColumns.test.tsx`, where the container width is a seam; jsdom
    // reports every box as 0 px, so the shell cannot draw one here.
    expect(screen.getByTestId("all-columns-requests").textContent).toContain("40 + 40 + 36");
  });
});

describe("the picker", () => {
  it("offers every catalogue key and adds one to the current view", async () => {
    const api = stubApi();
    const user = userEvent.setup();

    render(<App />);
    await waitFor(() => expect(api.selects.length).toBe(1), { timeout: 3_000 });

    await user.click(screen.getByTestId("columns-button"));
    const picker = await screen.findByTestId("column-picker");
    expect(within(picker).getByTestId("column-picker-count").textContent).toBe(`${TODAY_KEYS.length} / 40`);

    await user.type(within(picker).getByRole("searchbox", { name: "Find a column" }), "column_100");
    await user.click(within(picker).getByRole("checkbox", { name: /column_100/ }));

    const extended = () => api.selects.filter((body) => body.columns.includes("column_100"));
    await waitFor(() => expect(extended()).toHaveLength(1));
    expect(extended()[0]?.columns).toHaveLength(TODAY_KEYS.length + 1);
    expect(extended()[0]?.columns).toContain("column_100");
    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get("cols")).toContain("column_100"),
    );
  });
});