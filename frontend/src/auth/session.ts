import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../lib/api";

export type Role = "exec" | "cofounder";

export type Permission =
  | "upload.create"
  | "upload.void_any"
  | "mapping.approve"
  | "recon.view"
  | "suggestion.decide"
  | "claim.work"
  | "claim.export"
  | "claim.write_off"
  | "rate_card.view"
  | "rate_card.edit"
  | "settings.edit"
  | "dashboard.view"
  | "user.manage"
  | "audit.view_all";

export interface Me {
  id: number;
  email: string;
  name: string;
  role: Role;
  permissions: Permission[];
}

export const ROLE_LABELS: Record<Role, string> = {
  exec: "Finance executive",
  cofounder: "Co-founder",
};

export const ME_KEY = ["me"] as const;

/** The signed-in user, or null when signed out. */
export function useMe() {
  return useQuery({
    queryKey: ME_KEY,
    queryFn: async () => {
      try {
        return await api<Me>("/me");
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

export function can(me: Me | null | undefined, permission: Permission): boolean {
  return Boolean(me?.permissions.includes(permission));
}

export function useSignIn() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (credentials: { email: string; password: string }) =>
      api<Me>("/auth/login", { json: credentials }),
    onSuccess: (me) => queryClient.setQueryData(ME_KEY, me),
  });
}

export function useSignOut() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api<void>("/auth/logout", { method: "POST" }),
    onSettled: () => {
      queryClient.clear();
      queryClient.setQueryData(ME_KEY, null);
    },
  });
}
