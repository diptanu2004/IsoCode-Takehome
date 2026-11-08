import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export type ReconStatus =
  | "return_not_received"
  | "unpaid_past_due"
  | "short_paid"
  | "freight_overcharged"
  | "overpaid"
  | "pending"
  | "within_tolerance"
  | "settled"
  | "not_applicable";

export type ClaimType = "return_not_received" | "unpaid_past_due" | "short_paid" | "freight_overcharged" | "systematic_gap";
export type ClaimStatus = "open" | "raised" | "partner_rejected" | "recovered" | "written_off" | "auto_resolved";
export type ClaimGroup = "live" | "open" | "raised" | "partner_rejected" | "closed";

export interface Partner {
  id: number;
  name: string;
  kind: "storefront" | "marketplace" | "quick_commerce" | "courier" | "internal";
  settlement_cycle_days: number;
  tolerance_paise: number;
  return_window_days: number;
}

export interface OrderRow {
  id: number;
  order_id: string;
  channel: string;
  courier: string | null;
  order_date: string;
  value_paise: number;
  payment_mode: "cod" | "prepaid";
  delivery_status: string;
  expected_paise: number;
  received_paise: number;
  diff_paise: number;
  tcs_paise: number;
  tds_paise: number;
  status: ReconStatus;
  issues: string[];
  due_date: string | null;
}

export interface Page<T> {
  rows: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface OrderDetail extends OrderRow {
  awb: string | null;
  zone: string | null;
  declared_weight_g: number | null;
  units: number;
  delivered_at: string | null;
  returned_at: string | null;
  computed_at: string;
  components: { component: string; label: string; expected_paise: number; actual_paise: number; gap_paise: number }[];
  lines: {
    component: string;
    amount_paise: number;
    txn_date: string;
    reference: string | null;
    billed_weight_g: number | null;
    zone: string | null;
    upload_id: number;
    row_no: number;
    partner: string;
    filename: string | null;
  }[];
  scans: { scanned_at: string; condition: string | null; upload_id: number; row_no: number; filename: string | null }[];
  claims: { id: number; type: ClaimType; amount_paise: number; status: ClaimStatus; aging_from: string; partner: string }[];
  rate_cards: { partner: string; version: number; effective_from: string }[];
}

export interface SourceRow {
  source: string;
  filename: string;
  row_no: number;
  cells: { column: string; value: string | null }[];
}

export interface ClaimRow {
  id: number;
  partner_id: number;
  partner: string;
  type: ClaimType;
  type_label: string;
  order_pk: number | null;
  order_id: string | null;
  component: string | null;
  period_month: string | null;
  amount_paise: number;
  recovered_paise: number;
  status: ClaimStatus;
  partner_ref: string | null;
  aging_from: string;
  age_days: number;
  opened_at: string;
}

export interface ClaimDetail extends ClaimRow {
  events: { from_status: string | null; to_status: string; note: string | null; at: string; by: string | null }[];
  /** The largest 200 orders behind a systematic claim; order_count is all of them. */
  orders: { id: number; order_id: string; amount_paise: number }[];
  order_count: number;
}

export interface PartnerTotal {
  partner_id: number;
  partner: string;
  live_claims: number;
  open_claims: number;
  outstanding_paise: number;
  recovered_paise: number;
}

export const STATUS_LABELS: Record<ReconStatus, string> = {
  return_not_received: "Return not received",
  unpaid_past_due: "Unpaid past due",
  short_paid: "Short-paid",
  freight_overcharged: "Freight overcharged",
  overpaid: "Overpaid",
  pending: "Pending",
  within_tolerance: "Within tolerance",
  settled: "Settled",
  not_applicable: "Cancelled",
};

export const PROBLEM_STATUSES: ReconStatus[] = [
  "return_not_received",
  "unpaid_past_due",
  "short_paid",
  "freight_overcharged",
];

export const CLAIM_TYPE_LABELS: Record<ClaimType, string> = {
  return_not_received: "Return not received",
  unpaid_past_due: "Unpaid past due",
  short_paid: "Short-paid",
  freight_overcharged: "Freight overcharged",
  systematic_gap: "Systematic small gap",
};

export const COMPONENT_LABELS: Record<string, string> = {
  sale: "Sale",
  commission: "Commission",
  fixed_fee: "Fixed fee",
  shipping_fee: "Shipping fee",
  return_shipping_fee: "Return shipping fee",
  adjustment: "Adjustment",
  freight_forward: "Forward freight",
  freight_return: "Return freight",
  cod_fee: "COD fee",
  tcs: "TCS",
  tds: "TDS",
};

export const CLAIM_STATUS_LABELS: Record<ClaimStatus, string> = {
  open: "Open",
  raised: "Raised",
  partner_rejected: "Rejected by partner",
  recovered: "Recovered",
  written_off: "Written off",
  auto_resolved: "Resolved by matching",
};

function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export interface OrderFilters {
  status?: string;
  partner_id?: string;
  date_from?: string;
  date_to?: string;
  q?: string;
  sort?: string;
  page?: string;
}

export function usePartners() {
  return useQuery({ queryKey: ["partners"], queryFn: () => api<Partner[]>("/partners"), staleTime: 60_000 });
}

export function useOrders(filters: OrderFilters) {
  return useQuery({
    queryKey: ["recon-orders", filters],
    queryFn: () => api<Page<OrderRow>>(`/recon/orders${query({ ...filters })}`),
    placeholderData: keepPreviousData,
  });
}

export function useStatusCounts(filters: OrderFilters) {
  const { status: _status, sort: _sort, page: _page, ...rest } = filters;
  return useQuery({
    queryKey: ["recon-summary", rest],
    queryFn: () =>
      api<{ status: ReconStatus; orders: number; diff_paise: number }[]>(`/recon/summary${query({ ...rest })}`),
    placeholderData: keepPreviousData,
  });
}

export function useOrderDetail(id: number | null) {
  return useQuery({
    queryKey: ["recon-order", id],
    queryFn: () => api<OrderDetail>(`/recon/orders/${id}`),
    enabled: id !== null,
  });
}

export function useSourceRow(uploadId: number, rowNo: number, enabled: boolean) {
  return useQuery({
    queryKey: ["source-row", uploadId, rowNo],
    queryFn: () => api<SourceRow>(`/recon/lines/${uploadId}/${rowNo}`),
    enabled,
    staleTime: Infinity,
  });
}

export function useClaims(filters: {
  group?: string;
  partner_id?: string;
  type?: string;
  age?: string;
  q?: string;
  page?: string;
}) {
  return useQuery({
    queryKey: ["claims", filters],
    queryFn: () => api<Page<ClaimRow> & { total_paise: number }>(`/claims${query({ ...filters })}`),
    placeholderData: keepPreviousData,
  });
}

export function usePartnerTotals() {
  return useQuery({ queryKey: ["claim-partners"], queryFn: () => api<PartnerTotal[]>("/claims/partners") });
}

export function useClaimDetail(id: number | null) {
  return useQuery({
    queryKey: ["claim", id],
    queryFn: () => api<ClaimDetail>(`/claims/${id}`),
    enabled: id !== null,
  });
}

function useInvalidateClaims() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all(
      ["claims", "claim", "claim-partners", "recon-order", "overview"].map((key) =>
        queryClient.invalidateQueries({ queryKey: [key] }),
      ),
    );
}

export function useClaimAction(id: number) {
  const invalidate = useInvalidateClaims();
  return useMutation({
    mutationFn: (body: { action: "raise" | "recover" | "reject" | "write_off"; partner_ref?: string; recovered_paise?: number }) =>
      api<{ id: number; status: ClaimStatus }>(`/claims/${id}/actions`, { json: body }),
    onSuccess: invalidate,
  });
}

export function useRaiseAll() {
  const invalidate = useInvalidateClaims();
  return useMutation({
    mutationFn: (body: { partner_id: number; partner_ref?: string }) =>
      api<{ raised: number }>("/claims/raise-all", { json: body }),
    onSuccess: invalidate,
  });
}

/** "1,409.50" or "₹1409.5" → 140950 paise; null when it isn't an amount. */
export function parseRupees(text: string): number | null {
  const cleaned = text.replace(/[₹,\s]/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return null;
  const [whole, fraction = ""] = cleaned.split(".");
  return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
}
