/**
 * The id the skip link points at.
 *
 * It is the wrapper that holds whatever occupies the content column — the grid,
 * its error state or its skeleton — rather than the `<table>` itself, because a
 * skip link whose target only exists in the happy state is a link that dies
 * exactly when the panel is showing an error. The wrapper always exists.
 *
 * It lives in its own module so the shell (which renders the link) and the
 * screener (which renders the target) can both name it without importing each
 * other.
 */
export const GRID_REGION_ID = "grid-region";
