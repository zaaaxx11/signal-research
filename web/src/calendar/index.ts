/**
 * The Calendar surface, exported as a page rather than a route.
 *
 * The shell owner wires `routes.tsx` and `nav.ts`; this module keeps the import
 * to one line and one name.
 */

export { CalendarPage, CALENDAR_RESERVED_HEIGHT, CALENDAR_SKELETON_ROWS } from "./CalendarPage";
export type { CalendarPageProps } from "./CalendarPage";
export {
  CALENDAR_CAP,
  CALENDAR_DAYS,
  CALENDAR_REGION,
  calendarPath,
  dayLabel,
  overflowCount,
  readCalendar,
  shownSymbols,
  todayUtc,
} from "./calendar";
export type { CalendarBucket, CalendarReport, CalendarSymbol } from "./calendar";
export { useCalendar } from "./useCalendar";
