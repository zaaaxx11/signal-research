/**
 * A snapshot of one `GET /market/news?limit=60` answer, in wire shape.
 *
 * Sixty rows across three UTC days plus one undated story, so the fixture can
 * prove the count, the sort and the day grouping at the size the page actually
 * reads. Tests that need a particular edge (same headline twice, a refused URL,
 * one story on a day) build a small payload with `wireItem` instead of bending
 * this one.
 */

export const NEWS_TODAY = "2026-11-01";

export interface WireNewsItem {
  headline: string;
  url: string;
  source: string;
  published_at: string | null;
  symbols: string[];
}

export function wireItem(overrides: Partial<WireNewsItem> = {}): WireNewsItem {
  return {
    headline: "S&P 500 edges higher as chipmakers rally",
    url: "https://news.example.com/markets/0",
    source: "Reuters",
    published_at: "2026-11-01T09:21:00Z",
    symbols: ["SPY"],
    ...overrides,
  };
}

const DAYS = ["2026-11-01", "2026-10-31", "2026-10-30"] as const;
const COMPANIES = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "JPM"] as const;
const FUNDS = ["SPY", "QQQ", "DIA", "IWM"] as const;
const SOURCES = ["Reuters", "Bloomberg", "CNBC", "MarketWatch"] as const;

const HEADLINES = [
  "Is Apple a Top Tech Stock to Buy Now?",
  "S&P 500 edges higher as chipmakers rally",
  "Treasury yields slip after soft inflation print",
  "Nvidia suppliers gain on data-centre orders",
  "Oil steadies as traders weigh supply outlook",
  "Small caps lag as rate-cut bets fade",
  "Dollar firms ahead of the payrolls report",
  "Banks lead the Dow on stronger net interest income",
  "Tesla deliveries beat the lowered bar",
  "Index funds see a fourth week of inflows",
] as const;

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function defaultItems(): WireNewsItem[] {
  const items: WireNewsItem[] = [];
  for (let index = 0; index < 60; index += 1) {
    if (index === 59) {
      // One story the feeds did not stamp: it must sort last, print no clock
      // and sit under `Undated` rather than under today.
      items.push(
        wireItem({
          headline: "Column: what the index boom leaves out",
          url: "https://news.example.com/opinion/undated",
          source: "MarketWatch",
          published_at: null,
          symbols: ["SPY"],
        }),
      );
      continue;
    }
    const day = DAYS[Math.floor(index / 20)] ?? DAYS[0];
    const hour = 20 - (index % 20);
    const template = HEADLINES[index % HEADLINES.length] ?? HEADLINES[0];
    items.push(
      wireItem({
        headline: index < HEADLINES.length ? template : `${template} — update ${index}`,
        url: `https://news.example.com/markets/${index}`,
        source: SOURCES[index % SOURCES.length] ?? "Reuters",
        published_at: `${day}T${pad(hour)}:15:00Z`,
        symbols: [COMPANIES[index % COMPANIES.length] ?? "AAPL", FUNDS[index % FUNDS.length] ?? "SPY"],
      }),
    );
  }
  return items;
}

export function newsDocument(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const items = defaultItems();
  const document: Record<string, unknown> = {
    as_of: "2026-11-01T20:05:00Z",
    symbols: ["SPY", "QQQ", "DIA", "IWM", "AAPL", "MSFT"],
    count: items.length,
    items,
    provider_errors: [],
    ...overrides,
  };
  if (!("count" in overrides) && Array.isArray(document["items"])) {
    document["count"] = (document["items"] as unknown[]).length;
  }
  return document;
}
