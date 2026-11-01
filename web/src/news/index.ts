/**
 * The News surface, exported as a page rather than a route.
 *
 * The shell owner wires `routes.tsx`, `nav.ts` and `lazyPages.ts`; this module
 * keeps that import to one line and one name.
 */

export { NewsPage, NEWS_RESERVED_HEIGHT, NEWS_SKELETON_ROWS, type NewsPageProps } from "./NewsPage";
export {
  NEWS_LIMIT,
  NewsMalformed,
  filterItems,
  groupByDay,
  newsPath,
  readNews,
  sortNewestFirst,
  type NewsGroup,
  type NewsItem,
  type NewsReport,
} from "./news";
export { useNews, type NewsQuery, type NewsStatus, type NewsView } from "./useNews";
export {
  dayKeyOf,
  dayLabel,
  formatDayLabel,
  itemClock,
  matchesQuery,
  previousDay,
  safeExternalHref,
  todayUtc,
  type ItemClock,
} from "./format";
