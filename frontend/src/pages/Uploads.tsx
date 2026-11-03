import { AlertTriangle, CheckCircle2, Download, FileUp, Inbox, LoaderCircle, XCircle } from "lucide-react";
import { useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";
import { Link, useLocation } from "react-router";
import { can, useMe } from "../auth/session";
import { Alert, Button, EmptyState, Notice, PageHeader, Spinner, cx, useTitle } from "../components/ui";
import { formatBytes, formatCount, formatDateTime, formatSeconds } from "../lib/format";
import {
  KIND_LABELS,
  isInProgress,
  totalSeconds,
  useCreateUpload,
  useSources,
  useUploads,
  type Source,
  type Upload,
} from "../lib/uploads";

const ACCEPT = ".csv,.xlsx";

export function Uploads() {
  useTitle("Uploads");
  const { data: me } = useMe();
  const notice = (useLocation().state as { notice?: string } | null)?.notice;
  return (
    <>
      <PageHeader
        title="Uploads"
        description="Partner reports, loaded into Settle. Each source remembers its column layout."
      />
      {notice && (
        <div className="mb-4">
          <Notice tone="gain">{notice}</Notice>
        </div>
      )}
      {can(me, "upload.create") && <UploadPanel />}
      <UploadHistory />
    </>
  );
}

function groupByPartner(sources: Source[]): [string, Source[]][] {
  const groups = new Map<string, Source[]>();
  for (const source of sources) groups.set(source.partner, [...(groups.get(source.partner) ?? []), source]);
  return [...groups.entries()];
}

function UploadPanel() {
  const sources = useSources();
  const create = useCreateUpload();
  const input = useRef<HTMLInputElement>(null);
  const [sourceId, setSourceId] = useState<number | "">("");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const groups = useMemo(() => groupByPartner(sources.data ?? []), [sources.data]);

  function choose(chosen: File | undefined) {
    setProblem(null);
    create.reset();
    if (!chosen) return;
    if (!/\.(csv|xlsx)$/i.test(chosen.name)) {
      setProblem("Choose a .csv or .xlsx file.");
      return;
    }
    setFile(chosen);
  }

  function drop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    choose(event.dataTransfer.files[0]);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!sourceId) return setProblem("Choose which partner report this is.");
    if (!file) return setProblem("Choose a file to upload.");
    create.mutate(
      { sourceId, file },
      {
        onSuccess: () => {
          setFile(null);
          if (input.current) input.current.value = "";
        },
      },
    );
  }

  const error = problem ?? create.error?.message;
  return (
    <form onSubmit={submit} className="mb-8 rounded-xl border border-line bg-surface p-4 sm:p-5">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,18rem)_minmax(0,1fr)_auto] lg:items-end">
        <div className="space-y-1.5">
          <label htmlFor="source" className="block text-sm font-medium">
            Report
          </label>
          <select
            id="source"
            value={sourceId}
            onChange={(e) => setSourceId(e.target.value ? Number(e.target.value) : "")}
            disabled={sources.isPending}
            className="block h-10 w-full rounded-md border border-line-strong bg-surface px-3 text-sm focus:border-accent focus:ring-3 focus:ring-accent/15 focus:outline-none"
          >
            <option value="">{sources.isPending ? "Loading…" : "Choose a partner report"}</option>
            {groups.map(([partner, items]) => (
              <optgroup key={partner} label={partner}>
                {items.map((s) => (
                  <option key={s.id} value={s.id}>
                    {partner} · {KIND_LABELS[s.file_kind]}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>

        <label
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={drop}
          className={cx(
            "flex h-10 cursor-pointer items-center gap-2.5 rounded-md border border-dashed px-3 text-sm transition-colors",
            dragging ? "border-accent bg-accent-soft" : "border-line-strong hover:bg-canvas",
          )}
        >
          <FileUp className="size-4 shrink-0 text-muted" aria-hidden />
          {file ? (
            <span className="min-w-0 truncate">
              <span className="font-medium text-ink">{file.name}</span>
              <span className="text-muted"> · {formatBytes(file.size)}</span>
            </span>
          ) : (
            <span className="truncate text-muted">
              Drop a .csv or .xlsx here, or <span className="font-medium text-accent">browse</span>
            </span>
          )}
          <input
            ref={input}
            type="file"
            accept={ACCEPT}
            className="sr-only"
            onChange={(e) => choose(e.target.files?.[0])}
          />
        </label>

        <Button type="submit" loading={create.isPending} className="lg:w-28">
          Upload
        </Button>
      </div>
      {error && (
        <div className="mt-3">
          <Alert>{error}</Alert>
        </div>
      )}
    </form>
  );
}

const STATUS: Record<Upload["status"], { label: string; className: string; icon: typeof CheckCircle2 }> = {
  queued: { label: "Waiting", className: "bg-accent-soft text-accent", icon: LoaderCircle },
  loading: { label: "Loading", className: "bg-accent-soft text-accent", icon: LoaderCircle },
  matching: { label: "Matching", className: "bg-accent-soft text-accent", icon: LoaderCircle },
  done: { label: "Loaded", className: "bg-gain-soft text-gain", icon: CheckCircle2 },
  needs_mapping: { label: "New format", className: "bg-warn-soft text-warn", icon: AlertTriangle },
  failed: { label: "Failed", className: "bg-loss-soft text-loss", icon: XCircle },
  voided: { label: "Voided", className: "bg-canvas text-muted", icon: XCircle },
};

function StatusBadge({ upload }: { upload: Upload }) {
  const { label, className, icon: Icon } = STATUS[upload.status];
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium", className)}>
      {isInProgress(upload) ? <Spinner className="size-3" /> : <Icon className="size-3.5" aria-hidden />}
      {label}
    </span>
  );
}

function outcome(upload: Upload): string | null {
  const s = upload.stats ?? {};
  if (upload.status !== "done") return null;
  const parts: string[] = [];
  if (s.orders_created !== undefined) {
    parts.push(`${formatCount(s.orders_created)} new orders`);
    if (s.orders_updated) parts.push(`${formatCount(s.orders_updated)} updated`);
  }
  if (s.lines_created !== undefined) {
    parts.push(`${formatCount(s.lines_created)} lines`);
    const unmatched = s.lines_created - (s.lines_matched ?? 0);
    if (unmatched) parts.push(`${formatCount(unmatched)} without an order`);
    if (s.lines_already_loaded) parts.push(`${formatCount(s.lines_already_loaded)} already loaded`);
  }
  if (s.scans_created !== undefined) parts.push(`${formatCount(s.scans_created)} scans`);
  return parts.join(" · ");
}

function Detail({ upload }: { upload: Upload }) {
  const { data: me } = useMe();
  if (upload.status === "needs_mapping") {
    return (
      <p className="text-warn">
        The columns don't match the saved layout.{" "}
        {can(me, "mapping.approve") ? (
          <Link to={`/uploads/${upload.id}/mapping`} className="font-medium text-accent hover:text-accent-strong">
            Map columns
          </Link>
        ) : (
          "Waiting for someone who can approve mappings."
        )}
      </p>
    );
  }
  if (upload.status === "failed") return <p className="text-loss">{upload.error_message}</p>;
  const text = outcome(upload);
  return text ? (
    <p className="text-muted">
      {text}
      {upload.mapping_version && (
        <>
          {" · "}
          <Link to={`/sources/${upload.source_id}/layouts`} className="hover:text-ink hover:underline">
            layout v{upload.mapping_version}
          </Link>
        </>
      )}
    </p>
  ) : null;
}

function RejectedLink({ upload }: { upload: Upload }) {
  if (!upload.error_count) return null;
  return (
    <a
      href={`/api/uploads/${upload.id}/errors.csv`}
      className="inline-flex items-center gap-1 font-medium text-accent hover:text-accent-strong"
    >
      <Download className="size-3.5" aria-hidden />
      {formatCount(upload.error_count)} rejected {upload.error_count === 1 ? "row" : "rows"}
    </a>
  );
}

function UploadHistory() {
  const uploads = useUploads();

  if (uploads.isPending) {
    return (
      <div className="space-y-2" aria-label="Loading uploads">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="h-16 animate-pulse rounded-lg bg-surface" />
        ))}
      </div>
    );
  }
  if (uploads.isError) {
    return (
      <Alert>
        Couldn't load the upload history. {uploads.error.message}{" "}
        <button type="button" className="font-medium underline" onClick={() => uploads.refetch()}>
          Try again
        </button>
      </Alert>
    );
  }
  if (!uploads.data.length) {
    return (
      <EmptyState icon={Inbox} title="No uploads yet">
        Choose a partner report above and upload its CSV or Excel file.
      </EmptyState>
    );
  }

  return (
    <section aria-labelledby="history-title">
      <h2 id="history-title" className="mb-3 text-sm font-semibold text-ink">
        History
      </h2>

      {/* Phone: one card per upload */}
      <ul className="space-y-2 md:hidden">
        {uploads.data.map((u) => (
          <li key={u.id} className="rounded-lg border border-line bg-surface p-3.5 text-sm">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate font-medium">{u.filename}</p>
                <p className="text-xs text-muted">{u.source}</p>
              </div>
              <StatusBadge upload={u} />
            </div>
            <div className="mt-2 space-y-1 text-xs">
              <Detail upload={u} />
              <RejectedLink upload={u} />
              <p className="text-faint">
                {formatDateTime(u.created_at)}
                {u.uploaded_by && ` · ${u.uploaded_by}`}
              </p>
            </div>
          </li>
        ))}
      </ul>

      {/* Desktop: a table */}
      <div className="hidden overflow-hidden rounded-xl border border-line bg-surface md:block">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs font-medium text-muted">
              <th className="px-4 py-2.5 font-medium">File</th>
              <th className="px-4 py-2.5 font-medium">Status</th>
              <th className="px-4 py-2.5 font-medium">Result</th>
              <th className="px-4 py-2.5 text-right font-medium">Rows</th>
              <th className="px-4 py-2.5 text-right font-medium">Time</th>
              <th className="px-4 py-2.5 font-medium">Uploaded</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {uploads.data.map((u) => {
              const seconds = totalSeconds(u);
              return (
                <tr key={u.id} className="align-top">
                  <td className="max-w-64 px-4 py-3">
                    <p className="truncate font-medium" title={u.filename}>
                      {u.filename}
                    </p>
                    <p className="truncate text-xs text-muted">{u.source}</p>
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge upload={u} />
                  </td>
                  <td className="max-w-md px-4 py-3 text-xs">
                    <Detail upload={u} />
                    <div className="mt-0.5">
                      <RejectedLink upload={u} />
                    </div>
                  </td>
                  <td className="num px-4 py-3 text-right">{u.row_count == null ? "—" : formatCount(u.row_count)}</td>
                  <td className="num px-4 py-3 text-right text-muted">
                    {seconds == null || !["done", "failed"].includes(u.status) ? "—" : formatSeconds(seconds)}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-muted">
                    {formatDateTime(u.created_at)}
                    {u.uploaded_by && <p className="text-xs text-faint">{u.uploaded_by}</p>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
