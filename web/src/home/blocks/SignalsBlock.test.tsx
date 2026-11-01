import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SignalsBlock } from "./SignalsBlock";
import { LOADING, READY, readHome } from "../home";
import { homeDocument } from "../__fixtures__/home";

/**
 * The compact reading of the same document: a count per screen and the name
 * leading it — with a zero drawn as `0` (an answer) and a missing leader drawn
 * as an em dash (not a name).
 */

describe("SignalsBlock", () => {
  it("draws the four screens with their counts and leaders", () => {
    const report = readHome(homeDocument());
    render(<SignalsBlock state={READY} movers={report.movers} />);

    const gainers = document.querySelector('[data-signal="gainers"]');
    expect(gainers?.textContent).toContain("Top gainers");
    expect(gainers?.textContent).toContain("12");
    expect(gainers?.textContent).toContain("NVDA");
    expect(gainers?.textContent).toContain("+12.00%");

    const newHigh = document.querySelector('[data-signal="newHigh"]');
    expect(newHigh?.textContent).toContain("10");
    expect(newHigh?.textContent).toContain("LLY");

    // The rows are the answer; a caption counting them is noise.
    expect(screen.getByTestId("home-signals").textContent).not.toContain("across four screens");
  });

  it("links the leading name to its stock page", () => {
    const report = readHome(homeDocument());
    render(<SignalsBlock state={READY} movers={report.movers} />);

    const link = document.querySelector('[data-signal="newLow"] a');
    expect(link?.getAttribute("href")).toBe("/app/stock/INTC");
  });

  it("draws zero and an em dash when a screen found nothing", () => {
    const report = readHome(homeDocument({ movers: { gainers: [], new_high: [], overbought: [], new_low: [] } }));
    render(<SignalsBlock state={READY} movers={report.movers} />);

    const row = document.querySelector('[data-signal="gainers"]');
    expect(row?.querySelector("[data-count]")?.getAttribute("data-count")).toBe("0");
    expect(row?.textContent).toContain("—");
    expect(row?.querySelector("a")).toBeNull();
  });

  it("prints the reason when the movers document is missing", () => {
    render(<SignalsBlock state={{ kind: "missing", reason: "Movers unavailable." }} movers={null} />);

    expect(screen.getByTestId("signals-reason").textContent).toBe("Movers unavailable.");
    expect(document.querySelectorAll("[data-signal]")).toHaveLength(0);
  });

  it("reserves four rows while loading", () => {
    render(<SignalsBlock state={LOADING} movers={null} />);

    expect(screen.getByTestId("signals-skeleton").children).toHaveLength(4);
    expect(screen.getByTestId("home-signals").style.minHeight).toBe("188px");
  });
});
