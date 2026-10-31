import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ScreenRow, ViewDefinition } from "@/data/screens";
import { DataTable } from "@/grid/DataTable";

import { DetailPanel, MAX_PANEL_WIDTH, MIN_PANEL_WIDTH } from "./DetailPanel";
import {
  deferred,
  failure,
  fundamentalsPayload,
  insiderPayload,
  json,
  newsPayload,
  notFound,
  screensPayload,
  selectPayload,
  stubApi,
} from "./__fixtures__/detail";
import { resetSnapshotCache } from "./useDetail";

afterEach(() => {
  // The snapshot id is a module-level session constant (F2): one file is one
  // process, so each test starts with no id rather than inheriting one.
  resetSnapshotCache();
  vi.unstubAllGlobals();
});

function allRoutes() {
  return stubApi({
    fundamentals: () => json(fundamentalsPayload()),
    insider: () => json(insiderPayload()),
    news: () => json(newsPayload()),
    screens: () => json(screensPayload()),
    select: () => json(selectPayload()),
  });
}

describe("DetailPanel — shape", () => {
  it("renders nothing when no symbol is selected", () => {
    const fetchMock = allRoutes();
    const { container } = render(<DetailPanel symbol={null} onClose={vi.fn()} />);
    expect(container.firstChild).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("is a named complementary landmark with four tabs", async () => {
    allRoutes();
    render(<DetailPanel symbol="AAPL" onClose={vi.fn()} />);

    const panel = screen.getByRole("complementary", { name: "Details for AAPL" });
    expect(panel.getAttribute("data-docked")).toBe("right");
    // Docked, not overlaid: the root is a flex sibling, so the grid shrinks.
    expect(panel.className).not.toMatch(/\b(fixed|absolute)\b/);

    expect(screen.getByRole("tab", { name: "Fundamentals" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Ownership" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Insiders" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "News" })).toBeTruthy();

    await screen.findByTestId("fundamentals-loaded");
  });

  it("fires all four reads at mount, in parallel", async () => {
    const fetchMock = allRoutes();
    render(<DetailPanel symbol="AAPL" onClose={vi.fn()} />);

    await screen.findByTestId("fundamentals-loaded");
    // fundamentals, insider, news, screens (snapshot) then select — the chain
    // only lengthens ownership, it never serialises the other three.
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
    const urls = fetchMock.mock.calls.map((call) => String((call as unknown[])[0]));
    expect(urls.filter((url) => url.includes("/engine/fundamentals/AAPL"))).toHaveLength(1);
    expect(urls.filter((url) => url.includes("/engine/insider/AAPL"))).toHaveLength(1);
    expect(urls.filter((url) => url.includes("/news/AAPL"))).toHaveLength(1);
    expect(urls.filter((url) => url.includes("/screens/select"))).toHaveLength(1);
  });
});

describe("DetailPanel — the four tabs, wired", () => {
  it("paints real shapes from all four endpoints", async () => {
    allRoutes();
    const user = userEvent.setup();
    render(<DetailPanel symbol="AAPL" onClose={vi.fn()} />);

    await screen.findByTestId("fundamentals-loaded");
    expect(screen.getByTestId("metric-revenue").textContent).toContain("466.82B");

    await user.click(screen.getByRole("tab", { name: "Ownership" }));
    await screen.findByTestId("ownership-loaded");
    expect(screen.getByTestId("ownership-inst_own").textContent).toContain("66.34");

    await user.click(screen.getByRole("tab", { name: "Insiders" }));
    await screen.findByTestId("insiders-loaded");
    expect(screen.getAllByTestId("insider-row")[0]?.textContent).toContain("Newstead Jennifer");

    await user.click(screen.getByRole("tab", { name: "News" }));
    await screen.findByTestId("news-loaded");
    expect(screen.getByRole("link", { name: /Opal Wealth Advisors/ })).toBeTruthy();
  });

  it("does not let a slow fundamentals read hold the News tab", async () => {
    const slow = deferred<Response>();
    stubApi({
      fundamentals: () => slow.promise,
      news: () => json(newsPayload()),
    });
    const user = userEvent.setup();
    render(<DetailPanel symbol="AAPL" onClose={vi.fn()} />);

    expect(screen.getByTestId("fundamentals-loading")).toBeTruthy();

    await user.click(screen.getByRole("tab", { name: "News" }));
    await screen.findByTestId("news-loaded");
    expect(screen.getByRole("link", { name: /Opal Wealth Advisors/ })).toBeTruthy();

    // Still pending: the news answer arrived while fundamentals was in flight.
    await act(async () => {
      slow.resolve(json(fundamentalsPayload()));
      await Promise.resolve();
    });

    await user.click(screen.getByRole("tab", { name: "Fundamentals" }));
    await screen.findByTestId("fundamentals-loaded");
  });

  it("keeps a 500 inside the tab that suffered it", async () => {
    stubApi({
      fundamentals: () => failure("upstream exploded"),
      insider: () => json(insiderPayload()),
      news: () => json(newsPayload()),
      screens: () => json(screensPayload()),
      select: () => json(selectPayload()),
    });
    const user = userEvent.setup();
    render(<DetailPanel symbol="AAPL" onClose={vi.fn()} />);

    const alert = await screen.findByTestId("fundamentals-error");
    expect(alert.textContent).toContain("upstream exploded");

    await user.click(screen.getByRole("tab", { name: "News" }));
    await screen.findByTestId("news-loaded");

    await user.click(screen.getByRole("tab", { name: "Insiders" }));
    await screen.findByTestId("insiders-loaded");
  });

  it("treats a 404 as an empty fundamentals answer, not an error", async () => {
    stubApi({ fundamentals: () => notFound("security 'ZZZZ' is not in the loaded identity master") });
    render(<DetailPanel symbol="ZZZZ" onClose={vi.fn()} />);

    await screen.findByTestId("fundamentals-empty");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("fundamentals-error")).toBeNull();
  });

  it("shows the insiders empty state for a symbol with no Form 4 filings", async () => {
    stubApi({
      fundamentals: () => json(fundamentalsPayload({ ticker: "ASML", company: "ASML HOLDING NV" })),
      insider: () => json({ symbol: "ASML", as_of: "2026-10-29", count: 0, items: [] }),
      news: () => json(newsPayload()),
      screens: () => json(screensPayload()),
      select: () => json(selectPayload("ASML")),
    });
    const user = userEvent.setup();
    render(<DetailPanel symbol="ASML" onClose={vi.fn()} />);

    await screen.findByTestId("fundamentals-loaded");
    await user.click(screen.getByRole("tab", { name: "Insiders" }));
    const empty = await screen.findByTestId("insiders-empty");
    expect(empty.textContent).toContain("There are no Form 4 filings");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("DetailPanel — adversarial", () => {
  it("renders an HTML-shaped symbol as text in the header", async () => {
    allRoutes();
    const symbol = '<img src=x onerror="alert(1)">';
    render(<DetailPanel symbol={symbol} onClose={vi.fn()} />);

    const panel = screen.getByRole("complementary", { name: `Details for ${symbol}` });
    const heading = within(panel).getByRole("heading", { level: 2 });
    expect(heading.textContent).toBe(symbol);
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("[onerror]")).toBeNull();
  });

  it("never paints the previous symbol's data under a new header", async () => {
    const slowA = deferred<Response>();
    const fetchMock = stubApi({
      fundamentals: (url) =>
        url.includes("/AAPL")
          ? slowA.promise
          : json(fundamentalsPayload({ ticker: "MSFT", company: "Microsoft Corp." })),
    });

    const { rerender } = render(<DetailPanel symbol="AAPL" onClose={vi.fn()} />);
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) =>
          String((call as unknown[])[0]).includes("/engine/fundamentals/AAPL"),
        ),
      ).toBe(true),
    );
    const aaplCall = fetchMock.mock.calls.find((call) =>
      String((call as unknown[])[0]).includes("/engine/fundamentals/AAPL"),
    );
    const signal = (aaplCall as unknown[])[1] as RequestInit | undefined;
    expect(signal?.signal?.aborted).toBe(false);

    rerender(<DetailPanel symbol="MSFT" onClose={vi.fn()} />);
    await waitFor(() => expect(signal?.signal?.aborted).toBe(true));

    await screen.findByTestId("fundamentals-loaded");
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("MSFT");
    expect(screen.queryByText("Apple Inc.")).toBeNull();
    expect(screen.getByText("Microsoft Corp.")).toBeTruthy();

    // The aborted AAPL answer lands late; it must stay invisible.
    await act(async () => {
      slowA.resolve(json(fundamentalsPayload()));
      await Promise.resolve();
    });
    expect(screen.queryByText("Apple Inc.")).toBeNull();
  });
});

describe("DetailPanel — keyboard and focus", () => {
  it("Escape closes and returns focus to the grid row", async () => {
    allRoutes();
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <>
        <table>
          <tbody>
            <tr data-symbol="AAPL" tabIndex={0}>
              <td>AAPL</td>
            </tr>
          </tbody>
        </table>
        <DetailPanel symbol="AAPL" onClose={onClose} />
      </>,
    );

    const row = document.querySelector<HTMLElement>('tr[data-symbol="AAPL"]');
    expect(row).not.toBeNull();

    await user.click(screen.getByRole("tab", { name: "News" }));
    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(row);
  });

  it("closes from the close button too", async () => {
    allRoutes();
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<DetailPanel symbol="AAPL" onClose={onClose} />);

    await user.click(screen.getByRole("button", { name: "Close details" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("aborts every in-flight read when the parent closes the panel", async () => {
    const slow = deferred<Response>();
    const fetchMock = stubApi({
      fundamentals: () => slow.promise,
      screens: () => json(screensPayload()),
    });

    function Harness() {
      const [symbol, setSymbol] = React.useState<string | null>("AAPL");
      return <DetailPanel symbol={symbol} onClose={() => setSymbol(null)} />;
    }

    render(<Harness />);

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) =>
          String((call as unknown[])[0]).includes("/engine/fundamentals/AAPL"),
        ),
      ).toBe(true),
    );
    const call = fetchMock.mock.calls.find((entry) =>
      String((entry as unknown[])[0]).includes("/engine/fundamentals/AAPL"),
    );
    const signal = ((call as unknown[])[1] as RequestInit).signal as AbortSignal;
    expect(signal.aborted).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Close details" }));

    await waitFor(() => expect(signal.aborted).toBe(true));
    expect(screen.queryByTestId("detail-panel")).toBeNull();
  });

  it("leaves Escape to an open dialog", async () => {
    allRoutes();
    const onClose = vi.fn();
    render(
      <>
        <div role="dialog" aria-label="Filters" />
        <DetailPanel symbol="AAPL" onClose={onClose} />
      </>,
    );

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("still closes when the shell's closed drawer is only hidden, not unmounted", () => {
    allRoutes();
    const onClose = vi.fn();
    render(
      <>
        <div hidden>
          <div role="dialog" aria-label="Filters" />
        </div>
        <DetailPanel symbol="AAPL" onClose={onClose} />
      </>,
    );

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("DetailPanel — the resizer", () => {
  it("is a keyboard-operable vertical separator clamped to 360–560 px", () => {
    allRoutes();
    render(<DetailPanel symbol="AAPL" onClose={vi.fn()} />);

    const panel = screen.getByTestId("detail-panel");
    const separator = screen.getByRole("separator", { name: "Resize details panel" });
    expect(separator.getAttribute("aria-orientation")).toBe("vertical");
    expect(separator.getAttribute("aria-valuemin")).toBe(String(MIN_PANEL_WIDTH));
    expect(separator.getAttribute("aria-valuemax")).toBe(String(MAX_PANEL_WIDTH));
    expect(separator.getAttribute("aria-valuenow")).toBe("420");
    expect(separator.getAttribute("aria-controls")).toBe(panel.id);

    fireEvent.keyDown(separator, { key: "ArrowLeft" });
    expect(separator.getAttribute("aria-valuenow")).toBe("436");
    expect(panel.style.width).toBe("436px");

    fireEvent.keyDown(separator, { key: "End" });
    expect(separator.getAttribute("aria-valuenow")).toBe("560");
    fireEvent.keyDown(separator, { key: "ArrowLeft" });
    expect(separator.getAttribute("aria-valuenow")).toBe("560");

    fireEvent.keyDown(separator, { key: "Home" });
    expect(separator.getAttribute("aria-valuenow")).toBe("360");
    fireEvent.keyDown(separator, { key: "ArrowRight" });
    expect(separator.getAttribute("aria-valuenow")).toBe("360");

    fireEvent.keyDown(separator, { key: "PageUp" });
    expect(separator.getAttribute("aria-valuenow")).toBe("424");
  });

  it("drags with the pointer and clamps at both ends", () => {
    allRoutes();
    render(<DetailPanel symbol="AAPL" onClose={vi.fn()} />);
    const panel = screen.getByTestId("detail-panel");
    const separator = screen.getByRole("separator", { name: "Resize details panel" });

    fireEvent.pointerDown(separator, { clientX: 500, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 300, pointerId: 1 });
    expect(panel.style.width).toBe("560px");
    fireEvent.pointerUp(window, { pointerId: 1 });

    fireEvent.pointerDown(separator, { clientX: 500, pointerId: 2 });
    fireEvent.pointerMove(window, { clientX: 800, pointerId: 2 });
    expect(panel.style.width).toBe("360px");
    fireEvent.pointerUp(window, { pointerId: 2 });
  });
});

/**
 * F6/F7 — Escape has to work from the two places the report found it inert or
 * destructive: the grid row that opened the panel (it closed the panel but
 * dropped focus to `<body>`, because the row blurred itself) and the top-bar
 * search input (the panel's handler stood down and nothing else closed it).
 *
 * The row case is composed against the real `DataTable`, because the defect is
 * in the seam between the row's key handler and the panel's document listener —
 * a hand-rolled `<tr>` would pass whatever the fix did.
 */

const COMPOSED_VIEW: ViewDefinition = {
  name: "today",
  label: "Today",
  job: "What is moving right now?",
  columns: ["price"],
};

const COMPOSED_ROWS: ScreenRow[] = [
  { symbol: "AAPL", price: 344.63 },
  { symbol: "NVDA", price: 902.1 },
];

function GridAndPanel({ onClose }: { onClose?: () => void }) {
  const [symbol, setSymbol] = React.useState<string | null>(null);
  const close = () => {
    onClose?.();
    setSymbol(null);
  };
  return (
    <div>
      <DataTable
        view={COMPOSED_VIEW}
        rows={COMPOSED_ROWS}
        status="ready"
        error={null}
        revalidated={false}
        pageIndex={0}
        pageSize={2}
        rowCount={2}
        onRetry={vi.fn()}
        containerWidth={800}
        selectedSymbol={symbol}
        onActivate={setSymbol}
        onClear={close}
      />
      {symbol ? <DetailPanel symbol={symbol} onClose={close} /> : null}
    </div>
  );
}

function composedRow(symbol: string): HTMLElement {
  const row = document.querySelector<HTMLElement>(`tr[data-symbol="${symbol}"]`);
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

describe("DetailPanel — Escape from the grid row (F6)", () => {
  it("closes the panel and leaves focus on the row that opened it", async () => {
    allRoutes();
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<GridAndPanel onClose={onClose} />);

    const row = composedRow("AAPL");
    row.focus();
    expect(document.activeElement).toBe(row);

    await user.keyboard("{Enter}");
    await screen.findByTestId("detail-panel");
    expect(row.getAttribute("data-selected")).toBe("true");

    await user.keyboard("{Escape}");

    // One key, one close: the row's handler and the panel's document listener
    // must not both act on the same keystroke (the row sets defaultPrevented).
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("detail-panel")).toBeNull();
    expect(document.activeElement).toBe(row);
    expect(document.activeElement).not.toBe(document.body);
    // The row is still the grid's one tab stop: a keyboard reader can keep going.
    expect(row.getAttribute("tabindex")).toBe("0");
  });

  it("still clears the roving cursor when no panel is open", async () => {
    allRoutes();
    const user = userEvent.setup();
    render(<GridAndPanel />);

    // Row two: row one is the grid's default tab stop when nothing is focused.
    const row = composedRow("NVDA");
    row.focus();
    await user.keyboard("{Escape}");

    expect(screen.queryByTestId("detail-panel")).toBeNull();
    expect(row.getAttribute("tabindex")).toBe("-1");
  });
});

describe("DetailPanel — Escape from the search field (F7)", () => {
  function SearchAndPanel({ onClose }: { onClose: () => void }) {
    return (
      <>
        <input
          type="text"
          role="combobox"
          aria-label="Search symbols and companies"
          aria-expanded="false"
          aria-controls="symbol-search-listbox"
          aria-autocomplete="list"
        />
        <DetailPanel symbol="AAPL" onClose={onClose} />
      </>
    );
  }

  it("closes the panel when focus sits in the top-bar search", async () => {
    allRoutes();
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<SearchAndPanel onClose={onClose} />);

    const input = screen.getByRole("combobox", { name: "Search symbols and companies" });
    input.focus();
    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
    // The caret stays in the field: the panel closed, the search did not.
    expect(document.activeElement).toBe(input);
  });

  it("stands down while the search's own result list owns Escape", async () => {
    allRoutes();
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<SearchAndPanel onClose={onClose} />);

    const input = screen.getByRole("combobox", { name: "Search symbols and companies" });
    input.setAttribute("aria-expanded", "true");
    input.focus();
    await user.keyboard("{Escape}");

    // The list closes first; the panel is not a second casualty of one key.
    expect(onClose).not.toHaveBeenCalled();
  });

  it("does not close the panel in the same keystroke that closes the filter drawer", async () => {
    allRoutes();
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <>
        <div role="dialog" aria-label="Filters" />
        <SearchAndPanel onClose={onClose} />
      </>,
    );

    screen.getByRole("combobox").focus();
    await user.keyboard("{Escape}");

    expect(onClose).not.toHaveBeenCalled();
  });

  it("survives a drawer that unmounts itself on the same keystroke", async () => {
    allRoutes();
    const onClose = vi.fn();
    const user = userEvent.setup();

    // The race the guard has to win: a dialog whose own Escape listener runs
    // first and removes it. By the time the panel's bubble handler runs there
    // is no dialog left to find — so the decision must have been taken when the
    // key went down (capture), not when it bubbled.
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-label", "Filters");
    document.body.append(dialog);
    const dismiss = () => {
      dialog.remove();
      document.removeEventListener("keydown", dismiss);
    };
    document.addEventListener("keydown", dismiss);

    try {
      render(<SearchAndPanel onClose={onClose} />);
      screen.getByRole("combobox").focus();
      await user.keyboard("{Escape}");

      expect(dialog.isConnected).toBe(false);
      expect(onClose).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener("keydown", dismiss);
      dialog.remove();
    }
  });
});
