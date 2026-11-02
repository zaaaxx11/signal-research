/**
 * The export dialog's promises, at the dialog.
 *
 * Each case here fails if the *effect* is missing, not if a label moved: the
 * clamp is read from the request body, the spinner from the spinner's presence
 * while the promise is open, the failure from the alert and a second request,
 * the success from a spy on the one download function and the API pane from a
 * fetch that was never made.
 */

import type { ComponentProps } from "react";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

const downloadSpy = vi.hoisted(() => vi.fn());

vi.mock("./exportJob", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./exportJob")>();
  return { ...actual, downloadFile: downloadSpy };
});

import { ExportDialog } from "./ExportDialog";
import type { ExportBody } from "./exportJob";

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
    criteria: { filters: [], sort: "market_cap" },
    columns: ["market_cap", "price"],
    region: "us",
    matched: 5_120,
    pageSymbols: ["AAPL", "MSFT"],
    selectedSymbols: ["NVDA"],
    ...overrides,
  };
  return render(<ExportDialog {...props} />);
}

function sentBody(fetchMock: Mock, call = 0): ExportBody {
  const init = fetchMock.mock.calls[call]?.[1] as RequestInit;
  return JSON.parse(String(init.body)) as ExportBody;
}

beforeEach(() => {
  downloadSpy.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the export dialog", () => {
  it("defaults to 200 rows and says how many rows the screen matches", () => {
    renderDialog();

    expect((screen.getByTestId("export-rows") as HTMLInputElement).value).toBe("200");
    expect(screen.getByTestId("export-match").textContent).toBe(
      "5,120 match — exporting up to 200",
    );
  });

  it("clamps 500 to the cap, says so, and sends 200", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async () => fileResponse());
    vi.stubGlobal("fetch", fetchMock);
    renderDialog();

    const rows = screen.getByTestId("export-rows") as HTMLInputElement;
    // A number input rejects `user.clear` in jsdom; a change event is what a
    // keystroke produces, and the handler is what this case is about.
    fireEvent.change(rows, { target: { value: "500" } });

    expect(screen.getByTestId("export-rows-note").textContent).toContain("200");
    expect(screen.getByTestId("export-match").textContent).toBe(
      "5,120 match — exporting up to 200",
    );

    fireEvent.blur(rows); // leaving the field settles it to the clamped number
    expect(rows.value).toBe("200");

    await user.click(screen.getByTestId("export-run"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(sentBody(fetchMock).limit).toBe(200);
  });

  it("spins while the file is being written and disables a second send", async () => {
    const user = userEvent.setup();
    let settle: (response: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      settle = resolve;
    });
    const fetchMock = vi.fn(() => pending);
    vi.stubGlobal("fetch", fetchMock);
    renderDialog();

    await user.click(screen.getByTestId("export-run"));

    expect(screen.getByTestId("export-spinner")).toBeTruthy();
    expect((screen.getByTestId("export-run") as HTMLButtonElement).disabled).toBe(true);

    settle(fileResponse(12));
    await waitFor(() => expect(screen.queryByTestId("export-spinner")).toBeNull());
    expect(screen.getByTestId("export-success")).toBeTruthy();
  });

  it("shows a failure in red and retries the same body", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(failureResponse())
      .mockResolvedValueOnce(fileResponse(10));
    vi.stubGlobal("fetch", fetchMock);
    renderDialog();

    await user.click(screen.getByTestId("export-run"));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("export cap of 200 rows");
    expect(alert.className).toContain("border-down");
    expect(screen.getByTestId("export-retry")).toBeTruthy();
    expect(downloadSpy).not.toHaveBeenCalled();

    await user.click(screen.getByTestId("export-retry"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(sentBody(fetchMock, 1)).toEqual(sentBody(fetchMock, 0));
    await waitFor(() => expect(screen.getByTestId("export-success")).toBeTruthy());
  });

  it("downloads the server's bytes on success and reports the file", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async () => fileResponse(42));
    vi.stubGlobal("fetch", fetchMock);
    renderDialog();

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
    const fetchMock = vi.fn(async () => fileResponse());
    vi.stubGlobal("fetch", fetchMock);
    renderDialog({ choice: "api" });

    expect(screen.getByTestId("export-api-url").textContent).toBe("/screens/export");
    expect(screen.queryByTestId("export-run")).toBeNull();

    await user.click(screen.getByTestId("export-format-csv"));
    await user.click(screen.getByTestId("export-format-api"));

    expect(fetchMock).not.toHaveBeenCalled();
    expect(downloadSpy).not.toHaveBeenCalled();
  });

  it("exports the page's symbols — not the whole screen — when the page scope is chosen", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async () => fileResponse(2));
    vi.stubGlobal("fetch", fetchMock);
    renderDialog();

    await user.click(screen.getByTestId("export-scope-page"));
    expect(screen.getByTestId("export-match").textContent).toBe(
      "2 match — exporting up to 200",
    );

    await user.click(screen.getByTestId("export-run"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    const body = sentBody(fetchMock);
    expect(body.mode).toBe("symbols");
    expect(body.symbols).toEqual(["AAPL", "MSFT"]);
    expect(body.criteria).toBeUndefined();
    expect(body.columns).toEqual(["market_cap", "price"]);
  });

  it("exports a typed ticker list, de-duplicated and in order", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async () => fileResponse(2));
    vi.stubGlobal("fetch", fetchMock);
    renderDialog();

    await user.click(screen.getByTestId("export-scope-tickers"));
    await user.type(screen.getByTestId("export-tickers"), "msft, aapl msft");
    expect(screen.getByTestId("export-match").textContent).toBe(
      "2 match — exporting up to 200",
    );

    await user.click(screen.getByTestId("export-run"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(sentBody(fetchMock).symbols).toEqual(["MSFT", "AAPL"]);
  });
});
