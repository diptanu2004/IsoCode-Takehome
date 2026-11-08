import { describe, expect, it } from "vitest";
import { parseRupees } from "./recon";

describe("parseRupees", () => {
  it("reads what a person types as an amount", () => {
    expect(parseRupees("1409.50")).toBe(140950);
    expect(parseRupees("₹1,409.5")).toBe(140950);
    expect(parseRupees(" 30 ")).toBe(3000);
    expect(parseRupees("0.09")).toBe(9);
  });

  it("refuses anything that isn't a plain amount", () => {
    for (const bad of ["", "abc", "-30", "1.234", "1e3", "12.3.4"]) expect(parseRupees(bad)).toBeNull();
  });
});

describe("heatStep", () => {
  it("shades nothing for zero and splits the rest into five steps", async () => {
    const { heatStep } = await import("./overview");
    expect(heatStep(0, 100)).toBe(0);
    expect(heatStep(1, 100)).toBe(1);
    expect(heatStep(20, 100)).toBe(1);
    expect(heatStep(21, 100)).toBe(2);
    expect(heatStep(100, 100)).toBe(5);
    expect(heatStep(5, 0)).toBe(0);
  });
});
