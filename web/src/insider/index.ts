/**
 * The Insider surface, exported as a page rather than a route.
 *
 * `routes.tsx`, `nav.ts` and the lazy loader are wired by the shell owner; this
 * module exists so the import is one line and does not reach into a file name.
 *
 * There is no ranked read here on purpose: the vendor field this page used to
 * rank on cannot be interpreted as a flow (see `InsiderPage.tsx`). What is
 * exported is the filings reader and the panel that renders it.
 */

export { InsiderPage } from "./InsiderPage";
export { InsiderDetailPage } from "./InsiderDetailPage";
export { InsiderDetail, INSIDER_DETAIL_NOTE, INSIDER_DETAIL_TITLE_ID } from "./InsiderDetail";
export { FilingsMalformed, filingsPath, readFilings } from "./filings";
export type { InsiderField, InsiderFiling, InsiderFilings } from "./filings";
export { dayText, safeHref } from "./format";
export { useInsiderFilings } from "./useInsiderFilings";
export type { FilingsStatus, FilingsView } from "./useInsiderFilings";
