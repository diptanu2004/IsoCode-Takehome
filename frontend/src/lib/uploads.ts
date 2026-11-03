import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export type FileKind = "orders" | "settlement" | "cod_remittance" | "freight_invoice" | "return_scans";
export type UploadStatus = "queued" | "needs_mapping" | "loading" | "matching" | "done" | "failed" | "voided";

export interface Source {
  id: number;
  name: string;
  partner: string;
  file_kind: FileKind;
  mapping_version: number | null;
  last_upload_at: string | null;
}

export interface Upload {
  id: number;
  source_id: number;
  source: string;
  partner: string;
  file_kind: FileKind;
  filename: string;
  size_bytes: number;
  status: UploadStatus;
  row_count: number | null;
  error_count: number | null;
  error_message: string | null;
  stats: Record<string, number>;
  stage_timings: Record<string, number>;
  mapping_version: number | null;
  uploaded_by: string | null;
  created_at: string;
  finished_at: string | null;
}

export const KIND_LABELS: Record<FileKind, string> = {
  orders: "Orders",
  settlement: "Settlement",
  cod_remittance: "COD remittance",
  freight_invoice: "Freight invoice",
  return_scans: "Return scans",
};

const IN_PROGRESS: UploadStatus[] = ["queued", "loading", "matching"];

export function isInProgress(upload: Upload): boolean {
  return IN_PROGRESS.includes(upload.status);
}

export function useSources() {
  return useQuery({ queryKey: ["sources"], queryFn: () => api<Source[]>("/sources") });
}

export function useUploads() {
  return useQuery({
    queryKey: ["uploads"],
    queryFn: () => api<Upload[]>("/uploads"),
    // Poll while anything is still loading, so progress shows without a refresh.
    refetchInterval: (query) => (query.state.data?.some(isInProgress) ? 1500 : false),
  });
}

export function useCreateUpload() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sourceId, file }: { sourceId: number; file: File }) => {
      const body = new FormData();
      body.append("source_id", String(sourceId));
      body.append("file", file);
      return api<Upload>("/uploads", { method: "POST", body });
    },
    onSuccess: (created) => {
      queryClient.setQueryData<Upload[]>(["uploads"], (old) => [created, ...(old ?? [])]);
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

/** Seconds the load took, from the stage timings. */
export function totalSeconds(upload: Upload): number | null {
  const values = Object.values(upload.stage_timings ?? {});
  return values.length ? values.reduce((a, b) => a + b, 0) : null;
}
