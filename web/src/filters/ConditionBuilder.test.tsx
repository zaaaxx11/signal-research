import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { filterSchemaFixture } from "./__fixtures__/schema";
import { buildRegistry } from "./registry";
import { ConditionBuilder } from "./ConditionBuilder";

const registry = buildRegistry(filterSchemaFixture());

function setup() {
  const onAdd = vi.fn();
  render(<ConditionBuilder registry={registry} onAdd={onAdd} />);
  return { onAdd };
}

describe("the advanced builder", () => {
  it("(a) refuses an inverted range, names the field, and cannot add it", async () => {
    const user = userEvent.setup();
    const { onAdd } = setup();

    await user.click(screen.getByRole("button", { name: "Add condition" }));
    await user.selectOptions(screen.getByLabelText("Filter 1 field"), "cap");
    await user.selectOptions(screen.getByLabelText("Filter 1 operator"), "between");
    await user.type(screen.getByLabelText(/^Market Cap\. minimum/), "10000000000");
    await user.type(screen.getByLabelText(/^Market Cap\. maximum/), "2000000000");

    expect(screen.getByRole("alert").textContent).toMatch(/Market Cap\./);
    expect(screen.getByRole("alert").textContent).toMatch(/range inverted/);
    expect(
      (screen.getByRole("button", { name: "Add to screen" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(onAdd).not.toHaveBeenCalled();
  });

  it("(a) refuses an empty or non-numeric value before the API sees it", async () => {
    const user = userEvent.setup();
    setup();

    await user.click(screen.getByRole("button", { name: "Add condition" }));
    await user.selectOptions(screen.getByLabelText("Filter 1 field"), "cap");
    await user.type(screen.getByLabelText(/^Market Cap\. value/), "abc");

    expect(screen.getByRole("alert").textContent).toMatch(/Market Cap\. needs a number/);
    expect(
      (screen.getByRole("button", { name: "Add to screen" }) as HTMLButtonElement).disabled,
    ).toBe(true);

    await user.clear(screen.getByLabelText(/^Market Cap\. value/));
    expect(screen.getByRole("alert").textContent).toMatch(/needs a value/);
    expect(
      (screen.getByRole("button", { name: "Add to screen" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("adds a single condition as one filter", async () => {
    const user = userEvent.setup();
    const { onAdd } = setup();

    await user.click(screen.getByRole("button", { name: "Add condition" }));
    await user.selectOptions(screen.getByLabelText("Filter 1 field"), "sec");
    await user.type(screen.getByLabelText(/^Sector value/), "Technology");
    await user.click(screen.getByRole("button", { name: "Add to screen" }));

    expect(onAdd).toHaveBeenCalledWith([{ key: "sec", op: "eq", value: "Technology" }]);
    expect(screen.queryByLabelText("Filter 1 field")).toBeNull();
  });

  it("(d) keeps two conditions on one key as two filters", async () => {
    const user = userEvent.setup();
    const { onAdd } = setup();

    await user.click(screen.getByRole("button", { name: "Add condition" }));
    await user.selectOptions(screen.getByLabelText("Filter 1 field"), "cap");
    await user.type(screen.getByLabelText(/^Market Cap\. value/), "2000000000");

    await user.click(screen.getByRole("button", { name: "Add condition" }));
    await user.selectOptions(screen.getByLabelText("Filter 2 field"), "cap");
    await user.selectOptions(screen.getByLabelText("Filter 2 operator"), "lt");
    await user.type(screen.getAllByLabelText(/^Market Cap\. value/)[1] as HTMLElement, "10000000000");

    await user.click(screen.getByRole("button", { name: "Add to screen" }));
    expect(onAdd).toHaveBeenCalledWith([
      { key: "cap", op: "gte", value: 2_000_000_000 },
      { key: "cap", op: "lt", value: 10_000_000_000 },
    ]);
  });

  it("compiles a run of `any` rows into one `in` on that field", async () => {
    const user = userEvent.setup();
    const { onAdd } = setup();

    await user.click(screen.getByRole("button", { name: "Add condition" }));
    await user.selectOptions(screen.getByLabelText("Filter 1 field"), "sec");
    await user.type(screen.getByLabelText(/^Sector value/), "Technology");

    await user.click(screen.getByRole("button", { name: "Add condition" }));
    await user.selectOptions(screen.getByLabelText("Match condition 2"), "or");
    await user.selectOptions(screen.getByLabelText("Filter 2 field"), "sec");
    await user.type(screen.getAllByLabelText(/^Sector value/)[1] as HTMLElement, "Healthcare");

    await user.click(screen.getByRole("button", { name: "Add to screen" }));
    expect(onAdd).toHaveBeenCalledWith([
      { key: "sec", op: "in", value: ["Technology", "Healthcare"] },
    ]);
  });

  it("shows the compiler's refusal when an OR spans two fields", async () => {
    const user = userEvent.setup();
    const { onAdd } = setup();

    await user.click(screen.getByRole("button", { name: "Add condition" }));
    await user.selectOptions(screen.getByLabelText("Filter 1 field"), "sec");
    await user.type(screen.getByLabelText(/^Sector value/), "Technology");

    await user.click(screen.getByRole("button", { name: "Add condition" }));
    await user.selectOptions(screen.getByLabelText("Match condition 2"), "or");
    await user.selectOptions(screen.getByLabelText("Filter 2 field"), "industry");
    await user.type(screen.getByLabelText(/^Industry value/), "Semiconductors");

    await user.click(screen.getByRole("button", { name: "Add to screen" }));

    expect(onAdd).not.toHaveBeenCalled();
    expect(screen.getAllByRole("alert").at(-1)?.textContent).toMatch(/Sector and Industry/);
    expect(screen.getAllByRole("alert").at(-1)?.textContent).toMatch(/ANDs filters/);
  });
});
