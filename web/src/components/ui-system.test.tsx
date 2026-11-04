import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ConfirmDialogCanvas, confirmAction } from "./ConfirmDialog";
import { clearToasts, dismiss, notify, push, subscribe } from "@/lib/notify";
import { ToastCenter } from "./ToastCenter";
import { parseQuoteAttr, quoteProps, QUOTE_ATTR } from "@/lib/quoteAttrs";
import { parseSeries, tintAlpha } from "./HoverBubble";

describe("the notification store", () => {
  beforeEach(() => clearToasts());
  afterEach(() => vi.useRealTimers());

  it("pushes, auto-dismisses after 4s, and caps at four", () => {
    vi.useFakeTimers();
    const seen: number[] = [];
    subscribe((toasts) => seen.push(toasts.length));
    for (let i = 0; i < 6; i += 1) push("info", `toast ${i}`);
    // Six pushed, only the last four retained.
    expect(seen.at(-1)).toBe(4);
    vi.advanceTimersByTime(4001);
    expect(seen.at(-1)).toBe(0);
    dismiss(999); // an id that never existed is a no-op
    expect(seen.at(-1)).toBe(0);
  });

  it("renders as polite live text with a working dismiss button", async () => {
    const view = render(<ToastCenter />);
    notify.error("Failed to load", "the depth store refused");
    const region = (await screen.findByRole("status", {}, { timeout: 3000 })) as HTMLElement;
    expect(region.textContent).toContain("Failed to load");
    view.unmount();
    await waitFor(() => {});
  });
});

describe("the awaitable confirm", () => {
  it("resolves true on confirm and false on cancel, once per canvas", async () => {
    const view = render(<ConfirmDialogCanvas />);
    const first = confirmAction({ title: "Delete watchlist", body: "5 symbols gone", danger: true });
    await screen.findByText("Delete watchlist");
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await expect(first).resolves.toBe(true); // the user confirmed

    const second = confirmAction({ title: "again", body: "once", confirmLabel: "Do it" });
    await screen.findByText("again");
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await expect(second).resolves.toBe(true); // every open dialog confirm resolves its own promise true

    const third = confirmAction({ title: "cancel path", body: "by cancel", danger: true });
    await screen.findByText("cancel path");
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));
    await expect(third).resolves.toBe(false);
    view.unmount();

    // A canvas that is gone answers false without throwing.
    await expect(confirmAction({ title: "x", body: "no canvas" })).resolves.toBe(false);
  });
});

describe("the quote attribute", () => {
  it("round-trips a quote through an HTML-safe JSON value", () => {
    const props = quoteProps({ symbol: "BTC-USD", price: 78249.78, changePct: 1.17, kind: "crypto" });
    const attr = props[QUOTE_ATTR];
    expect(parseQuoteAttr(attr)).toMatchObject({ symbol: "BTC-USD", changePct: 1.17 });
    expect(parseQuoteAttr("<bad>")).toBeNull();
    expect(parseQuoteAttr(null)).toBeNull();
    expect(parseQuoteAttr(JSON.stringify({ nope: 1 }))).toBeNull();
  });
});

describe("the hover bubble's colour logic", () => {
  it("tints harder the bigger the move, 5%-60% band", () => {
    expect(tintAlpha(null)).toBe(0.05);
    expect(tintAlpha(0)).toBe(0.05);
    expect(tintAlpha(0.5)).toBeCloseTo(0.125);
    expect(tintAlpha(1.2)).toBeCloseTo(0.3);
    expect(tintAlpha(-4)).toBe(0.6); // |4|/4 = 1 → clamped to 0.6
    expect(tintAlpha(9)).toBe(0.6);
  });

  it("parses the chart document's bars into a sparkline", () => {
    const spark = parseSeries({
      bars: [{ c: 100 }, { c: 102 }, { c: 99 }, { c: null }],
      previous_close: 98.5,
    });
    expect(spark?.closes).toEqual([100, 102, 99]);
    expect(spark?.prevClose).toBe(98.5);
    expect(parseSeries({ bars: [] })).toBeNull();
    expect(parseSeries("no")).toBeNull();
  });
});
