import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { NewsTab } from "./NewsTab";
import { parseNews } from "./useDetail";
import type { NewsData } from "./useDetail";
import { emptyNewsPayload, newsPayload, resource } from "./__fixtures__/detail";

function data(payload: Record<string, unknown>) {
  const parsed = parseNews(payload);
  if (!parsed) throw new Error("fixture did not parse");
  return parsed;
}

describe("News tab", () => {
  it("shows its own skeleton while the request is pending", () => {
    render(<NewsTab symbol="AAPL" state={resource("loading")} onRetry={vi.fn()} />);
    expect(screen.getByTestId("news-loading").getAttribute("aria-busy")).toBe("true");
  });

  it("renders a headline as a new-tab link with a relative source time", () => {
    render(<NewsTab symbol="AAPL" state={resource("ready", data(newsPayload()))} onRetry={vi.fn()} />);

    const link = screen.getByRole("link", { name: /Opal Wealth Advisors/ });
    expect(link.getAttribute("href")).toBe("https://news.google.com/rss/articles/abc?oc=5");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");

    expect(screen.getByText("MarketBeat")).toBeTruthy();
    const times = screen.getAllByText("1h ago");
    expect(times[0]?.getAttribute("title")).toContain("UTC");
  });

  it("never turns a javascript: URL into a link", () => {
    render(<NewsTab symbol="AAPL" state={resource("ready", data(newsPayload()))} onRetry={vi.fn()} />);

    expect(screen.queryByRole("link", { name: /What Apple Doubters/ })).toBeNull();
    const plain = screen.getByTestId("news-plain");
    expect(plain.textContent).toBe("What Apple Doubters Got Wrong");
    expect(document.querySelector('a[href^="javascript"]')).toBeNull();
    expect(document.querySelector('a[href="#"]')).toBeNull();
  });

  it("renders an empty state when the feed has nothing", () => {
    expect(parseNews(emptyNewsPayload())).toBeNull();
    render(<NewsTab symbol="AAPL" state={resource<NewsData>("empty")} onRetry={vi.fn()} />);
    expect(screen.getByTestId("news-empty").textContent).toContain("No recent news for AAPL.");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows its own error and retries it", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(
      <NewsTab symbol="AAPL" state={resource<NewsData>("error", null, "HTTP 503")} onRetry={onRetry} />,
    );
    expect(screen.getByRole("alert").textContent).toContain("Could not load news.");
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
