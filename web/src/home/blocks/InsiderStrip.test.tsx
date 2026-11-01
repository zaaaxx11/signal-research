import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { InsiderStrip } from "./InsiderStrip";
import { LOADING, READY, readHome } from "../home";
import { homeDocument } from "../__fixtures__/home";

/**
 * The strip's one non-negotiable: the door to `/insider` exists in every state.
 * A missing batch of Form 4s is a gap in the index, not a reason to remove the
 * page it indexes.
 */

describe("InsiderStrip", () => {
  it("draws five filings with a date, a symbol, a name and a role", () => {
    const report = readHome(homeDocument());
    render(<InsiderStrip state={READY} filings={report.filings} />);

    const cells = document.querySelectorAll("[data-filing]");
    expect(cells).toHaveLength(5);
    expect(cells[0]?.textContent).toContain("2026-10-29");
    expect(cells[0]?.textContent).toContain("AAPL");
    expect(cells[0]?.textContent).toContain("Newstead Jennifer");
    expect(cells[0]?.textContent).toContain("General Counsel");
  });

  it("links a filing's symbol to its stock page", () => {
    const report = readHome(homeDocument());
    render(<InsiderStrip state={READY} filings={report.filings} />);

    const link = document.querySelector('[data-filing="AAPL"] a');
    expect(link?.getAttribute("href")).toBe("/app/stock/AAPL");
  });

  it("keeps the link to /insider when the filings block is missing", () => {
    render(
      <InsiderStrip
        state={{ kind: "missing", reason: "Filings unavailable — the source did not answer this request." }}
        filings={null}
      />,
    );

    expect(screen.getByTestId("insider-reason").textContent).toContain("did not answer");
    expect(screen.getByTestId("insider-all").getAttribute("href")).toBe("/app/insider");
    expect(document.querySelectorAll("[data-filing]")).toHaveLength(0);
  });

  it("keeps the link while loading and while empty", () => {
    const { unmount } = render(<InsiderStrip state={LOADING} filings={null} />);
    expect(screen.getByTestId("insider-all").getAttribute("href")).toBe("/app/insider");
    expect(screen.getByTestId("insider-skeleton").children).toHaveLength(5);
    unmount();

    render(<InsiderStrip state={READY} filings={[]} />);
    expect(screen.getByTestId("insider-all").getAttribute("href")).toBe("/app/insider");
    expect(screen.getByTestId("home-insider").textContent).toContain(
      "No Form 4 filings in the latest window.",
    );
  });

  it("keeps the link when the whole page failed", () => {
    render(
      <InsiderStrip
        state={{ kind: "error", reason: "The dashboard did not load." }}
        filings={null}
      />,
    );

    expect(screen.getByTestId("insider-all").getAttribute("href")).toBe("/app/insider");
    expect(screen.getByTestId("insider-reason").textContent).toBe("The dashboard did not load.");
  });

  it("names an unnamed filer instead of printing an empty cell", () => {
    const report = readHome(
      homeDocument({ filings: [{ symbol: "ZZ", owner: "", role: "", filed: "2026-10-30" }] }),
    );
    render(<InsiderStrip state={READY} filings={report.filings} />);

    expect(screen.getByTestId("home-insider").textContent).toContain("Unnamed filer");
  });
});
