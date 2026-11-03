import { ArrowLeft, Lock, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { can, useMe } from "../auth/session";
import { MappingVersions } from "../components/MappingVersions";
import { Alert, Badge, Button, EmptyState, Notice, Spinner, cx, useTitle, type Tone } from "../components/ui";
import { ApiError } from "../lib/api";
import { formatCount, formatRupees } from "../lib/format";
import {
  DATE_FORMATS,
  REVIEW_BELOW,
  useApprove,
  useDebounced,
  usePreview,
  useReview,
  type FieldSpec,
  type MappingRequest,
  type Provenance,
  type Review,
  type TargetField,
} from "../lib/mapping";
import { KIND_LABELS } from "../lib/uploads";

export function Mapping() {
  const { uploadId } = useParams();
  const id = Number(uploadId);
  const review = useReview(id);
  useTitle("Map columns");

  if (review.isPending) {
    return (
      <div className="space-y-3" aria-label="Loading">
        <div className="h-10 w-72 animate-pulse rounded bg-surface" />
        <div className="h-96 animate-pulse rounded-xl bg-surface" />
      </div>
    );
  }
  if (review.isError) {
    const notWaiting = review.error instanceof ApiError && review.error.status === 409;
    return (
      <EmptyState
        icon={Lock}
        title={notWaiting ? "This file isn't waiting for a mapping" : "Couldn't open the mapping"}
        action={
          <Link to="/uploads" className="text-sm font-medium text-accent hover:text-accent-strong">
            Back to uploads
          </Link>
        }
      >
        {review.error.message}
      </EmptyState>
    );
  }
  return <MappingEditor review={review.data} uploadId={id} />;
}

function initialFields(review: Review): Record<string, FieldSpec> {
  const fields: Record<string, FieldSpec> = {};
  for (const [name, s] of Object.entries(review.suggestion.fields)) {
    if (s.column) fields[name] = { column: s.column, ...s.options };
  }
  return fields;
}

function MappingEditor({ review, uploadId }: { review: Review; uploadId: number }) {
  const { data: me } = useMe();
  const navigate = useNavigate();
  const approve = useApprove(uploadId);
  const suggestion = review.suggestion;

  const [fields, setFields] = useState(() => initialFields(review));
  const [provenance, setProvenance] = useState<Record<string, Provenance>>(() =>
    Object.fromEntries(Object.entries(suggestion.fields).map(([k, s]) => [k, s.source])),
  );
  const [personal, setPersonal] = useState(() => new Set(suggestion.personal_data_columns));

  const request: MappingRequest = useMemo(
    () => ({
      fields: Object.fromEntries(Object.entries(fields).filter(([, s]) => s.column)),
      personal_data_columns: [...personal],
      provenance,
    }),
    [fields, personal, provenance],
  );
  const debounced = useDebounced(request);
  const preview = usePreview(uploadId, debounced);
  const resolved = preview.data?.mapping.fields ?? {};
  const settling = debounced !== request || preview.isFetching;

  function setColumn(name: string, column: string | null) {
    setFields((current) => {
      const next = { ...current };
      if (column) next[name] = { column };
      else delete next[name];
      return next;
    });
    setProvenance((p) => ({ ...p, [name]: "manual" }));
  }

  function setOptions(name: string, patch: Partial<FieldSpec>) {
    setFields((current) => {
      const base = { ...(resolved[name] ?? current[name]), ...current[name], ...patch };
      const cleaned = Object.fromEntries(
        Object.entries(base).filter(([, v]) => v !== undefined && v !== false && v !== ""),
      ) as unknown as FieldSpec;
      return { ...current, [name]: cleaned };
    });
    setProvenance((p) => ({ ...p, [name]: "manual" }));
  }

  const usedBy = useMemo(() => {
    const map: Record<string, string> = {};
    for (const f of review.fields) if (fields[f.name]?.column) map[fields[f.name].column!] = f.label;
    return map;
  }, [fields, review.fields]);
  const notImported = review.columns.filter((c) => !usedBy[c.header] && !personal.has(c.header));
  const canApprove = can(me, "mapping.approve");

  function submit() {
    if (!preview.data) return;
    approve.mutate(
      { ...request, fields: preview.data.mapping.fields },
      {
        onSuccess: (result) =>
          navigate("/uploads", {
            state: { notice: `Saved as layout version ${result.version}. ${review.upload.filename} is loading now.` },
          }),
      },
    );
  }

  return (
    <div className="pb-28">
      <Link to="/uploads" className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink">
        <ArrowLeft className="size-4" aria-hidden /> Uploads
      </Link>
      <div className="mb-6">
        <h1 className="text-xl font-semibold tracking-tight">Map columns</h1>
        <p className="mt-1 text-sm text-muted">
          <span className="font-medium text-ink">{review.upload.filename}</span> for {review.source.partner} ·{" "}
          {KIND_LABELS[review.source.file_kind]}.{" "}
          {review.active_version
            ? `Its columns don't match the saved layout (version ${review.active_version}).`
            : "There's no saved layout for this report yet."}{" "}
          Check each field, then approve.
        </p>
      </div>

      <div className="mb-6">
        {suggestion.ai_status === "used" && (
          <Notice tone="accent">
            <Sparkles className="mr-1.5 inline size-4" aria-hidden />
            Suggested by rules and AI. The AI saw column names and value types only, never the values. Fields marked{" "}
            <strong>Check</strong> need a look.
          </Notice>
        )}
        {suggestion.ai_status === "unavailable" && <Notice tone="warn">{suggestion.ai_message}</Notice>}
        {suggestion.ai_status === "not_needed" && (
          <Notice tone="gain">These columns match saved layout version {suggestion.matched_version}.</Notice>
        )}
      </div>

      <section aria-labelledby="fields-title" className="mb-8">
        <h2 id="fields-title" className="mb-3 text-sm font-semibold">
          Fields
        </h2>
        <div className="divide-y divide-line rounded-xl border border-line bg-surface">
          {review.fields.map((f) => (
            <FieldRow
              key={f.name}
              field={f}
              review={review}
              spec={fields[f.name]}
              resolved={resolved[f.name]}
              source={provenance[f.name]}
              usedBy={usedBy}
              personal={personal}
              onColumn={(c) => setColumn(f.name, c)}
              onOptions={(patch) => setOptions(f.name, patch)}
              disabled={!canApprove}
            />
          ))}
        </div>
      </section>

      <section aria-labelledby="personal-title" className="mb-8">
        <h2 id="personal-title" className="mb-1 text-sm font-semibold">
          Personal data
        </h2>
        <p className="mb-3 text-sm text-muted">Ticked columns are never stored, not even in the original-row copy.</p>
        <div className="flex flex-wrap gap-2">
          {review.columns
            .filter((c) => !usedBy[c.header])
            .map((c) => (
              <label
                key={c.header}
                className={cx(
                  "inline-flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm",
                  personal.has(c.header) ? "border-warn/30 bg-warn-soft" : "border-line bg-surface",
                )}
                title={c.personal ? `Flagged because ${c.personal}` : undefined}
              >
                <input
                  type="checkbox"
                  className="accent-accent"
                  checked={personal.has(c.header)}
                  disabled={!canApprove}
                  onChange={(e) =>
                    setPersonal((s) => {
                      const next = new Set(s);
                      if (e.target.checked) next.add(c.header);
                      else next.delete(c.header);
                      return next;
                    })
                  }
                />
                {c.header}
              </label>
            ))}
        </div>
        {notImported.length > 0 && (
          <p className="mt-3 text-xs text-muted">
            Kept as original cells but not used: {notImported.map((c) => c.header).join(", ")}.
          </p>
        )}
      </section>

      <PreviewSection review={review} preview={preview} />

      <section aria-labelledby="versions-title" className="mb-8">
        <h2 id="versions-title" className="mb-3 text-sm font-semibold">
          Saved layouts for this report
        </h2>
        <MappingVersions sourceId={review.source.id} />
      </section>

      <div className="fixed inset-x-0 bottom-0 z-10 border-t border-line bg-surface/95 backdrop-blur lg:left-60">
        <div className="mx-auto flex max-w-7xl flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-6 lg:px-8">
          <div className="min-w-0 text-sm">
            {approve.error ? (
              <p className="text-loss">{approve.error.message}</p>
            ) : preview.data?.blocking.length ? (
              <p className="text-loss">
                {preview.data.blocking[0]}
                {preview.data.blocking.length > 1 && ` (and ${preview.data.blocking.length - 1} more)`}
              </p>
            ) : preview.data ? (
              <p className="text-muted">
                {formatCount(preview.data.total_rows)} rows will load
                {preview.data.rejected_rows > 0 && `; ${formatCount(preview.data.rejected_rows)} will be rejected`}.
              </p>
            ) : (
              <p className="text-muted">Checking the file…</p>
            )}
          </div>
          <Button
            onClick={submit}
            loading={approve.isPending}
            disabled={!canApprove || settling || !preview.data || preview.data.blocking.length > 0}
            className="shrink-0"
          >
            {settling && !approve.isPending && <Spinner className="size-4" />}
            Approve and load file
          </Button>
        </div>
      </div>
    </div>
  );
}

function confidenceBadge(spec: FieldSpec | undefined, field: TargetField, source: Provenance | undefined, confidence: number) {
  if (!spec?.column) {
    return field.required ? { tone: "loss" as Tone, label: "Needs a column" } : { tone: "neutral" as Tone, label: "Not in file" };
  }
  if (source === "manual") return { tone: "accent" as Tone, label: "Your choice" };
  if (source === "saved" || source === "kept") return { tone: "neutral" as Tone, label: "Saved before" };
  if (confidence < REVIEW_BELOW) return { tone: "warn" as Tone, label: "Check" };
  if (confidence < 0.9) return { tone: "neutral" as Tone, label: "Likely" };
  return { tone: "gain" as Tone, label: "Sure" };
}

const SOURCE_LABELS: Partial<Record<Provenance, string>> = {
  both: "rules and AI agree",
  ai: "from AI",
  rules: "from rules",
};

function FieldRow({
  field,
  review,
  spec,
  resolved,
  source,
  usedBy,
  personal,
  onColumn,
  onOptions,
  disabled,
}: {
  field: TargetField;
  review: Review;
  spec: FieldSpec | undefined;
  resolved: FieldSpec | undefined;
  source: Provenance | undefined;
  usedBy: Record<string, string>;
  personal: Set<string>;
  onColumn: (column: string | null) => void;
  onOptions: (patch: Partial<FieldSpec>) => void;
  disabled: boolean;
}) {
  const suggestion = review.suggestion.fields[field.name];
  const badge = confidenceBadge(spec, field, source, suggestion?.confidence ?? 0);
  const options = resolved ?? spec;
  const column = review.columns.find((c) => c.header === spec?.column);
  const showReason = suggestion && source !== "manual" && spec?.column === suggestion.column;
  const alternative = suggestion?.alternative && suggestion.alternative !== spec?.column ? suggestion.alternative : null;

  return (
    <div className="grid gap-3 px-4 py-3.5 md:grid-cols-[minmax(0,14rem)_minmax(0,18rem)_minmax(0,1fr)] md:items-start">
      <div>
        <p className="text-sm font-medium">
          {field.label}
          {field.required && <span className="ml-1 text-loss" aria-label="required">*</span>}
        </p>
        <p className="text-xs text-muted">{field.description}</p>
      </div>

      <div className="space-y-1.5">
        <select
          aria-label={`Column for ${field.label}`}
          value={spec?.column ?? ""}
          disabled={disabled}
          onChange={(e) => onColumn(e.target.value || null)}
          className={cx(
            "block h-9 w-full rounded-md border bg-surface px-2.5 text-sm focus:border-accent focus:ring-3 focus:ring-accent/15 focus:outline-none",
            !spec?.column && field.required ? "border-loss/50" : "border-line-strong",
          )}
        >
          <option value="">— Not in this file —</option>
          {review.columns.map((c) => {
            const takenBy = usedBy[c.header] && c.header !== spec?.column ? usedBy[c.header] : null;
            return (
              <option key={c.header} value={c.header} disabled={personal.has(c.header)}>
                {c.header}
                {takenBy ? ` (used for ${takenBy})` : ""}
                {personal.has(c.header) ? " (personal data)" : ""}
              </option>
            );
          })}
        </select>
        {column && <p className="text-xs text-faint">{column.detail}</p>}
      </div>

      <div className="space-y-2 text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={badge.tone}>{badge.label}</Badge>
          {showReason && (
            <span className="text-muted">
              {suggestion.reason}
              {SOURCE_LABELS[suggestion.source] && ` · ${SOURCE_LABELS[suggestion.source]}`}
            </span>
          )}
          {alternative && !disabled && (
            <button type="button" onClick={() => onColumn(alternative)} className="font-medium text-accent hover:text-accent-strong">
              Use “{alternative}” instead
            </button>
          )}
        </div>
        {spec?.column && <FieldOptions field={field} options={options} column={column} onChange={onOptions} disabled={disabled} />}
      </div>
    </div>
  );
}

function FieldOptions({
  field,
  options,
  column,
  onChange,
  disabled,
}: {
  field: TargetField;
  options: FieldSpec | undefined;
  column: Review["columns"][number] | undefined;
  onChange: (patch: Partial<FieldSpec>) => void;
  disabled: boolean;
}) {
  if (field.type === "money" && field.deduction) {
    return (
      <label className="inline-flex items-center gap-2 text-muted">
        <input
          type="checkbox"
          className="accent-accent"
          checked={Boolean(options?.negate)}
          disabled={disabled}
          onChange={(e) => onChange({ negate: e.target.checked })}
        />
        Partner shows this deduction as a positive number
      </label>
    );
  }
  if (field.type === "date" || field.type === "datetime") {
    return (
      <label className="inline-flex items-center gap-2 text-muted">
        Date format
        <select
          value={options?.date_format ?? ""}
          disabled={disabled}
          onChange={(e) => onChange({ date_format: e.target.value || undefined })}
          className="h-7 rounded border border-line-strong bg-surface px-1.5 text-xs"
        >
          <option value="">Detect</option>
          {Object.entries(DATE_FORMATS).map(([fmt, label]) => (
            <option key={fmt} value={fmt}>
              {label}
            </option>
          ))}
        </select>
      </label>
    );
  }
  if (field.type === "weight") {
    return (
      <label className="inline-flex items-center gap-2 text-muted">
        Unit
        <select
          value={options?.scale === 1000 ? "kg" : "g"}
          disabled={disabled}
          onChange={(e) => onChange({ scale: e.target.value === "kg" ? 1000 : undefined })}
          className="h-7 rounded border border-line-strong bg-surface px-1.5 text-xs"
        >
          <option value="g">grams</option>
          <option value="kg">kilograms</option>
        </select>
      </label>
    );
  }
  if (field.type === "enum" && column?.categories.length) {
    const values = options?.values ?? {};
    return (
      <div className="flex flex-wrap gap-x-3 gap-y-1.5">
        {column.categories.map((label) => (
          <label key={label} className="inline-flex items-center gap-1.5 text-muted">
            <span className="text-ink">{label}</span>→
            <select
              value={values[label] ?? ""}
              disabled={disabled}
              onChange={(e) => {
                const next = { ...values };
                if (e.target.value) next[label] = e.target.value;
                else delete next[label];
                onChange({ values: next });
              }}
              className={cx(
                "h-7 rounded border bg-surface px-1.5 text-xs",
                values[label] || field.choices.includes(label.toLowerCase()) ? "border-line-strong" : "border-loss/50",
              )}
            >
              <option value="">{field.choices.includes(label.toLowerCase()) ? label.toLowerCase() : "— unknown —"}</option>
              {field.choices.map((c) => (
                <option key={c} value={c}>
                  {c.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
    );
  }
  if (field.type === "id" && options?.strip_suffix) {
    return <p className="text-muted">Removes the suffix matching {options.strip_suffix} (kept from the last layout).</p>;
  }
  return null;
}

function formatValue(type: string, value: string | number | null): string {
  if (value === null || value === undefined) return "";
  if (type === "money" && typeof value === "number") return formatRupees(value);
  if (type === "weight" && typeof value === "number") return `${formatCount(value)} g`;
  if (type === "date" && typeof value === "string") {
    return new Date(`${value}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  }
  if (type === "datetime" && typeof value === "string") {
    return new Date(value).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  }
  return String(value).replaceAll("_", " ");
}

function PreviewSection({ review, preview }: { review: Review; preview: ReturnType<typeof usePreview> }) {
  const mapped = review.fields.filter((f) => preview.data?.mapping.fields[f.name]);
  return (
    <section aria-labelledby="preview-title" className="mb-8">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 id="preview-title" className="text-sm font-semibold">
          Preview: the first {Math.min(20, preview.data?.total_rows ?? 20)} rows as they will load
        </h2>
        {preview.isFetching && <Spinner className="size-4 text-muted" />}
      </div>
      {preview.isError && <Alert>{preview.error.message}</Alert>}
      {preview.data && (
        <>
          <div className="overflow-x-auto rounded-xl border border-line bg-surface">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-line text-left text-muted">
                  <th className="sticky left-0 bg-surface px-3 py-2 font-medium">Row</th>
                  {mapped.map((f) => (
                    <th key={f.name} className={cx("px-3 py-2 font-medium whitespace-nowrap", f.type === "money" && "text-right")}>
                      {f.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {preview.data.rows.map((r) => (
                  <tr key={r.row}>
                    <td className="num sticky left-0 bg-surface px-3 py-1.5 text-faint">{r.row}</td>
                    {mapped.map((f) => {
                      const problem = r.problems[f.name];
                      return (
                        <td
                          key={f.name}
                          title={problem}
                          className={cx(
                            "px-3 py-1.5 whitespace-nowrap",
                            f.type === "money" && "num text-right",
                            problem && "bg-loss-soft text-loss",
                          )}
                        >
                          {problem ? "Can't read" : formatValue(f.type, r.values[f.name])}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {preview.data.examples.length > 0 && (
            <div className="mt-3 text-xs text-loss">
              <p className="font-medium">
                {formatCount(preview.data.rejected_rows)} of {formatCount(preview.data.total_rows)} rows can't be read, for
                example:
              </p>
              <ul className="mt-1 list-inside list-disc">
                {preview.data.examples.slice(0, 3).map((e) => (
                  <li key={e.row}>
                    Row {e.row}: {e.problem}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  );
}
