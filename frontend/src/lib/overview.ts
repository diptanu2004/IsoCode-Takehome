import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export interface PartnerRisk {
  partner_id: number;
  partner: string;
  by_age: number[];
  claims_by_age: number[];
  total_paise: number;
  claims: number;
}

export interface OverviewData {
  as_of: string;
  last_file_at: string | null;
  orders: number;
  at_risk: {
    buckets: string[];
    partners: PartnerRisk[];
    by_age: number[];
    total_paise: number;
    claims: number;
    not_raised_paise: number;
  };
  recovery: { recovered_paise: number; target_paise: number; written_off_paise: number; paid_late_paise: number };
  auto_match: { rate: number | null; target: number; matched_orders: number; unmatched_rows: number };
  within_tolerance: { orders: number; gap_paise: number; patterns: number };
  taxes: { partner_id: number; partner: string; tcs_paise: number; tds_paise: number }[];
}

/** "0-7" → "0–7 days", "61+" → "Over 60 days" */
export const AGE_LABELS: Record<string, string> = {
  "0-7": "0–7 days",
  "8-15": "8–15 days",
  "16-30": "16–30 days",
  "31-60": "31–60 days",
  "61+": "Over 60 days",
};

export function useOverview() {
  return useQuery({ queryKey: ["overview"], queryFn: () => api<OverviewData>("/overview") });
}

/** Five steps of shading for a heat cell: 0 for nothing, then 1 (least) to 5 (most). */
export function heatStep(value: number, max: number): number {
  if (value <= 0 || max <= 0) return 0;
  return Math.min(5, Math.max(1, Math.ceil((value / max) * 5)));
}
