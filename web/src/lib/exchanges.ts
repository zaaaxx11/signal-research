/**
 * Exchange codes, in the one place both surfaces can read them.
 *
 * The grid's `exchange` cell and the detail panel's profile group show the same
 * fact in two vocabularies: the grid carries the code the API filters on
 * (`XNAS`), and the detail carries whatever the security master said
 * (`Nasdaq`). Printing the raw code in a 104 px cell asked the reader to know
 * that `XNAS` is NASDAQ; printing a *name* on the wire would ask the API to
 * accept a spelling it does not.
 *
 * So the rule is: **the code is the contract, the label is display only.**
 * Everything here maps a code to a name for the eye. Nothing here is sent, and
 * no caller may write the result back into a filter, a URL or a request body —
 * `format.ts` keeps the code in the cell's `title` and `data-value`, and the
 * filter state keeps it in `ActiveFilter.value`.
 *
 * Three properties make one function safe to share:
 *
 * * **Idempotent over both vocabularies.** `XNAS` → `NASDAQ`, `xnas` → `NASDAQ`,
 *   `Nasdaq` → `Nasdaq`, `NASDAQ` → `NASDAQ`, `NYSE American` → `NYSE American`.
 *   A name is not a code, so it is returned untouched; that is what lets the
 *   detail panel call this on a payload that already says `Nasdaq`.
 * * **Never empty.** An unknown code falls back to itself. A formatter that
 *   returned `""` would delete the value from the row it was asked to describe
 *   (the detail panel's "reported fields" list counts non-empty cells).
 * * **Cheap.** One `Map` lookup per call — see the cost guard in
 *   `lib/exchanges.test.ts`. A formatter runs once per exchange cell per render,
 *   so anything with an allocation or an `Intl` constructor inside it would tax
 *   the format pass that everything else in the grid is tuned around.
 */

/**
 * Code → name, for every code the product has ever issued.
 *
 * The four US venues are the ones the screener itself writes (`mic_for`); the
 * rest are short codes from the global tab, which are not MICs at all, and the
 * table exists precisely because the screen has no other way to know that `SSE`
 * is Shanghai. `BCS` is deliberately absent: we do not know what it stands for,
 * so it falls back to itself rather than to a guess.
 */
const LABELS: Readonly<Record<string, string>> = {
  // US venues, in the order the menu draws them.
  XNAS: "NASDAQ",
  XNYS: "NYSE",
  XASE: "AMEX",
  XCBO: "CBOE",
  // The global tab's short codes.
  SSE: "Shanghai",
  SZSE: "Shenzhen",
  SIX: "SIX Swiss",
  LSE: "London",
  KRX: "Korea Exchange",
  HKEX: "Hong Kong",
  TWSE: "Taiwan",
  TSE: "Tokyo",
  XETR: "Xetra",
  TSX: "Toronto",
  TADAWUL: "Tadawul",
  EURONEXT: "Euronext",
};

/**
 * The menu's fixed order: the four venues that carry 99.9% of the US universe
 * first, in descending size, then everything else by name.
 *
 * A menu that reordered itself by the tape would be a menu the reader has to
 * re-read every minute; exchange has a natural priority (80% of rows are
 * NASDAQ), so it gets one. Sectors do not, which is why they sort
 * alphabetically.
 */
const VENUE_ORDER: readonly string[] = ["XNAS", "XNYS", "XASE", "XCBO"];

/** The lookup key: a code is case-insensitive, a name never enters the table. */
function codeKey(value: string): string {
  return value.trim().toUpperCase();
}

/**
 * `XNAS` → `NASDAQ`; a name, a blank or an unknown code → itself.
 *
 * The fallback is the input *as given*, not its upper-case form: the detail
 * panel passes names, and `Nasdaq` must not become `NASDAQ` just because the
 * table could not place it.
 */
export function exchangeLabel(code: string): string {
  const raw = String(code);
  const label = LABELS[codeKey(raw)];
  return label ?? raw;
}

/**
 * `XNAS` → `NASDAQ (XNAS)`: the name for the eye, the code for the copy-paste.
 *
 * When the label *is* the value (an unknown code, or a name that already reads
 * as one) there is nothing to disambiguate and no parentheses are added — a
 * title that says `Nasdaq (Nasdaq)` is noise.
 */
export function exchangeTitle(code: string): string {
  const raw = String(code).trim();
  const label = exchangeLabel(raw);
  return label === raw ? raw : `${label} (${raw})`;
}

/**
 * Codes in menu order: US majors first, then known codes by name, then unknown
 * codes by code. Duplicates collapse and the input array is never mutated.
 *
 * The normalisation is the *write* rule (trim, upper-case) because this list
 * becomes menu identity and, when the reader picks, a request value: the API
 * compares `exch` exactly, so `xnas` from a hand-written link that reached the
 * wire would match nothing.
 */
export function sortExchanges(codes: readonly string[]): string[] {
  const seen = new Set<string>();
  const unique: string[] = [];
  for (const code of codes) {
    const key = codeKey(String(code));
    if (key.length === 0 || seen.has(key)) continue;
    seen.add(key);
    unique.push(key);
  }
  return unique.sort(compareExchanges);
}

/** A total order, so the same set always draws the same menu. */
function compareExchanges(left: string, right: string): number {
  const leftRank = VENUE_ORDER.indexOf(left);
  const rightRank = VENUE_ORDER.indexOf(right);
  if (leftRank !== -1 || rightRank !== -1) {
    if (rightRank === -1) return -1;
    if (leftRank === -1) return 1;
    return leftRank - rightRank;
  }

  const leftLabel = exchangeLabel(left);
  const rightLabel = exchangeLabel(right);
  const leftKnown = leftLabel !== left;
  const rightKnown = rightLabel !== right;
  if (leftKnown !== rightKnown) return leftKnown ? -1 : 1;
  // Two unknown codes have no name to sort by; their code is the only label
  // they have, and it is the one the menu prints.
  return leftLabel.localeCompare(rightLabel) || left.localeCompare(right);
}
