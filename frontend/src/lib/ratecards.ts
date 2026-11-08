import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export interface MarketplaceRules {
  type: "marketplace";
  commission_pct: number;
  commission_rounding: "paise" | "rupee";
  fixed_fee: number;
  shipping: { slab_g: number; first: number; additional: number } | null;
  on_return: { refund_commission: boolean; refund_fixed_fee: boolean; return_shipping_pct_of_forward: number };
}

export interface CourierRules {
  type: "courier";
  slab_g: number;
  forward: Record<string, { first: number; additional: number }>;
  return_pct_of_forward: number;
  cod_fee: { pct: number; min: number };
}

export type Rules = MarketplaceRules | CourierRules;

export interface CardVersion {
  id: number;
  version: number;
  effective_from: string;
  effective_to: string | null;
  rules: Rules;
  note: string | null;
  created_by: string | null;
  created_at: string;
  orders_priced: number;
}

export interface PartnerCards {
  partner_id: number;
  partner: string;
  kind: "marketplace" | "quick_commerce" | "courier";
  versions: CardVersion[];
  suspected: { claim_id: number; component: string; period_month: string; amount_paise: number }[];
}

export interface SavedVersion {
  id: number;
  version: number;
  orders_matched: number;
  claims_opened: number;
  claims_updated: number;
  claims_resolved: number;
}

export const ZONES = ["A", "B", "C", "D", "E"] as const;

export const KIND_LABELS: Record<PartnerCards["kind"], string> = {
  marketplace: "Marketplace",
  quick_commerce: "Quick commerce",
  courier: "Courier",
};

export function useRateCards() {
  return useQuery({ queryKey: ["rate-cards"], queryFn: () => api<PartnerCards[]>("/rate-cards") });
}

export function useAddVersion(partnerId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { effective_from: string; rules: Rules; note: string }) =>
      api<SavedVersion>(`/rate-cards/${partnerId}`, { json: body }),
    // Orders were re-matched, so everything built on their results is stale.
    onSuccess: () => queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "me" }),
  });
}

/** Form values for a rules object: every number as text, so a field can be empty while typing. */
export type RuleForm = Record<string, string>;

export function toForm(rules: Rules): RuleForm {
  if (rules.type === "marketplace") {
    return {
      commission_pct: String(rules.commission_pct),
      commission_rounding: rules.commission_rounding,
      fixed_fee: String(rules.fixed_fee),
      shipping: rules.shipping ? "yes" : "no",
      "shipping.slab_g": String(rules.shipping?.slab_g ?? 500),
      "shipping.first": String(rules.shipping?.first ?? 0),
      "shipping.additional": String(rules.shipping?.additional ?? 0),
      "on_return.refund_commission": rules.on_return.refund_commission ? "yes" : "no",
      "on_return.refund_fixed_fee": rules.on_return.refund_fixed_fee ? "yes" : "no",
      "on_return.return_shipping_pct_of_forward": String(rules.on_return.return_shipping_pct_of_forward),
    };
  }
  const form: RuleForm = {
    slab_g: String(rules.slab_g),
    return_pct_of_forward: String(rules.return_pct_of_forward),
    "cod_fee.pct": String(rules.cod_fee.pct),
    "cod_fee.min": String(rules.cod_fee.min),
  };
  for (const zone of ZONES) {
    form[`forward.${zone}.first`] = rules.forward[zone] ? String(rules.forward[zone].first) : "";
    form[`forward.${zone}.additional`] = rules.forward[zone] ? String(rules.forward[zone].additional) : "";
  }
  return form;
}

/** The rules a form describes, or the first problem with it. */
export function fromForm(type: Rules["type"], form: RuleForm): { rules: Rules } | { problem: string } {
  const problems: string[] = [];
  const num = (key: string, label: string, max = 100_000): number => {
    const text = (form[key] ?? "").trim();
    const value = Number(text);
    if (text === "" || !Number.isFinite(value) || value < 0 || value > max) {
      problems.push(`${label} must be a number from 0 to ${max.toLocaleString("en-IN")}.`);
    }
    return value;
  };

  let rules: Rules;
  if (type === "marketplace") {
    rules = {
      type,
      commission_pct: num("commission_pct", "Commission", 100),
      commission_rounding: form.commission_rounding === "rupee" ? "rupee" : "paise",
      fixed_fee: num("fixed_fee", "Fixed fee"),
      shipping:
        form.shipping === "yes"
          ? {
              slab_g: num("shipping.slab_g", "Shipping slab", 50_000),
              first: num("shipping.first", "Shipping for the first slab"),
              additional: num("shipping.additional", "Shipping for each extra slab"),
            }
          : null,
      on_return: {
        refund_commission: form["on_return.refund_commission"] === "yes",
        refund_fixed_fee: form["on_return.refund_fixed_fee"] === "yes",
        return_shipping_pct_of_forward: num("on_return.return_shipping_pct_of_forward", "Return shipping", 100),
      },
    };
  } else {
    const forward: CourierRules["forward"] = {};
    for (const zone of ZONES) {
      const first = (form[`forward.${zone}.first`] ?? "").trim();
      const additional = (form[`forward.${zone}.additional`] ?? "").trim();
      if (!first && !additional) continue; // the courier doesn't serve this zone
      forward[zone] = {
        first: num(`forward.${zone}.first`, `Zone ${zone} first slab`),
        additional: num(`forward.${zone}.additional`, `Zone ${zone} extra slab`),
      };
    }
    if (!Object.keys(forward).length) problems.push("Enter freight for at least one zone.");
    rules = {
      type,
      slab_g: num("slab_g", "Weight slab", 50_000),
      forward,
      return_pct_of_forward: num("return_pct_of_forward", "Return freight", 200),
      cod_fee: { pct: num("cod_fee.pct", "COD fee", 100), min: num("cod_fee.min", "COD minimum") },
    };
    if (rules.slab_g === 0) problems.push("Weight slab must be more than 0 g.");
  }
  if (rules.type === "marketplace" && rules.shipping?.slab_g === 0) problems.push("Shipping slab must be more than 0 g.");
  return problems.length ? { problem: problems[0] } : { rules };
}
