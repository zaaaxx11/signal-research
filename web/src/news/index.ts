/**
 * The News surface, exported as a page rather than a route.
 *
 * The shell owner wires `routes.tsx`, `nav.ts` and `lazyPages.ts`; this module
 * keeps that import to one line and one name.
 */

export { NewsPage, NEWS_SKELETON_ROWS, type NewsPageProps } from "./NewsPage";
export {
  CryptoMalformed,
  NEWS_LIMIT,
  NewsMalformed,
  cryptoPath,
  filterByCategory,
  filterByTime,
  filterItems,
  groupByDay,
  isIndexFundSymbol,
  newsPath,
  readCrypto,
  readNews,
  sortNewestFirst,
  sourceColor,
  type CategoryTab,
  type CryptoRow,
  type NewsGroup,
  type NewsItem,
  type NewsReport,
  type TimeWindow,
} from "./news";
export { useCrypto, useNews, type CryptoView, type NewsQuery, type NewsStatus, type NewsView } from "./useNews";
export {
  dayKeyOf,
  dayLabel,
  formatDayLabel,
  itemClock,
  matchesQuery,
  previousDay,
  relativeTime,
  safeExternalHref,
  todayUtc,
  type ItemClock,
} from "./format";