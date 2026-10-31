/**
 * One real answer from `GET /market/breadth?region=us&asset_class=stock`.
 *
 * Copied verbatim from the running scanner (as_of 2026-10-31T09:25:02+00:00), not hand-written: the
 * tests that read it are only worth something if the shape is the shape the
 * route actually emits. `LIVE_BREADTH` is annotated with the interface, so a
 * field the API adds or renames breaks compilation rather than silently
 * rendering as `undefined`.
 *
 * The three buckets in this snapshot sum to `universe_total` (5,678) and every
 * sector carries a measured average — the adversarial cases (a null average, a
 * long name, counts that do not add up) are built by overriding this document
 * in the tests, so they stay visibly synthetic.
 */

import type { BreadthReport } from "../report";

export const LIVE_BREADTH: BreadthReport = {
  "as_of": "2026-10-31T09:25:02+00:00",
  "region": "us",
  "asset_class": "stock",
  "source": "scan:us:stock",
  "stale": false,
  "universe_total": 5678,
  "advancing": 1557,
  "declining": 3932,
  "unchanged": 189,
  "unchanged_reported": 186,
  "unchanged_missing": 3,
  "new_highs": 45,
  "new_lows": 54,
  "new_highs_denominator": 5675,
  "new_lows_denominator": 5675,
  "sector_missing": 0,
  "method": {
    "unchanged": "change_pct == 0.0 or absent; a missing change is counted here, never as a decline, and reported separately as unchanged_missing",
    "sector_avg_change_pct": "arithmetic mean over the rows of the sector carrying a numeric change_pct; a row without one is excluded from the mean and stays in count",
    "new_highs": "price >= high_52w, counted over rows carrying both; new_highs_denominator is that row count",
    "new_lows": "price <= low_52w, counted over rows carrying both; new_lows_denominator is that row count",
    "sectors": "one bucket per provider sector label plus a null bucket for rows the provider did not label, so sector counts sum to universe_total"
  },
  "sectors": [
    {
      "name": "Finance",
      "avg_change_pct": -1.0998602989015587,
      "count": 1610,
      "advancing": 452,
      "declining": 1039
    },
    {
      "name": "Health Technology",
      "avg_change_pct": -3.4409887258629213,
      "count": 830,
      "advancing": 154,
      "declining": 662
    },
    {
      "name": "Technology Services",
      "avg_change_pct": -2.884843134688338,
      "count": 576,
      "advancing": 132,
      "declining": 434
    },
    {
      "name": "Electronic Technology",
      "avg_change_pct": -5.377373173413726,
      "count": 338,
      "advancing": 23,
      "declining": 314
    },
    {
      "name": "Producer Manufacturing",
      "avg_change_pct": -3.2632775512202437,
      "count": 295,
      "advancing": 55,
      "declining": 237
    },
    {
      "name": "Commercial Services",
      "avg_change_pct": -2.076977148666317,
      "count": 226,
      "advancing": 87,
      "declining": 135
    },
    {
      "name": "Consumer Services",
      "avg_change_pct": -0.5901937879081953,
      "count": 187,
      "advancing": 74,
      "declining": 108
    },
    {
      "name": "Non-Energy Minerals",
      "avg_change_pct": -4.5341329525848115,
      "count": 180,
      "advancing": 17,
      "declining": 162
    },
    {
      "name": "Process Industries",
      "avg_change_pct": -1.0332899258294739,
      "count": 163,
      "advancing": 67,
      "declining": 91
    },
    {
      "name": "Industrial Services",
      "avg_change_pct": -1.181154907822764,
      "count": 157,
      "advancing": 55,
      "declining": 101
    },
    {
      "name": "Retail Trade",
      "avg_change_pct": -1.170191134641322,
      "count": 156,
      "advancing": 53,
      "declining": 101
    },
    {
      "name": "Transportation",
      "avg_change_pct": -1.0497288033613255,
      "count": 156,
      "advancing": 46,
      "declining": 105
    },
    {
      "name": "Consumer Non-Durables",
      "avg_change_pct": 0.47654977498465584,
      "count": 136,
      "advancing": 75,
      "declining": 58
    },
    {
      "name": "Consumer Durables",
      "avg_change_pct": -2.02035977232512,
      "count": 127,
      "advancing": 37,
      "declining": 89
    },
    {
      "name": "Utilities",
      "avg_change_pct": 0.019946197715968218,
      "count": 119,
      "advancing": 68,
      "declining": 50
    },
    {
      "name": "Miscellaneous",
      "avg_change_pct": -1.0581866770570358,
      "count": 105,
      "advancing": 35,
      "declining": 66
    },
    {
      "name": "Energy Minerals",
      "avg_change_pct": 0.6927571060835745,
      "count": 102,
      "advancing": 67,
      "declining": 34
    },
    {
      "name": "Distribution Services",
      "avg_change_pct": -2.5078072442855683,
      "count": 98,
      "advancing": 27,
      "declining": 66
    },
    {
      "name": "Health Services",
      "avg_change_pct": -1.8583037054377212,
      "count": 80,
      "advancing": 17,
      "declining": 61
    },
    {
      "name": "Communications",
      "avg_change_pct": -0.7452017911563535,
      "count": 36,
      "advancing": 16,
      "declining": 18
    },
    {
      "name": "Government",
      "avg_change_pct": -0.387931034482758,
      "count": 1,
      "advancing": 0,
      "declining": 1
    }
  ]
};
