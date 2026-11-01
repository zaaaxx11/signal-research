/**
 * The shell's old name for its header.
 *
 * The header itself moved to `HeaderBar.tsx`; this module stays so
 * `@/shell/SiteNav` keeps resolving for anything that still imports it. New
 * code imports `./HeaderBar`.
 *
 * The folding panel is gone: the row shows all fourteen sections at every
 * width, so there is no `NAV_PANEL_ID` to re-export any more.
 */

export {
  HeaderBar as SiteNav,
  NAV_ROW_HEIGHT,
  type HeaderBarProps,
} from "./HeaderBar";
export type { HeaderBarProps as SiteNavProps } from "./HeaderBar";
