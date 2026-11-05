/**
 * The /market/insider shapes: one row per transaction, plus the two sidebar
 * blocks the page renders alongside the main table.
 *
 * The API lives at ``GET /market/insider?owner_type=…&transaction_type=…``.
 */

/** One transaction row from the global insider feed. */
export interface InsiderRow {
  readonly symbol: string;
  readonly owner: string;
  readonly owner_type: "insider" | "manager" | "fund";
  readonly relationship: string | null;
  readonly transaction_type: "Buy" | "Sale";
  readonly shares: number | null;
  readonly value: number | null;
  readonly filing_date: string;
}

export type OwnerType = "insiders" | "managers" | "funds";
export type TransactionFilter = "all" | "buy" | "sell";

export const OWNER_TYPES: readonly OwnerType[] = ["insiders", "managers", "funds"];
export const OWNER_TYPE_LABELS: Record<OwnerType, string> = {
  insiders: "Insiders",
  managers: "Managers",
  funds: "Funds",
};

export const TRANSACTION_FILTERS: readonly TransactionFilter[] = ["all", "buy", "sell"];
export const TRANSACTION_FILTER_LABELS: Record<TransactionFilter, string> = {
  all: "All Transactions",
  buy: "Buy only",
  sell: "Sell only",
};

export interface InsiderFeed {
  readonly rows: readonly InsiderRow[];
  readonly top_weekly: readonly InsiderRow[];
  readonly top_10_owner_weekly: readonly InsiderRow[];
  readonly as_of: string | null;
}

/** The path the page fetches. */
export function insiderFeedPath(
  ownerType: OwnerType,
  transactionFilter: TransactionFilter,
): string {
  const params = new URLSearchParams({
    owner_type: ownerType,
    transaction_type: transactionFilter,
  });
  return `/market/insider?${params.toString()}`;
}

/** Parse the API response into a typed feed. Never throws. */
export function readInsiderFeed(body: unknown): InsiderFeed {
  if (typeof body !== "object" || body === null) {
    return { rows: [], top_weekly: [], top_10_owner_weekly: [], as_of: null };
  }
  const b = body as Record<string, unknown>;

  const readRow = (raw: unknown): InsiderRow | null => {
    if (typeof raw !== "object" || raw === null) return null;
    const r = raw as Record<string, unknown>;
    const symbol = typeof r.symbol === "string" ? r.symbol.trim().toUpperCase() : "";
    const owner = typeof r.owner === "string" ? r.owner.trim() : "";
    if (!symbol || !owner) return null;
    const ttype = r.transaction_type === "Buy" || r.transaction_type === "Sale"
      ? r.transaction_type
      : null;
    if (!ttype) return null;
    const rawOt = typeof r.owner_type === "string" ? r.owner_type : "insider";
    const ownerType = rawOt === "manager" ? "manager" : rawOt === "fund" ? "fund" : "insider";
    return {
      symbol,
      owner,
      owner_type: ownerType as InsiderRow["owner_type"],
      relationship: typeof r.relationship === "string" && r.relationship.trim().length > 0
        ? r.relationship.trim()
        : null,
      transaction_type: ttype,
      shares: typeof r.shares === "number" && Number.isFinite(r.shares) ? r.shares : null,
      value: typeof r.value === "number" && Number.isFinite(r.value) ? r.value : null,
      filing_date: typeof r.filing_date === "string" ? r.filing_date : "",
    };
  };

  const readList = (key: string): readonly InsiderRow[] => {
    const raw = b[key];
    if (!Array.isArray(raw)) return [];
    return raw.map(readRow).filter((r): r is InsiderRow => r !== null);
  };

  return {
    rows: readList("rows"),
    top_weekly: readList("top_weekly"),
    top_10_owner_weekly: readList("top_10_owner_weekly"),
    as_of: typeof b.as_of === "string" ? b.as_of : null,
  };
}