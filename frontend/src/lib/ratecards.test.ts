import { describe, expect, it } from "vitest";
import { fromForm, toForm, type CourierRules, type MarketplaceRules } from "./ratecards";

const marketplace: MarketplaceRules = {
  type: "marketplace",
  commission_pct: 15,
  commission_rounding: "rupee",
  fixed_fee: 30,
  shipping: { slab_g: 500, first: 90, additional: 40 },
  on_return: { refund_commission: true, refund_fixed_fee: false, return_shipping_pct_of_forward: 100 },
};

const courier: CourierRules = {
  type: "courier",
  slab_g: 500,
  forward: { A: { first: 30, additional: 15 }, C: { first: 48, additional: 23 } },
  return_pct_of_forward: 100,
  cod_fee: { pct: 2, min: 30 },
};

describe("rate card form", () => {
  it("turns rules into fields and back unchanged", () => {
    expect(fromForm("marketplace", toForm(marketplace))).toEqual({ rules: marketplace });
    expect(fromForm("courier", toForm(courier))).toEqual({ rules: courier });
  });

  it("keeps an edit and drops shipping when it's switched off", () => {
    const form = { ...toForm(marketplace), commission_pct: "12.5", shipping: "no" };
    const built = fromForm("marketplace", form);
    expect(built).toEqual({ rules: { ...marketplace, commission_pct: 12.5, shipping: null } });
  });

  it("leaves out zones with no freight", () => {
    const built = fromForm("courier", toForm(courier));
    expect("rules" in built && Object.keys((built.rules as CourierRules).forward)).toEqual(["A", "C"]);
  });

  it("explains the first problem", () => {
    expect(fromForm("marketplace", { ...toForm(marketplace), commission_pct: "" })).toEqual({
      problem: "Commission must be a number from 0 to 100.",
    });
    expect(fromForm("marketplace", { ...toForm(marketplace), fixed_fee: "-5" })).toHaveProperty("problem");
    const noZones = { ...toForm(courier), "forward.A.first": "", "forward.A.additional": "", "forward.C.first": "", "forward.C.additional": "" };
    expect(fromForm("courier", noZones)).toEqual({ problem: "Enter freight for at least one zone." });
  });
});
