import { describe, expect, it } from "vitest";

import { filterSchemaFixture } from "./__fixtures__/schema";
import { buildRegistry } from "./registry";
import { toFiniteNumber, validateDraft } from "./validation";

/**
 * (a) An invalid value never reaches the API — and the refusal names the field
 * the reader is looking at, not the row column the server would name.
 */
describe("filter validation", () => {
  const registry = buildRegistry(filterSchemaFixture());

  const error = (key: string, op: Parameters<typeof validateDraft>[1]["op"], value: unknown) => {
    const result = validateDraft(registry, { key, op, value });
    expect(result.ok).toBe(false);
    return result.ok ? "" : result.error;
  };

  it("refuses an empty value and names the field", () => {
    expect(error("cap", "gte", "")).toMatch(/Market Cap/);
    expect(error("cap", "gte", "")).toMatch(/needs a value/);
    expect(error("sec", "eq", "   ")).toMatch(/Sector needs some text/);
  });

  it("refuses NaN, Infinity and non-numeric strings", () => {
    expect(error("cap", "gte", Number.NaN)).toMatch(/Market Cap\.? needs a number/);
    expect(error("cap", "gte", Number.POSITIVE_INFINITY)).toMatch(/needs a number/);
    expect(error("cap", "gte", "abc")).toMatch(/Market Cap\.? needs a number, not “abc”/);
    expect(error("fa_pe", "lt", "1e999")).toMatch(/needs a number/);
  });

  it("refuses an inverted range with both bounds in the message", () => {
    const message = error("cap", "between", [10_000_000_000, 2_000_000_000]);
    expect(message).toMatch(/Market Cap\.? has its range inverted/);
    expect(message).toMatch(/10000000000/);
    expect(message).toMatch(/2000000000/);
    expect(error("cap", "between", ["9", "1"])).toMatch(/inverted/);
  });

  it("refuses a malformed between and an empty `in`", () => {
    expect(error("cap", "between", [1])).toMatch(/two numbers/);
    expect(error("cap", "between", ["x", 2])).toMatch(/needs numbers/);
    expect(error("cap", "in", [])).toMatch(/at least one value/);
    expect(error("sec", "in", [])).toMatch(/at least one value/);
  });

  it("refuses an operator the field cannot take", () => {
    expect(error("cap", "contains", "cheap")).toMatch(/cannot use “contains”/);
    expect(error("sec", "gt", "Tech")).toMatch(/Sector cannot use “gt”/);
    expect(error("ta_perf", "gt", "1w10u")).toMatch(/Performance cannot use “gt”/);
  });

  it("refuses a key the schema marks refused, with the schema's reason", () => {
    const message = error("idx", "eq", "sp500");
    expect(message).toMatch(/^idx cannot be filtered/);
    expect(message).toMatch(/index membership/);
    expect(error("not_a_key", "eq", 1)).toMatch(/Unknown filter “not_a_key”/);
  });

  it("refuses a choice token that is not in the published vocabulary", () => {
    expect(error("ta_perf", "eq", "1w999u")).toMatch(/Performance has no option “1w999u”/);
    expect(error("ta_perf", "eq", "")).toMatch(/needs an option/);
  });

  it("coerces valid input to the API's types", () => {
    const scalar = validateDraft(registry, { key: "cap", op: "gte", value: "2000000000" });
    expect(scalar).toMatchObject({ ok: true, value: 2_000_000_000, op: "gte" });

    const range = validateDraft(registry, { key: "sh_price", op: "between", value: ["5", "20"] });
    expect(range).toMatchObject({ ok: true, value: [5, 20] });

    const list = validateDraft(registry, { key: "fa_pe", op: "in", value: "10, 20,30" });
    expect(list).toMatchObject({ ok: true, value: [10, 20, 30] });

    const text = validateDraft(registry, { key: "sec", op: "eq", value: " Technology " });
    expect(text).toMatchObject({ ok: true, value: "Technology" });

    const choice = validateDraft(registry, { key: "ta_perf", op: "eq", value: "1w10u" });
    expect(choice).toMatchObject({ ok: true, value: "1w10u" });
  });

  it("reads a finite number and nothing else", () => {
    expect(toFiniteNumber(" 12 ")).toBe(12);
    expect(toFiniteNumber("")).toBeNull();
    expect(toFiniteNumber("  ")).toBeNull();
    expect(toFiniteNumber(true)).toBeNull();
    expect(toFiniteNumber(Number.NaN)).toBeNull();
    expect(toFiniteNumber(Number.NEGATIVE_INFINITY)).toBeNull();
  });
});
