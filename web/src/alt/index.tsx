/**
 * The alt surfaces, exported as pages rather than routes.
 *
 * One component does the work; the three wrappers exist so the shell can mount
 * `CryptoPage`, `FuturesPage` and `ForexPage` by name without knowing that the
 * class is a prop. The parent wires `routes.tsx` and `nav.ts`; nothing here
 * touches either.
 */

import { AltPage } from "./AltPage";

export { ALT_RESERVED_HEIGHT, ALT_SKELETON_ROWS, AltPage } from "./AltPage";
export type { AltPageProps } from "./AltPage";
export {
  ALT_PER,
  ALT_SYMBOLS_LINKABLE,
  DEFAULT_TIMEFRAME,
  MARKET_CLASSES,
  TIMEFRAME_SPECS,
  TIMEFRAME_TABS,
  altPath,
  classCopy,
  filterRows,
  formatPrice,
  formatReadAt,
  formatVolume,
  nextSort,
  pricePrecision,
  readAlt,
  sortRows,
  stockHref,
  visibleRows,
} from "./alt";
export type { AltReport, AltRow, ClassCopy, MarketClass, SortDir, SortKey, SortState, Timeframe } from "./alt";
export { useAlt } from "./useAlt";
export type { AltStatus, AltView } from "./useAlt";

export function CryptoPage() {
  return <AltPage marketClass="crypto" />;
}

export function FuturesPage() {
  return <AltPage marketClass="futures" />;
}

export function ForexPage() {
  return <AltPage marketClass="forex" />;
}
