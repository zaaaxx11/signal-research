/**
 * The Groups surface, exported as a page rather than a route.
 *
 * `routes.tsx` and `nav.ts` are wired by the shell owner; this module exists so
 * the import is one line and does not reach into a file name.
 */

export { GroupsPage, GROUPS_RESERVED_HEIGHT, GROUPS_SKELETON_ROWS } from "./GroupsPage";
export type { GroupsPageProps } from "./GroupsPage";
export { GROUPS_REGION, groupsPath, readGroups, screenerHref } from "./groups";
export type { GroupBy, GroupRow, GroupsReport, GroupsSort } from "./groups";
export { useGroups } from "./useGroups";