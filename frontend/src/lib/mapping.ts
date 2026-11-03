import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "./api";
import type { FileKind } from "./uploads";

export type FieldType = "id" | "text" | "date" | "datetime" | "money" | "integer" | "weight" | "enum";
export type Provenance = "saved" | "kept" | "rules" | "ai" | "both" | "manual" | "none";

export interface TargetField {
  name: string;
  label: string;
  description: string;
  type: FieldType;
  required: boolean;
  key_group: string | null;
  deduction: boolean;
  choices: string[];
}

export interface ColumnInfo {
  header: string;
  kind: string;
  detail: string;
  personal: string | null;
  categories: string[];
}

export interface FieldSpec {
  column: string | null;
  negate?: boolean;
  date_format?: string;
  scale?: number;
  values?: Record<string, string>;
  strip_suffix?: string;
}

export interface FieldSuggestion {
  field: string;
  column: string | null;
  confidence: number;
  reason: string;
  source: Provenance;
  alternative: string | null;
  options: Omit<FieldSpec, "column">;
}

export interface Review {
  upload: { id: number; filename: string; header_row: number; rows: number };
  source: { id: number; name: string; partner: string; file_kind: FileKind };
  active_version: number | null;
  columns: ColumnInfo[];
  fields: TargetField[];
  suggestion: {
    fields: Record<string, FieldSuggestion>;
    personal_data_columns: string[];
    ai_status: "used" | "unavailable" | "not_needed";
    ai_message: string | null;
    matched_version: number | null;
  };
}

export interface MappingRequest {
  fields: Record<string, FieldSpec>;
  personal_data_columns: string[];
  provenance: Record<string, Provenance>;
}

export interface Preview {
  mapping: { header_row: number; fields: Record<string, FieldSpec> };
  rows: { row: number; values: Record<string, string | number | null>; problems: Record<string, string> }[];
  total_rows: number;
  rejected_rows: number;
  examples: { row: number; problem: string }[];
  blocking: string[];
}

export interface MappingVersion {
  id: number;
  version: number;
  is_active: boolean;
  suggested_by: string;
  approved_by: string | null;
  approved_at: string | null;
  uploads: number;
  columns: Record<string, string>;
  personal_data_columns: string[];
}

/** Matches backend app/mapping/profile.py. */
export const DATE_FORMATS: Record<string, string> = {
  "%Y-%m-%d": "yyyy-mm-dd",
  "%d/%m/%Y": "dd/mm/yyyy",
  "%d-%m-%Y": "dd-mm-yyyy",
  "%d-%b-%Y": "dd-Mon-yyyy",
  "%d %b %Y": "dd Mon yyyy",
  "%d.%m.%Y": "dd.mm.yyyy",
  "%m/%d/%Y": "mm/dd/yyyy",
  "%Y-%m-%d %H:%M:%S": "yyyy-mm-dd hh:mm:ss",
  "%Y-%m-%d %H:%M": "yyyy-mm-dd hh:mm",
  "%d/%m/%Y %H:%M": "dd/mm/yyyy hh:mm",
  "%d/%m/%Y %H:%M:%S": "dd/mm/yyyy hh:mm:ss",
};

export const REVIEW_BELOW = 0.7;

export function useReview(uploadId: number) {
  return useQuery({
    queryKey: ["mapping", uploadId],
    queryFn: () => api<Review>(`/uploads/${uploadId}/mapping`),
    retry: false,
  });
}

export function usePreview(uploadId: number, request: MappingRequest | null) {
  return useQuery({
    queryKey: ["mapping-preview", uploadId, request],
    queryFn: () => api<Preview>(`/uploads/${uploadId}/mapping/preview`, { json: request }),
    enabled: request !== null,
    placeholderData: (previous) => previous,
    retry: false,
  });
}

export function useApprove(uploadId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: MappingRequest) =>
      api<{ version: number; requeued_uploads: number[] }>(`/uploads/${uploadId}/mapping`, { json: request }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["uploads"] });
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["mapping-versions"] });
    },
  });
}

export function useVersions(sourceId: number | undefined) {
  return useQuery({
    queryKey: ["mapping-versions", sourceId],
    queryFn: () => api<MappingVersion[]>(`/sources/${sourceId}/mappings`),
    enabled: sourceId !== undefined,
  });
}

export function useActivate(sourceId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (versionId: number) =>
      api<{ active_version: number }>(`/sources/${sourceId}/mappings/${versionId}/activate`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mapping-versions", sourceId] });
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["uploads"] });
    },
  });
}

export function useDebounced<T>(value: T, delay = 350): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
