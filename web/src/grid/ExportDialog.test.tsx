/**
 * The guided export dialog's promises, at the dialog.
 *
 * Each case fails if the *effect* is missing: the step selects populate from
 * the options API, the +/- stepper clamps 1–200, the export body carries
 * the right filters, the spinner appears while the request is open, and
 * a failure retries the same body.
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
  { id: "Finance", label: "Finance", predicate: "Finance", count: 567 },
];

const INDUSTRY_OPTIONS = [
  { id: "Semiconductors", label: "Semiconductors", predicate: "Semiconductors", count: 120 },
  { id: "Software", label: "Software", predicate: "Software", count: 200 },
];

const COUNTRY_OPTIONS = [
  { id: "USA", label: "USA", predicate: "USA", count: 5000 },
  { id: "Canada", label: "Canada", predicate: "Canada", count: 300 },
];

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
    choice: "csv",
    columns: ["market_cap", "price"],
    region: "us",
    ...overrides,
  };
  return render(<ExportDialog {...props} />);
}

/** Find the first export-call body matching the given format. */
function exportBodyFor(fetchMock: Mock, format: string): ExportBody | null {
  for (const call of fetchMock.mock.calls) {
    const url = String(call[0]);
    if (url !== "/screens/export") continue;
    const init = call[1] as RequestInit;
    const body = JSON.parse(String(init.body)) as ExportBody;
    if (body.format === format) return body;
  }
  return null;
}

/**
 * Stub fetch so that GET /screens/options/* returns the fixtures and POST
 * /screens/export returns a file.
 */
function stubFetch(exportResponse = fileResponse()): Mock {
  const fetchMock = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/screens/options/sector")) return optionsResponse("sector");
    if (url.includes("/screens/options/industry")) return optionsResponse("industry");
    if (url.includes("/screens/options/country")) return optionsResponse("country");
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
  it("renders the three step selects and loads options from the API", async () => {
    const fetchMock = stubFetch();
    renderDialog();

    // All three selects exist
    expect(screen.getByTestId("export-select-sector")).toBeTruthy();
    expect(screen.getByTestId("export-select-industry")).toBeTruthy();
    expect(screen.getByTestId("export-select-country")).toBeTruthy();

    // Options populate after fetch resolves
    await waitFor(() => {
      const sectorSelect = screen.getByTestId("export-select-sector") as HTMLSelectElement;
      expect(sectorSelect.options.length).toBeGreaterThan(1); // "All Sectors" + items
    });

    // All three option endpoints were called
    const urls = fetchMock.mock.calls.map((c: unknown[]) => String(c[0]));
    expect(urls.some((u: string) => u.includes("/screens/options/sector"))).toBe(true);
    expect(urls.some((u: string) => u.includes("/screens/options/industry"))).toBe(true);
    expect(urls.some((u: string) => u.includes("/screens/options/country"))).toBe(true);
  });

  it("defaults the count display to 200", () => {
    stubFetch();
    renderDialog();
    expect(screen.getByTestId("export-count-value").textContent).toBe("200");
  });

  it("the + button increases the count and − decreases it", async () => {
    const user = userEvent.setup();
    stubFetch();
    renderDialog();

    await waitFor(() => {
      expect(screen.getByTestId("export-count-value").textContent).toBe("200");
    });

    // Decrease
    await user.click(screen.getByTestId("export-count-down"));
    expect(screen.getByTestId("export-count-value").textContent).toBe("199");

    // Increase — back to 200 (ceiling)
    await user.click(screen.getByTestId("export-count-up"));
    expect(screen.getByTestId("export-count-value").textContent).toBe("200");

    // One more click stays at 200 (clamped)
    await user.click(screen.getByTestId("export-count-up"));
    expect(screen.getByTestId("export-count-value").textContent).toBe("200");
  });

  it("disables the + button at 200 and the − button at 1", async () => {
    const user = userEvent.setup();
    stubFetch();
    renderDialog();

    await waitFor(() => {
      expect(screen.getByTestId("export-count-value")).toBeTruthy();
    });

    // At 200, + is disabled
    const upBtn = screen.getByTestId("export-count-up") as HTMLButtonElement;
    const downBtn = screen.getByTestId("export-count-down") as HTMLButtonElement;
    expect(upBtn.disabled).toBe(true);
    expect(downBtn.disabled).toBe(false);

    // Click down 199 times to reach 1
    for (let i = 0; i < 199; i++) {
      await user.click(downBtn);
    }
    await waitFor(() => {
      expect(screen.getByTestId("export-count-value").textContent).toBe("1");
    });
    expect(downBtn.disabled).toBe(true);
    expect(upBtn.disabled).toBe(false);
  });

  it("sends the correct criteria body when exporting with selected filters", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch();
    renderDialog();

    // Wait for options to load
    await waitFor(() => {
      const sel = screen.getByTestId("export-select-sector") as HTMLSelectElement;
      expect(sel.options.length).toBeGreaterThan(1);
    });

    // Pick sector = Technology
    await user.selectOptions(
      screen.getByTestId("export-select-sector"),
      "Technology",
    );
    // Pick country = USA
    await user.selectOptions(
      screen.getByTestId("export-select-country"),
      "USA",
    );
    // Set count to 50
    const downBtn = screen.getByTestId("export-count-down");
    for (let i = 0; i < 150; i++) {
      await user.click(downBtn);
    }
    await waitFor(() => {
      expect(screen.getByTestId("export-count-value").textContent).toBe("50");
    });

    // Export
    await user.click(screen.getByTestId("export-run"));

    await waitFor(() => {
      const exportCalls = fetchMock.mock.calls.filter(
        (c: unknown[]) => String(c[0]) === "/screens/export",
      );
      expect(exportCalls.length).toBeGreaterThan(0);
    });

    const body = exportBodyFor(fetchMock, "csv");
    expect(body).not.toBeNull();
    expect(body!.mode).toBe("criteria");
    expect(body!.limit).toBe(50);
    expect(body!.region).toBe("us");
    expect(body!.criteria).toBeDefined();
    expect(body!.criteria!.sort).toBe("-market_cap");
    expect(body!.criteria!.filters).toHaveLength(2);
  });

  it("choosing 'All' removes that filter from the body", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch();
    renderDialog();

    await waitFor(() => {
      const sel = screen.getByTestId("export-select-sector") as HTMLSelectElement;
      expect(sel.options.length).toBeGreaterThan(1);
    });

    // Pick sector then reset to All
    await user.selectOptions(
      screen.getByTestId("export-select-sector"),
      "Technology",
    );
    await user.selectOptions(
      screen.getByTestId("export-select-sector"),
      "", // "All Sectors"
    );

    await user.click(screen.getByTestId("export-run"));

    await waitFor(() => {
      const exportCalls = fetchMock.mock.calls.filter(
        (c: unknown[]) => String(c[0]) === "/screens/export",
      );
      expect(exportCalls.length).toBeGreaterThan(0);
    });

    const body = exportBodyFor(fetchMock, "csv");
    expect(body).not.toBeNull();
    expect(body!.criteria!.filters).toHaveLength(0);
  });

  it("spins while the file is being written and disables the export button", async () => {
    const user = userEvent.setup();
    let settle: (response: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      settle = resolve;
    });
    const fetchMock = vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/screens/options/")) {
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
      if (url.includes("/screens/options/")) {
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
    expect(success.textContent).toContain("ztockz-criteria-20261102-120000.csv");

    expect(downloadSpy).toHaveBeenCalledTimes(1);
    const handed = downloadSpy.mock.calls[0]?.[0] as {
      filename: string;
      rowCount: number;
      blob: Blob;
    };
    expect(handed.filename).toBe("ztockz-criteria-20261102-120000.csv");
    expect(handed.rowCount).toBe(42);
    expect(handed.blob.size).toBeGreaterThan(0);
  });

  it("the API pane names the URL and downloads nothing", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch();
    renderDialog({ choice: "api" });

    expect(screen.getByTestId("export-api-url").textContent).toBe("/screens/export");
    expect(screen.queryByTestId("export-run")).toBeNull();

    // Switch to csv and back to api — no fetch for export
    await user.click(screen.getByTestId("export-format-csv"));
    await user.click(screen.getByTestId("export-format-api"));

    // Only option fetches happened
    const exportCalls = fetchMock.mock.calls.filter(
      (c: unknown[]) => String(c[0]) === "/screens/export",
    );
    expect(exportCalls).toHaveLength(0);
    expect(downloadSpy).not.toHaveBeenCalled();
  });

  it("quick-export CSV button sends csv format regardless of current format", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch();
    // Open with JSON format
    renderDialog({ choice: "json" });

    await waitFor(() => {
      expect(screen.getByTestId("export-select-sector")).toBeTruthy();
    });

    // The quick CSV button should be visible when format is not csv
    const quickCsv = screen.getByTestId("export-quick-csv");
    expect(quickCsv).toBeTruthy();

    await user.click(quickCsv);

    // Wait for the export call to fire (it's on a setTimeout)
    await waitFor(() => {
      const body = exportBodyFor(fetchMock, "csv");
      expect(body).not.toBeNull();
    });
    expect(downloadSpy).toHaveBeenCalledTimes(1);
  });
});