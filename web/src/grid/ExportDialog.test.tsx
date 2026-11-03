/**
 * The guided export dialog's promises, at the dialog.
 *
 * Each case fails if the *effect* is missing: the five step selects populate
 * from the options API or the screener's own filter document, the +/- stepper
 * clamps 1–200, the export body carries the right filters, and a failure
 * retries the same body.
 */

import type { ComponentProps } from "react";

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

const downloadSpy = vi.hoisted(() => vi.fn());

vi.mock("./exportJob", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./exportJob")>();
  return { ...actual, downloadFile: downloadSpy };
});

import { ExportDialog } from "./ExportDialog";
import type { ExportBody } from "./exportJob";

// ---------------------------------------------------------------------------
// Option fixtures
// ---------------------------------------------------------------------------

const SECTOR_OPTIONS = [
  { id: "Technology", label: "Technology", predicate: "Technology", count: 1234 },
  { id: "Healthcare", label: "Healthcare", predicate: "Healthcare", count: 890 },
];

const INDUSTRY_OPTIONS = [
  { id: "Semiconductors", label: "Semiconductors", predicate: "Semiconductors", count: 120 },
];

const COUNTRY_OPTIONS = [
  { id: "USA", label: "USA", predicate: "USA", count: 5000 },
];

const ORDER_OPTIONS = [
  { id: "market_cap", label: "market_cap", predicate: "market_cap", count: 0 },
  { id: "pe_ttm", label: "pe_ttm", predicate: "pe_ttm", count: 0 },
];

const SIGNAL_OPTIONS = [
  { id: "top-gainers", label: "Top Gainers", predicate: "top-gainers", count: 0 },
];

/** The response GET /screens/options/{key} or GET /screens/filters answers. */
function optionsResponse(key: string) {
  const map: Record<string, typeof SECTOR_OPTIONS> = {
    sector: SECTOR_OPTIONS,
    industry: INDUSTRY_OPTIONS,
    country: COUNTRY_OPTIONS,
  };
  return new Response(
    JSON.stringify({ key, options: map[key] ?? [], as_of: "2026-11-03T10:00:00Z" }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

/** The response GET /screens/filters answers (for order and signal). */
function filtersResponse(): Response {
  return new Response(
    JSON.stringify({
      schema_version: "screener-3",
      groups: [
        {
          id: "descriptive",
          label: "Descriptive",
          controls: [
            {
              id: "order",
              label: "Order by",
              options: ORDER_OPTIONS,
            },
            {
              id: "signal",
              label: "Signal",
              options: SIGNAL_OPTIONS,
            },
          ],
        },
      ],
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

function fileResponse(rows = 200): Response {
  return new Response("symbol,name\nAAPL,Apple Inc\n", {
    status: 200,
    headers: {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition": 'attachment; filename="ztockz-criteria-20261102-120000.csv"',
      "x-row-count": String(rows),
    },
  });
}

function failureResponse(): Response {
  return new Response(
    JSON.stringify({
      error: "export_limit_exceeded",
      errorMessage: "limit 500 exceeds the export cap of 200 rows",
    }),
    { status: 422, headers: { "content-type": "application/json" } },
  );
}

function renderDialog(overrides: Partial<ComponentProps<typeof ExportDialog>> = {}) {
  const props: ComponentProps<typeof ExportDialog> = {
    open: true,
    onClose: vi.fn(),
    columns: ["market_cap", "price"],
    region: "us",
    ...overrides,
  };
  return render(<ExportDialog {...props} />);
}

/** Find the first export-call body. */
function exportBody(fetchMock: Mock): ExportBody | null {
  for (const call of fetchMock.mock.calls) {
    const url = String(call[0]);
    if (url !== "/screens/export") continue;
    const init = call[1] as RequestInit;
    return JSON.parse(String(init.body)) as ExportBody;
  }
  return null;
}

/** Stub fetch for options + /screens/filters + export. */
function stubFetch(exportResponse = fileResponse()): Mock {
  const fetchMock = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/screens/options/sector")) return optionsResponse("sector");
    if (url.includes("/screens/options/industry")) return optionsResponse("industry");
    if (url.includes("/screens/options/country")) return optionsResponse("country");
    if (url === "/screens/filters") return filtersResponse();
    if (url === "/screens/export" && init?.method === "POST") return exportResponse;
    return new Response("not found", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  downloadSpy.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("the guided export dialog", () => {
  it("renders five selects and loads options", async () => {
    const fetchMock = stubFetch();
    renderDialog();

    expect(screen.getByTestId("export-select-sector")).toBeTruthy();
    expect(screen.getByTestId("export-select-industry")).toBeTruthy();
    expect(screen.getByTestId("export-select-country")).toBeTruthy();
    expect(screen.getByTestId("export-select-order")).toBeTruthy();
    expect(screen.getByTestId("export-select-signal")).toBeTruthy();

    // Options populate after fetch resolves
    await waitFor(() => {
      const sel = screen.getByTestId("export-select-sector") as HTMLSelectElement;
      expect(sel.options.length).toBeGreaterThan(1);
    });

    // Screener filter document was fetched for order/signal
    const urls = fetchMock.mock.calls.map((c: unknown[]) => String(c[0]));
    expect(urls.some((u: string) => u === "/screens/filters")).toBe(true);
  });

  it("defaults the count display to 200", () => {
    stubFetch();
    renderDialog();
    expect((screen.getByTestId("export-count-value") as HTMLInputElement).value).toBe("200");
  });

  it("+/- stepper increases and decreases count, clamped 1–200", async () => {
    const user = userEvent.setup();
    stubFetch();
    renderDialog();

    await waitFor(() => {
      expect(screen.getByTestId("export-count-value")).toBeTruthy();
    });

    await user.click(screen.getByTestId("export-count-down"));
    expect((screen.getByTestId("export-count-value") as HTMLInputElement).value).toBe("199");

    await user.click(screen.getByTestId("export-count-up"));
    expect((screen.getByTestId("export-count-value") as HTMLInputElement).value).toBe("200");

    // + at ceiling stays 200
    await user.click(screen.getByTestId("export-count-up"));
    expect((screen.getByTestId("export-count-value") as HTMLInputElement).value).toBe("200");
  });

  it("disables + at 200 and − at 1", async () => {
    const user = userEvent.setup();
    stubFetch();
    renderDialog();

    await waitFor(() => {
      expect(screen.getByTestId("export-count-value")).toBeTruthy();
    });

    const upBtn = screen.getByTestId("export-count-up") as HTMLButtonElement;
    const downBtn = screen.getByTestId("export-count-down") as HTMLButtonElement;
    expect(upBtn.disabled).toBe(true);
    expect(downBtn.disabled).toBe(false);

    for (let i = 0; i < 199; i++) {
      await user.click(downBtn);
    }
    await waitFor(() => {
      expect((screen.getByTestId("export-count-value") as HTMLInputElement).value).toBe("1");
    });
    expect(downBtn.disabled).toBe(true);
  });

  it("sends criteria body with selected filters, sort and limit", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch();
    renderDialog();

    await waitFor(() => {
      const sel = screen.getByTestId("export-select-sector") as HTMLSelectElement;
      expect(sel.options.length).toBeGreaterThan(1);
    });

    await user.selectOptions(screen.getByTestId("export-select-sector"), "Technology");
    await user.selectOptions(screen.getByTestId("export-select-country"), "USA");
    await user.selectOptions(screen.getByTestId("export-select-order"), "pe_ttm");
    await user.selectOptions(screen.getByTestId("export-select-signal"), "top-gainers");

    // Count to 50
    const downBtn = screen.getByTestId("export-count-down");
    for (let i = 0; i < 150; i++) {
      await user.click(downBtn);
    }
    await waitFor(() => {
      expect((screen.getByTestId("export-count-value") as HTMLInputElement).value).toBe("50");
    });

    await user.click(screen.getByTestId("export-run"));

    await waitFor(() => {
      const exportCalls = fetchMock.mock.calls.filter(
        (c: unknown[]) => String(c[0]) === "/screens/export",
      );
      expect(exportCalls.length).toBeGreaterThan(0);
    });

    const body = exportBody(fetchMock);
    expect(body).not.toBeNull();
    expect(body!.mode).toBe("criteria");
    expect(body!.limit).toBe(50);
    expect(body!.criteria!.sort).toBe("pe_ttm");
    expect(body!.signal).toBe("top-gainers");
    expect(body!.criteria!.filters).toHaveLength(2);
  });

  it("\"All\" removes that filter and leaves the rest", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch();
    renderDialog();

    await waitFor(() => {
      const sel = screen.getByTestId("export-select-sector") as HTMLSelectElement;
      expect(sel.options.length).toBeGreaterThan(1);
    });

    await user.selectOptions(screen.getByTestId("export-select-sector"), "Technology");
    await user.selectOptions(screen.getByTestId("export-select-sector"), "");

    await user.click(screen.getByTestId("export-run"));

    await waitFor(() => {
      const calls = fetchMock.mock.calls.filter(
        (c: unknown[]) => String(c[0]) === "/screens/export",
      );
      expect(calls.length).toBeGreaterThan(0);
    });

    const body = exportBody(fetchMock);
    expect(body!.criteria!.filters).toHaveLength(0);
  });

  it("shows spinner while loading and disables the button", async () => {
    const user = userEvent.setup();
    let settle: (r: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => { settle = resolve; });
    const fetchMock = vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/screens/options/") || url === "/screens/filters") {
        return optionsResponse("sector");
      }
      return pending;
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDialog();

    await waitFor(() => {
      expect(screen.getByTestId("export-select-sector")).toBeTruthy();
    });

    await user.click(screen.getByTestId("export-run"));

    expect(screen.getByTestId("export-spinner")).toBeTruthy();
    expect((screen.getByTestId("export-run") as HTMLButtonElement).disabled).toBe(true);

    settle(fileResponse(12));
    await waitFor(() => expect(screen.queryByTestId("export-spinner")).toBeNull());
    expect(screen.getByTestId("export-success")).toBeTruthy();
  });

  it("shows a failure in red and retries the same body", async () => {
    const user = userEvent.setup();
    let callCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/screens/options/") || url === "/screens/filters") {
        return optionsResponse("sector");
      }
      if (url === "/screens/export" && init?.method === "POST") {
        callCount += 1;
        if (callCount === 1) return failureResponse();
        return fileResponse(10);
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDialog();

    await waitFor(() => {
      expect(screen.getByTestId("export-select-sector")).toBeTruthy();
    });

    await user.click(screen.getByTestId("export-run"));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("export cap of 200 rows");
    expect(alert.className).toContain("border-down");
    expect(screen.getByTestId("export-retry")).toBeTruthy();
    expect(downloadSpy).not.toHaveBeenCalled();

    await user.click(screen.getByTestId("export-retry"));

    await waitFor(() => expect(screen.getByTestId("export-success")).toBeTruthy());
    expect(downloadSpy).toHaveBeenCalledTimes(1);
  });

  it("downloads the server's bytes on success and reports the file", async () => {
    const user = userEvent.setup();
    stubFetch(fileResponse(42));
    renderDialog();

    await waitFor(() => {
      expect(screen.getByTestId("export-select-sector")).toBeTruthy();
    });

    await user.click(screen.getByTestId("export-run"));

    const success = await screen.findByTestId("export-success");
    expect(success.className).toContain("text-up");
    expect(success.textContent).toContain("Exported 42 rows");

    expect(downloadSpy).toHaveBeenCalledTimes(1);
  });
});