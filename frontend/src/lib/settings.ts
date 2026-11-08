import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Role } from "../auth/session";
import { api } from "./api";
import type { Partner } from "./recon";

export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
  is_active: boolean;
  created_at: string;
}

export interface AuditEntry {
  id: number;
  at: string;
  actor: string | null;
  action: string;
  entity: string;
  entity_id: string;
  subject: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface Rematched {
  orders_matched: number;
  claims_opened: number;
  claims_updated: number;
  claims_resolved: number;
}

export function useUsers(enabled: boolean) {
  return useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("/users"), enabled });
}

export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; name: string; role: Role; password: string }) =>
      api<User>("/users", { json: body }),
    onSuccess: () => Promise.all(["users", "audit"].map((k) => queryClient.invalidateQueries({ queryKey: [k] }))),
  });
}

export function useChangeUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: number; role?: Role; is_active?: boolean }) =>
      api<User>(`/users/${id}`, { method: "PATCH", json: body }),
    onSuccess: () => Promise.all(["users", "audit"].map((k) => queryClient.invalidateQueries({ queryKey: [k] }))),
  });
}

export function useChangePartner() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      ...body
    }: {
      id: number;
      settlement_cycle_days?: number;
      tolerance_paise?: number;
      return_window_days?: number;
    }) => api<{ partner: Partner } & Rematched>(`/partners/${id}`, { method: "PATCH", json: body }),
    // Orders were matched again: statuses, claims and the overview all moved.
    onSuccess: () => queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "me" }),
  });
}

export function useAudit(area: string, page: string) {
  return useQuery({
    queryKey: ["audit", area, page],
    queryFn: () => {
      const search = new URLSearchParams();
      if (area) search.set("area", area);
      if (page) search.set("page", page);
      const text = search.toString();
      return api<{ rows: AuditEntry[]; total: number; page: number; page_size: number; everyone: boolean }>(
        `/audit${text ? `?${text}` : ""}`,
      );
    },
    placeholderData: keepPreviousData,
  });
}
