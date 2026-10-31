import { describe, expect, it } from "vitest";

import { filterSchemaFixture } from "./__fixtures__/schema";
import { buildRegistry } from "./registry";
import {
  CompileError,
  compileGroup,
  rowsToGroup,
  toBuilder,
  type BuilderGroup,
} from "./compiler";

/**
 * The compiler's contract, in the API's own JSON.
 *
 * A three-condition OR group is the round-trip case the surface promises:
 * `Sector is Technology OR Healthcare OR Energy` has one API spelling, `in`.
 * The rest of these tests pin the ceiling: two fields cannot be ORed into one
 * request, so the compiler refuses and names both instead of emitting a body
 * that would 422.
 */
describe("the group compiler", () => {
  const registry = buildRegistry(filterSchemaFixture());

  it("compiles a 3-condition OR group to one `in` on that field", () => {
    const group: BuilderGroup = {
      kind: "group",
      match: "and",
      children: [
        {
          kind: "group",
          match: "or",
          children: [
            { kind: "condition", key: "sec", op: "eq", value: "Technology" },
            { kind: "condition", key: "sec", op: "eq", value: "Healthcare" },
            { kind: "condition", key: "sec", op: "eq", value: "Energy" },
          ],
        },
      ],
    };

    const compiled = compileGroup(group, registry);
    expect(JSON.stringify(compiled)).toBe(
      JSON.stringify([
        { key: "sec", op: "in", value: ["Technology", "Healthcare", "Energy"] },
      ]),
    );
  });

  it("round-trips: compile → toBuilder → compile is the same JSON", () => {
    const group: BuilderGroup = {
      kind: "group",
      match: "and",
      children: [
        { kind: "condition", key: "cap", op: "gte", value: 2_000_000_000 },
        {
          kind: "group",
          match: "or",
          children: [
            { kind: "condition", key: "sec", op: "eq", value: "Technology" },
            { kind: "condition", key: "sec", op: "eq", value: "Healthcare" },
            { kind: "condition", key: "sec", op: "eq", value: "Energy" },
          ],
        },
      ],
    };

    const first = compileGroup(group, registry);
    const second = compileGroup(toBuilder(first), registry);
    expect(JSON.stringify(second)).toBe(JSON.stringify(first));
    expect(JSON.stringify(first)).toBe(
      JSON.stringify([
        { key: "cap", op: "gte", value: 2_000_000_000 },
        { key: "sec", op: "in", value: ["Technology", "Healthcare", "Energy"] },
      ]),
    );
  });

  it("keeps two ANDed filters on one key as two predicates", () => {
    const compiled = compileGroup(
      toBuilder([
        { key: "cap", op: "gte", value: 2_000_000_000 },
        { key: "cap", op: "lt", value: 10_000_000_000 },
      ]),
      registry,
    );
    expect(compiled).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
      { key: "cap", op: "lt", value: 10_000_000_000 },
    ]);
  });

  it("refuses an OR across two fields, naming both", () => {
    const group: BuilderGroup = {
      kind: "group",
      match: "or",
      children: [
        { kind: "condition", key: "sec", op: "eq", value: "Technology" },
        { kind: "condition", key: "industry", op: "eq", value: "Semiconductors" },
      ],
    };

    expect(() => compileGroup(group, registry)).toThrow(CompileError);
    try {
      compileGroup(group, registry);
    } catch (error) {
      expect(error).toBeInstanceOf(CompileError);
      const failure = error as CompileError;
      expect(failure.code).toBe("or_across_fields");
      expect(failure.message).toMatch(/Sector/);
      expect(failure.message).toMatch(/Industry/);
      expect(failure.field).toMatch(/Sector/);
    }
  });

  it("refuses an OR of inequalities: the API cannot union ranges", () => {
    expect(() =>
      compileGroup(
        {
          kind: "group",
          match: "or",
          children: [
            { kind: "condition", key: "cap", op: "lt", value: 2_000_000_000 },
            { kind: "condition", key: "cap", op: "gt", value: 200_000_000_000 },
          ],
        },
        registry,
      ),
    ).toThrow(/any of.*one field|must be “is” or “any of”/s);
  });

  it("refuses an invalid value before producing any filter", () => {
    try {
      compileGroup(
        {
          kind: "group",
          match: "and",
          children: [
            { kind: "condition", key: "cap", op: "between", value: [10, 1] },
            { kind: "condition", key: "sec", op: "eq", value: "Technology" },
          ],
        },
        registry,
      );
      expect.unreachable("an inverted range must not compile");
    } catch (error) {
      expect(error).toBeInstanceOf(CompileError);
      expect((error as CompileError).code).toBe("invalid_value");
      expect((error as CompileError).message).toMatch(/Market Cap/);
    }
  });

  it("rowsToGroup turns a run of `any` rows into one OR group", () => {
    const group = rowsToGroup([
      { id: "1", connector: "and", key: "cap", op: "gte", value: "2000000000" },
      { id: "2", connector: "and", key: "sec", op: "eq", value: "Technology" },
      { id: "3", connector: "or", key: "sec", op: "eq", value: "Healthcare" },
      { id: "4", connector: "or", key: "sec", op: "eq", value: "Energy" },
      { id: "5", connector: "and", key: "sh_price", op: "gte", value: "5" },
    ]);

    expect(group.children).toHaveLength(3);
    expect(group.children[0]?.kind).toBe("condition");
    expect(group.children[1]?.kind).toBe("group");
    const or = group.children[1] as BuilderGroup;
    expect(or.match).toBe("or");
    expect(or.children).toHaveLength(3);
    expect(or.children.every((child) => child.kind === "condition" && child.key === "sec")).toBe(
      true,
    );
    expect(group.children[2]?.kind).toBe("condition");
  });

  it("deduplicates repeated alternatives", () => {
    const compiled = compileGroup(
      {
        kind: "group",
        match: "or",
        children: [
          { kind: "condition", key: "sec", op: "eq", value: "Technology" },
          { kind: "condition", key: "sec", op: "eq", value: "Technology" },
          { kind: "condition", key: "sec", op: "in", value: ["Healthcare", "Energy"] },
        ],
      },
      registry,
    );
    expect(compiled).toEqual([
      { key: "sec", op: "in", value: ["Technology", "Healthcare", "Energy"] },
    ]);
  });
});
