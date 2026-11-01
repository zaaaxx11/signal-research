/**
 * The two local lists, exported as pages rather than routes.
 *
 * `routes.tsx` and `nav.ts` are wired by the shell owner; this module exists so
 * the import is one line and does not reach into a file name.
 */

export { WatchlistPage } from "./WatchlistPage";
export { PortfolioPage } from "./PortfolioPage";
export { LOCAL_ONLY_SENTENCE } from "./LocalNotice";
export {
  MAX_SYMBOLS,
  PORTFOLIO_KEY,
  WATCHLIST_KEY,
  decodePortfolio,
  decodeWatchlist,
  portfolioStore,
  watchlistStore,
} from "./storage";
export type { Holding, LocalStore } from "./storage";
export { useQuotes } from "./useQuotes";
export type { QuotesView } from "./useQuotes";
