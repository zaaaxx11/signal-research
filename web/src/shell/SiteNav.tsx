/**
 * The shell's old name for its header.
 *
 * The header itself moved to `HeaderBar.tsx` when it gained the folding panel
 * and the day/night switch; this module stays so `@/shell/SiteNav` keeps
 * resolving for anything that still imports it. New code imports
 * `./HeaderBar`.
 */

export {
  HeaderBar as SiteNav,
  NAV_PANEL_ID,
  NAV_ROW_HEIGHT,
  type HeaderBarProps,
} from "./HeaderBar";
export type { HeaderBarProps as SiteNavProps } from "./HeaderBar";
