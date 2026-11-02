/**
 * Statements: three panels, one row per line, an em dash for a gap, and a
 * reason instead of a table when the endpoint says no.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { describe, expect, it } from "vitest";

import { StatementsTab } from "./StatementsTab";
import { StatementsTable } from "./StatementsTable";
import { parseStatements, yearOverYear } from "./statements";
import { notFound, statementTablePayload, statementsPayload, stubTickerApi } from "./__fixtures__/ticker";
import type { StatementsDocument } from "./statements";

function documentFixture() {
  const document = parseStatements(statementsPayload());
  if (document === null) throw new Error("fixture did not parse");
  return document;
}

function Harness() {
  const [mode, setMode] = React.useState<"annual" | "quarterly">("annual");
  return (
    <StatementsTable document={documentFixture()} mode={mode} onModeChange={setMode} />
  );
}

describe("StatementsTable", () => {
  it("renders the three panels with a row per reported line", () => {
    render(<Harness />);

    expect(screen.getByTestId("statements-income")).toBeTruthy();
    expect(screen.getByTestId("statements-balance")).toBeTruthy();
    expect(screen.getByTestId("statements-cash_flow")).toBeTruthy();

    const income = screen.getByTestId("statements-income");
    const rows = income.querySelectorAll("tbody tr[data-statement-row]");
    expect(rows).toHaveLength(3);
    expect(
      Array.from(rows).map((row) => row.getAttribute("data-statement-row")),
    ).toEqual(["revenue", "gross_profit", "eps_diluted"]);
  });

  it("prints a null cell as the em dash, never as zero", () => {
    render(<Harness />);
    const row = screen.getByTestId("statements-income").querySelector(
      'tr[data-statement-row="gross_profit"]',
    );
    const cells = row?.querySelectorAll("td");
    expect(cells?.[0]?.textContent).toBe("$195.2B");
    expect(cells?.[1]?.textContent).toBe("—");
    expect(cells?.[1]?.getAttribute("data-null")).toBe("true");
  });

  it("computes YoY from the two newest annual columns", () => {
    const document = documentFixture();
    const revenue = document.blocks[0]?.lines.find((line) => line.key === "revenue");
    expect(revenue).toBeDefined();
    expect(yearOverYear(revenue?.cells ?? [], 0)).toBeCloseTo(6.4255, 3);

    render(<StatementsTable document={document} mode="annual" onModeChange={() => {}} />);
    expect(screen.getByTestId("statements-yoy-revenue").textContent).toBe("+6.43%");
    // One reported end is not a ratio.
    expect(screen.getByTestId("statements-yoy-gross_profit").textContent).toBe("—");
  });

  it("offers an annual/quarterly toggle and states when a mode has no periods", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole("button", { name: "Annual" }));
    expect(screen.getByTestId("statements-income")).toBeTruthy();

    // The legacy fixture is annual-only: quarterly is an honest sentence, not
    // annual columns wearing a quarterly label.
    await user.click(screen.getByRole("button", { name: "Quarterly" }));
    expect(screen.getByTestId("statements-mode-empty").textContent).toContain(
      "No quarterly periods are reported",
    );
    expect(screen.queryByTestId("statements-income")).toBeNull();
  });

  it("filters a two-kind document's columns instead of relabelling them", async () => {
    const parsed = parseStatements(statementTablePayload());
    if (parsed === null) throw new Error("keyed fixture did not parse");
    const table: StatementsDocument = parsed;
    expect(table.periods.map((period) => period.kind)).toEqual([
      "annual",
      "annual",
      "quarterly",
      "quarterly",
    ]);

    function KeyedHarness() {
      const [mode, setMode] = React.useState<"annual" | "quarterly">("annual");
      return <StatementsTable document={table} mode={mode} onModeChange={setMode} />;
    }
    const user = userEvent.setup();
    render(<KeyedHarness />);

    // Annual: the collector's own YoY and TTM, not a recomputation.
    expect(screen.getByTestId("statements-yoy-revenue").textContent).toBe("+6.43%");
    expect(screen.getByTestId("statements-ttm-revenue").textContent).toBe("$420B");

    await user.click(screen.getByRole("button", { name: "Quarterly" }));
    expect(screen.getByTestId("statements-income").getAttribute("data-mode")).toBe("quarterly");
    const income = screen.getByTestId("statements-income");
    expect(within(income).getAllByText("2025Q3").length).toBeGreaterThan(0);
    expect(within(income).queryByText("2025")).toBeNull();
    // Same quarter a year earlier: (102.466 − 85.777) / 85.777.
    expect(screen.getByTestId("statements-yoy-revenue").textContent).toBe("+19.46%");
    expect(screen.queryByTestId("statements-ttm-revenue")).toBeNull();
  });
});

describe("StatementsTab", () => {
  it("shows the 404's reason instead of an empty table", async () => {
    const reason = "no_statements: SEC companyfacts are not on file for ZZZZ";
    stubTickerApi({ statements: () => notFound(reason) });

    render(<StatementsTab symbol="ZZZZ" />);

    const message = await screen.findByTestId("statements-404");
    expect(message.textContent).toContain(reason);
    await waitFor(() => expect(screen.queryByTestId("statements-income")).toBeNull());
  });

  it("renders the collected document when the endpoint answers", async () => {
    stubTickerApi();

    render(<StatementsTab symbol="AAPL" />);

    await screen.findByTestId("statements-income", undefined, { timeout: 3_000 });
    expect(screen.getByTestId("statements").getAttribute("data-mode")).toBe("annual");
  });
});
