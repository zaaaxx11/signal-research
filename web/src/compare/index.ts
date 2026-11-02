/**
 * The comparison page, as the shell's route table imports it.
 *
 * One chunk: the page, its table, its one read and its URL rules. A reader who
 * never opens a comparison never downloads any of it.
 */
export { ComparePage } from "./ComparePage";
export { COMPARE_GROUPS, COMPARE_METRICS } from "./metrics";
export { MAX_COMPARE, MIN_COMPARE, compareHref, symbolsFromSearch } from "./symbols";
