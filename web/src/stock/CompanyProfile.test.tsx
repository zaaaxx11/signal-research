/**
 * The company profile block: a clamped description with a real expander, a
 * two-column officer list, and one sentence when there is no profile.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CompanyProfile, COMPANY_CLAMP_CHARS } from "./CompanyProfile";
import { parseCompany } from "./useTicker";
import { companyPayload, json, notFound, stubTickerApi } from "./__fixtures__/ticker";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CompanyProfile", () => {
  it("renders the description clamped, the expander, and the key people", async () => {
    stubTickerApi({ company: () => json(companyPayload()) });
    render(<CompanyProfile symbol="AAPL" />);

    const description = await screen.findByTestId("company-description");
    expect(description.textContent).toContain("Apple Inc. designs");
    expect(description.textContent?.length).toBeGreaterThan(COMPANY_CLAMP_CHARS);
    expect(description.className).toContain("line-clamp-4");

    for (const officer of companyPayload()["officers"] as Array<{ name: string }>) {
      expect(screen.getByText(officer.name)).toBeTruthy();
    }
    expect(screen.getByText("Chief Executive Officer")).toBeTruthy();

    const more = screen.getByTestId("company-more");
    expect(more.getAttribute("aria-expanded")).toBe("false");
    await userEvent.setup().click(more);
    expect(screen.getByTestId("company-description").className).not.toContain("line-clamp-4");
    expect(screen.getByTestId("company-more").textContent).toBe("Less");
    expect(screen.getByTestId("company-more").getAttribute("aria-expanded")).toBe("true");
  });

  it("hides the expander for a description short enough to fit the clamp", async () => {
    stubTickerApi({
      company: () =>
        json({
          symbol: "AAPL",
          name: "Apple Inc.",
          description: "Apple makes consumer hardware and services.",
          officers: [{ name: "Mr. Timothy D. Cook", title: "Chief Executive Officer" }],
        }),
    });
    render(<CompanyProfile symbol="AAPL" />);

    await screen.findByTestId("company-description");
    expect(screen.queryByTestId("company-more")).toBeNull();
  });

  it("says there is no profile on a 404, never drawing an empty heading body", async () => {
    stubTickerApi({ company: () => notFound("company profile not found") });
    render(<CompanyProfile symbol="ZZZZ" />);

    const empty = await screen.findByTestId("company-empty");
    expect(empty.textContent).toBe("No company profile available.");
    expect(screen.queryByTestId("company-description")).toBeNull();
    expect(screen.queryByTestId("company-officers")).toBeNull();
  });

  it("treats an empty document as no profile rather than a blank paragraph", async () => {
    stubTickerApi({ company: () => json({ symbol: "ZZZZ", name: "ZZZZ", officers: [] }) });
    render(<CompanyProfile symbol="ZZZZ" />);

    await screen.findByTestId("company-empty");
    expect(screen.getByTestId("company-profile").getAttribute("data-status")).toBe("empty");
  });

  it("drops an officer with no name but keeps the named ones", () => {
    const parsed = parseCompany(
      {
        symbol: "AAPL",
        description: "A company.",
        officers: [
          { name: "", title: "Nobody" },
          { name: "Ms. Jane Doe", title: null },
        ],
      },
      "AAPL",
    );
    expect(parsed?.officers).toEqual([{ name: "Ms. Jane Doe", title: null }]);
  });

  it("shows a loading state while the endpoint has not answered", () => {
    // A handler that never resolves: the block must not claim there is no
    // profile before the read has finished.
    stubTickerApi({ company: () => new Promise<Response>(() => {}) });
    render(<CompanyProfile symbol="AAPL" />);

    expect(screen.getByTestId("company-loading")).toBeTruthy();
    expect(screen.getByTestId("company-profile").getAttribute("data-status")).toBe("loading");
  });

  it("offers a retry when the request itself failed", async () => {
    stubTickerApi({ company: () => json({ error: "boom", errorMessage: "engine is down" }, 503) });
    render(<CompanyProfile symbol="AAPL" />);

    const error = await screen.findByTestId("company-error");
    expect(error.textContent).toContain("engine is down");
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
    await waitFor(() => expect(screen.queryByTestId("company-empty")).toBeNull());
  });
});
