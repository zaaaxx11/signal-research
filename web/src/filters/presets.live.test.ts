// @vitest-environment node
/**
 * The live half of the preset proof: every bucket is POSTed to :8099 and every
 * sampled row is checked **against the bucket's own predicate**.
 *
 * A `200` is not a proof of meaning. Phase 3 found two buckets that answered
 * `200` with the wrong population (`Under 10` was 66 % loss-makers; `Under 1%`
 * silently dropped every stock whose provider reports no yield), and both
 * passed the earlier `status === 200 && typeof total === "number"` test. So the
 * assertions here are membership, not transport:
 *
 *   * every row a bucket returns satisfies every predicate in that bucket;
 *   * a named symbol lands on the side of the boundary the label promises;
 *   * a null cell is a member of no numeric bucket, and the label says so.
 *
 * Skipped unless `LIVE_API=1`, because the default suite must not depend on a
 * running server. It is read-only: `POST /screens/select` is a selection.
 *
 *   LIVE_API=1 npx vitest run src/filters/presets.live.test.ts
 */

import { describe, expect, it } from "vitest";

import { parseFilterSchema, usableKeys } from "./schema";
import { buildRegistry, type FilterRegistry } from "./registry";
import { allBuckets, bucketAccepts } from "./presets";
import type { PresetBucket, PresetGroup, WireFilter } from "./types";

const LIVE = process.env["LIVE_API"] === "1";
const ORIGIN = process.env["LIVE_API_ORIGIN"] ?? "http://127.0.0.1:8099";
const LIMIT = 100;

interface Answer {
  status: number;
  total: number;
  universeTotal: number;
  rows: Array<Record<string, unknown>>;
}

async function post(body: unknown): Promise<Answer> {
  const response = await fetch(`${ORIGIN}/screens/select`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  let parsed: Record<string, unknown> = {};
  try {
    parsed = text ? (JSON.parse(text) as Record<string, unknown>) : {};
  } catch {
    // A proxy/500 body is text, not the selection envelope. Keep the status and
    // a snippet so the failure names the transport, not a JSON parser.
    parsed = { error: text.slice(0, 200) || "(empty body)" };
  }
  const rows = Array.isArray(parsed["rows"])
    ? (parsed["rows"] as Array<Record<string, unknown>>)
    : [];
  return {
    status: response.status,
    total: typeof parsed["total"] === "number" ? parsed["total"] : -1,
    universeTotal:
      typeof parsed["universe_total"] === "number" ? parsed["universe_total"] : -1,
    rows,
  };
}

/** A predicate as `criteria.filters` takes it: a key, or a raw row column. */
interface ProbeFilter {
  key?: string;
  field?: string;
  op: string;
  value: unknown;
}

function select(
  filters: readonly ProbeFilter[],
  columns: string[],
  offset = 0,
  limit = LIMIT,
): Promise<Answer> {
  return post({
    mode: "criteria",
    region: "us",
    asset_class: "stock",
    columns,
    criteria: { limit, offset, filters },
  });
}

/** One predicate, evaluated exactly as `app/screen/filters.py` does. */
function satisfies(value: unknown, filter: WireFilter): boolean {
  if (typeof value !== "number" || !Number.isFinite(value)) return false;
  const target = filter.value as number;
  switch (filter.op) {
    case "eq":
      return value === target;
    case "ne":
      return value !== target;
    case "gt":
      return value > target;
    case "gte":
      return value >= target;
    case "lt":
      return value < target;
    case "lte":
      return value <= target;
    case "between": {
      const [low, high] = filter.value as number[];
      return value >= low && value <= high;
    }
    default:
      return false;
  }
}

/** The row column a preset key filters, straight from the served schema. */
function columnFor(registry: FilterRegistry, key: string): string {
  const field = registry.byKey.get(key)?.field;
  if (!field) throw new Error(`live schema has no column for ${key}`);
  return field;
}

/** Every row of one symbol, read through the symbol column. */
async function rowsFor(
  symbols: string[],
  columns: string[],
): Promise<Map<string, Record<string, unknown>>> {
  const answer = await select(
    [{ field: "symbol", op: "in", value: symbols }],
    ["symbol", ...columns],
    0,
    50,
  );
  expect(answer.status, `symbol probe failed: ${JSON.stringify(symbols)}`).toBe(200);
  const bySymbol = new Map<string, Record<string, unknown>>();
  for (const row of answer.rows) {
    const symbol = row["symbol"];
    if (typeof symbol === "string") bySymbol.set(symbol, row);
  }
  return bySymbol;
}

describe.skipIf(!LIVE)("presets against the live API", () => {
  it("offers exactly the live schema's usable keys", async () => {
    const response = await fetch(`${ORIGIN}/screens/select/schema`);
    expect(response.status).toBe(200);
    const schema = parseFilterSchema(await response.json());
    const registry = buildRegistry(schema);
    const liveUsable = usableKeys(schema);

    console.log(
      `live registry: ${registry.entries.length} offered / ${liveUsable.length} usable / ` +
        `${Object.keys(registry.refused).length} refused`,
    );
    expect([...registry.usableKeys].sort()).toEqual([...liveUsable].sort());
  });

  it("every bucket's rows satisfy that bucket's own predicate", async () => {
    const response = await fetch(`${ORIGIN}/screens/select/schema`);
    const registry = buildRegistry(parseFilterSchema(await response.json()));
    const buckets = allBuckets();

    const failures: string[] = [];
    let checked = 0;

    for (const { group, bucket } of buckets) {
      const column = columnFor(registry, group.key);
      for (const filter of bucket.filters) {
        const valid = registry.byKey.get(filter.key);
        expect(valid, `${filter.key} is not a usable key`).toBeTruthy();
      }

      const first = await select(bucket.filters, ["symbol", column]);
      if (first.status !== 200) {
        failures.push(`${group.key}/${bucket.id}: HTTP ${first.status}`);
        continue;
      }

      // First page plus (when the answer is larger than one page) the last one,
      // so a bucket cannot pass by returning only its first 100 rows.
      const pages = [first];
      if (first.total > LIMIT) {
        pages.push(await select(bucket.filters, ["symbol", column], first.total - LIMIT));
      }

      let rows = 0;
      let matching = 0;
      for (const page of pages) {
        for (const row of page.rows) {
          rows += 1;
          const value = row[column];
          const ok = bucket.filters.every((filter) => satisfies(value, filter));
          if (ok) matching += 1;
          else {
            failures.push(
              `${group.key}/${bucket.id}: ${String(row["symbol"])} has ${column}=${String(value)}`,
            );
          }
          checked += 1;
        }
      }

      if (first.total === 0) failures.push(`${group.key}/${bucket.id}: matched 0 rows`);
      console.log(
        `  ${group.key.padEnd(12)} ${bucket.label.padEnd(24)} total ${String(first.total).padStart(5)} · ` +
          `rows ${String(rows).padStart(3)} · matching ${String(matching).padStart(3)}`,
      );
    }

    console.log(`\nrow checks: ${checked} · violations: ${failures.length}`);
    expect(failures).toEqual([]);
  }, 900_000);

  it("every named boundary symbol lands on the side its bucket promises", async () => {
    const response = await fetch(`${ORIGIN}/screens/select/schema`);
    const registry = buildRegistry(parseFilterSchema(await response.json()));
    const buckets = allBuckets();

    const pe = columnFor(registry, "fa_pe");
    const div = columnFor(registry, "fa_div");
    const cap = columnFor(registry, "cap");

    // A genuine sub-1 P/E, found live rather than assumed: the named proof for
    // adversarial (a) that excluding negatives did not exclude small positives.
    const cheap = await select(
      [
        { key: "fa_pe", op: "gte", value: 0.01 },
        { key: "fa_pe", op: "lt", value: 1 },
      ],
      ["symbol", pe],
      0,
      1,
    );
    expect(cheap.rows).toHaveLength(1);
    const cheapSymbol = cheap.rows[0]?.["symbol"] as string;
    const cheapPe = cheap.rows[0]?.[pe] as number;
    expect(cheapPe).toBeGreaterThanOrEqual(0.01);
    expect(cheapPe).toBeLessThan(1);

    const symbols = ["F", "TDG", "TSLA", "SAH", cheapSymbol];
    const rows = await rowsFor(symbols, [pe, div, cap]);
    for (const symbol of symbols) expect(rows.has(symbol), `${symbol} missing`).toBe(true);

    const of = (symbol: string) => rows.get(symbol) as Record<string, unknown>;

    // F: pe −6.55. It is in the universe and has a P/E under 10; only the
    // bucket's own guard can exclude it.
    expect(of("F")[pe] as number).toBeLessThan(0);
    const peBuckets = buckets.filter(({ group }) => group.key === "fa_pe");
    expect(bucketAccepts(bucketOf(peBuckets, "u10"), of("F")[pe] as number)).toBe(false);
    // Negative P/E is nobody's bucket.
    for (const { bucket } of peBuckets) {
      expect(bucketAccepts(bucket, of("F")[pe] as number), bucket.label).toBe(false);
    }
    // Adversarial (a): the smallest genuine positive P/E still is.
    expect(bucketAccepts(bucketOf(peBuckets, "u10"), cheapPe)).toBe(true);

    // TDG reports exactly 0.0: the zero bucket owns it, and it is still
    // under 1 % because 0 < 1.
    expect(of("TDG")[div]).toBe(0);
    const divBuckets = buckets.filter(({ group }) => group.key === "fa_div");
    expect(bucketAccepts(bucketOf(divBuckets, "none"), 0)).toBe(true);
    expect(bucketAccepts(bucketOf(divBuckets, "u1"), 0)).toBe(true);
    expect(bucketAccepts(bucketOf(divBuckets, "1-3"), 0)).toBe(false);

    // TSLA reports no yield at all (null). A null cell matches no numeric
    // predicate, so it is in no dividend bucket — which is why the group label
    // and hint say "reported" instead of claiming to be the whole market.
    expect(of("TSLA")[div]).toBeNull();
    for (const { bucket } of divBuckets) {
      expect(bucketAccepts(bucket, null), `${bucket.label} includes TSLA`).toBe(false);
    }

    // SAH's cap is exactly 1,999,003,845: under $2B by 996,155 dollars. The
    // $2B boundary is inclusive, which is why no bucket says "Over".
    expect(of("SAH")[cap]).toBe(1_999_003_845);
    const capBuckets = buckets.filter(({ group }) => group.key === "cap");
    expect(bucketAccepts(bucketOf(capBuckets, "micro"), 1_999_003_845)).toBe(true);
    expect(bucketAccepts(bucketOf(capBuckets, "small"), 1_999_003_845)).toBe(false);
    expect(bucketAccepts(bucketOf(capBuckets, "mega"), 200_000_000_000)).toBe(true);

    console.log(
      `named symbols: F pe ${String(of("F")[pe])} · ${cheapSymbol} pe ${cheapPe} · ` +
        `TDG div ${String(of("TDG")[div])} · TSLA div null · SAH cap ${String(of("SAH")[cap])}`,
    );
  }, 300_000);

  it("F1 live: 'Under 10' is the earners only, not the whole pe < 10 population", async () => {
    const under10 = await select([{ key: "fa_pe", op: "lt", value: 10 }], ["symbol"], 0, 1);
    const earners = await select(
      [
        { key: "fa_pe", op: "gte", value: 0 },
        { key: "fa_pe", op: "lt", value: 10 },
      ],
      ["symbol"],
      0,
      1,
    );
    const losses = await select([{ key: "fa_pe", op: "lt", value: 0 }], ["symbol"], 0, 1);

    expect(earners.total).toBeLessThan(under10.total);
    expect(earners.total + losses.total).toBe(under10.total);

    // The bucket the drawer now sends is the earners one.
    const bucket = bucketOf(
      allBuckets().filter(({ group }) => group.key === "fa_pe"),
      "u10",
    );
    expect(bucket.filters).toEqual(
      expect.arrayContaining([{ key: "fa_pe", op: "gte", value: 0 }]),
    );

    console.log(
      `F1 live: pe<10 = ${under10.total} = earners ${earners.total} + losses ${losses.total}`,
    );
  }, 120_000);

  it("F2 live: the dividend buckets account for every reported yield", async () => {
    const reported = await select([{ key: "fa_div", op: "gte", value: 0 }], ["symbol"], 0, 1);
    const under1 = await select(
      [
        { key: "fa_div", op: "gte", value: 0 },
        { key: "fa_div", op: "lt", value: 1 },
      ],
      ["symbol"],
      0,
      1,
    );
    const zero = await select([{ key: "fa_div", op: "eq", value: 0 }], ["symbol"], 0, 1);
    const universe = await select([], ["symbol"], 0, 1);

    const nonPayers = universe.total - reported.total;
    console.log(
      `F2 live: universe ${universe.total} · reported ${reported.total} · under 1% ${under1.total} ` +
        `· exactly 0.0 ${zero.total} · no reported yield ${nonPayers}`,
    );

    expect(reported.total).toBeGreaterThan(0);
    expect(under1.total + (reported.total - under1.total)).toBe(reported.total);
    expect(zero.total).toBeGreaterThan(0);
    expect(nonPayers).toBeGreaterThan(0);
    // A 0.0 payer is under 1 %; the label "(reported)" is what excludes the
    // non-payers, not a hidden predicate.
    expect(under1.total).toBeGreaterThan(zero.total);
  }, 120_000);
});

function bucketOf(list: Array<{ group: PresetGroup; bucket: PresetBucket }>, id: string): PresetBucket {
  const found = list.find(({ bucket }) => bucket.id === id);
  if (!found) throw new Error(`no bucket ${id}`);
  return found.bucket;
}
