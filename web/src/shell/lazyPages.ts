/**
 * The three chart-shaped pages, each in its own chunk.
 *
 * They are lazy for the same reason the detail panel is: a reader who opens the
 * screener should not download a treemap layout engine, and a reader who opens
 * the map should not download the grid. Every loader is a static import
 * expression so the bundler can see the module it splits.
 */
export const loadMapPage = () => import("@/map").then((module) => ({ default: module.MapPage }));
export const loadGroupsPage = () =>
  import("@/groups").then((module) => ({ default: module.GroupsPage }));
export const loadCalendarPage = () =>
  import("@/calendar").then((module) => ({ default: module.CalendarPage }));
export const loadChartsPage = () =>
  import("@/charts").then((module) => ({ default: module.ChartsPage }));
export const loadInsiderPage = () =>
  import("@/insider").then((module) => ({ default: module.InsiderPage }));
export const loadInsiderDetailPage = () =>
  import("@/insider").then((module) => ({ default: module.InsiderDetailPage }));
export const loadCryptoPage = () => import("@/alt").then((m) => ({ default: m.CryptoPage }));
export const loadCryptoDetailPage = () =>
  import("@/crypto/CryptoDetailPage").then((m) => ({ default: m.CryptoDetailPage }));
export const loadFuturesPage = () => import("@/alt").then((m) => ({ default: m.FuturesPage }));
export const loadForexPage = () => import("@/alt").then((m) => ({ default: m.ForexPage }));
export const loadWatchlistPage = () =>
  import("@/lists").then((m) => ({ default: m.WatchlistPage }));
export const loadPortfolioPage = () =>
  import("@/lists").then((m) => ({ default: m.PortfolioPage }));
export const loadNewsPage = () => import("@/news").then((m) => ({ default: m.NewsPage }));
export const loadComparePage = () => import("@/compare").then((m) => ({ default: m.ComparePage }));
// The three pages a reader lands on most are split too: their chart, crypto and
// icon dependencies ride in their own chunk, not the initial one.
export const loadHomePage = () =>
  import("@/home/HomePage").then((m) => ({ default: m.HomePage }));
export const loadScreenerScreen = () =>
  import("@/screener/ScreenerScreen").then((m) => ({ default: m.ScreenerScreen }));
export const loadStockPage = () =>
  import("@/stock/StockPage").then((m) => ({ default: m.StockPage }));
