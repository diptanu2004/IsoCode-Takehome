import { ChevronDown, ChevronRight, FileSearch, Inbox, Search } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { Pager } from "../components/Pager";
import { PanelSection, SidePanel } from "../components/SidePanel";
import { ClaimBadge, ReconBadge } from "../components/StatusBadges";
import { Alert, Button, EmptyState, PageHeader, Spinner, cx, useTitle } from "../components/ui";
import { formatCompact, formatCount, formatDate, formatDateTime, formatRupees, formatSigned } from "../lib/format";
import { useDebouncedSearch, useFilterParams } from "../lib/params";
import {
  CLAIM_TYPE_LABELS,
  COMPONENT_LABELS,
  PROBLEM_STATUSES,
  STATUS_LABELS,
  useOrderDetail,
  useOrders,
  usePartners,
  useSourceRow,
  useStatusCounts,
  type OrderDetail,
  type OrderFilters,
  type OrderRow,
} from "../lib/recon";

const FILTER_KEYS = ["status", "partner_id", "date_from", "date_to", "q", "sort", "page"] as const;
const PAGE_SIZE = 50;
/** Over ₹10 is a claim; anything smaller is tolerance (TDD §8.4). */
const CLAIM_THRESHOLD = 1000;

const SORTS = [
  { value: "", label: "Problems first" },
  { value: "difference", label: "Largest shortfall" },
  { value: "date", label: "Newest orders" },
  { value: "value", label: "Highest value" },
];

export function Reconciliation() {
  useTitle("Reconciliation");
  const { values: filters, params, update } = useFilterParams(FILTER_KEYS);
  const selected = Number(params.get("order")) || null;
  const hasFilters = Boolean(filters.partner_id || filters.date_from || filters.date_to || filters.q);

  return (
    <>
      <PageHeader
        title="Reconciliation"
        description="Every order: what the rate cards say it should have earned, what the partners actually paid, and the difference."
      />
      <StatusChips filters={filters} onPick={(status) => update({ status })} />
      <FilterBar filters={filters} hasFilters={hasFilters} update={update} />
      <OrderList
        filters={filters}
        hasFilters={hasFilters}
        onOpen={(id) => update({ order: String(id) }, true)}
        onPage={(page) => update({ page: String(page) })}
        onClear={() => update({ partner_id: null, date_from: null, date_to: null, q: null, status: null })}
      />
      {selected && <OrderPanel key={selected} id={selected} onClose={() => update({ order: null })} />}
    </>
  );
}

function StatusChips({ filters, onPick }: { filters: OrderFilters; onPick: (status: string | null) => void }) {
  const counts = useStatusCounts(filters);
  const rows = counts.data ?? [];
  const all = rows.reduce((sum, r) => sum + r.orders, 0);
  const problems = rows.filter((r) => PROBLEM_STATUSES.includes(r.status));
  const chips = [
    { key: "", label: "All orders", count: all, money: null },
    {
      key: "problems",
      label: "Problems",
      count: problems.reduce((sum, r) => sum + r.orders, 0),
      money: problems.reduce((sum, r) => sum + Math.min(r.diff_paise, 0), 0),
    },
    ...rows.map((r) => ({ key: r.status as string, label: STATUS_LABELS[r.status], count: r.orders, money: null })),
  ];
  const active = filters.status ?? "";

  if (counts.isPending) return <div className="mb-4 h-9 w-full max-w-2xl animate-pulse rounded-md bg-surface" />;
  return (
    <div className="-mx-4 mb-4 overflow-x-auto px-4 sm:mx-0 sm:overflow-visible sm:px-0">
      <div className="flex gap-2 pb-1 sm:flex-wrap" role="group" aria-label="Status">
        {chips.map((chip) => {
          const on = chip.key === active;
          return (
            <button
              key={chip.key || "all"}
              type="button"
              aria-pressed={on}
              title={
                chip.money
                  ? "Money short in payments. Claims also include the value of returns that never came back."
                  : undefined
              }
              onClick={() => onPick(chip.key || null)}
              className={cx(
                "inline-flex h-9 shrink-0 items-center gap-2 rounded-md border px-3 text-sm font-medium whitespace-nowrap transition-colors",
                on ? "border-accent bg-accent-soft text-accent" : "border-line bg-surface text-muted hover:text-ink",
              )}
            >
              {chip.label}
              <span className={cx("num text-xs", on ? "text-accent" : "text-faint")}>{formatCount(chip.count)}</span>
              {chip.money ? <span className="num text-xs text-loss">{formatCompact(chip.money)}</span> : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}

const control =
  "block h-9 w-full rounded-md border border-line-strong bg-surface px-3 text-sm focus:border-accent focus:ring-3 focus:ring-accent/15 focus:outline-none";

function FilterBar({
  filters,
  hasFilters,
  update,
}: {
  filters: OrderFilters;
  hasFilters: boolean;
  update: (changes: Partial<Record<string, string | null>>) => void;
}) {
  const partners = usePartners();
  const [text, setText] = useDebouncedSearch(filters.q ?? "", (q) => update({ q }));
  const external = (partners.data ?? []).filter((p) => p.kind !== "internal");

  return (
    <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-[minmax(0,16rem)_12rem_9.5rem_9.5rem_11rem_auto]">
      <div className="relative col-span-2 sm:col-span-3 lg:col-span-1">
        <Search className="pointer-events-none absolute top-2.5 left-3 size-4 text-faint" aria-hidden />
        <input
          type="search"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Order ID or AWB"
          aria-label="Search by order ID or AWB"
          maxLength={40}
          className={cx(control, "pl-9")}
        />
      </div>
      <select
        aria-label="Partner"
        value={filters.partner_id}
        onChange={(e) => update({ partner_id: e.target.value })}
        className={cx(control, "col-span-2 sm:col-span-1")}
      >
        <option value="">All partners</option>
        {external.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
      <input
        type="date"
        aria-label="Ordered from"
        title="Ordered from"
        value={filters.date_from}
        max={filters.date_to || undefined}
        onChange={(e) => update({ date_from: e.target.value })}
        className={control}
      />
      <input
        type="date"
        aria-label="Ordered to"
        title="Ordered to"
        value={filters.date_to}
        min={filters.date_from || undefined}
        onChange={(e) => update({ date_to: e.target.value })}
        className={control}
      />
      <select
        aria-label="Sort"
        value={filters.sort}
        onChange={(e) => update({ sort: e.target.value })}
        className={cx(control, "col-span-2 sm:col-span-1")}
      >
        {SORTS.map((s) => (
          <option key={s.value} value={s.value}>
            {s.label}
          </option>
        ))}
      </select>
      {hasFilters && (
        <Button
          variant="ghost"
          className="col-span-2 h-9 sm:col-span-1"
          onClick={() => {
            setText("");
            update({ partner_id: null, date_from: null, date_to: null, q: null });
          }}
        >
          Clear filters
        </Button>
      )}
    </div>
  );
}

function diffClass(paise: number): string {
  if (paise <= -CLAIM_THRESHOLD) return "text-loss";
  if (paise >= CLAIM_THRESHOLD) return "text-warn";
  return "text-muted";
}

function OrderList({
  filters,
  hasFilters,
  onOpen,
  onPage,
  onClear,
}: {
  filters: OrderFilters;
  hasFilters: boolean;
  onOpen: (id: number) => void;
  onPage: (page: number) => void;
  onClear: () => void;
}) {
  const orders = useOrders({ ...filters, page: filters.page || undefined });

  if (orders.isPending) {
    return (
      <div className="space-y-2" aria-label="Loading orders">
        {Array.from({ length: 8 }, (_, i) => (
          <div key={i} className="h-12 animate-pulse rounded-lg bg-surface" />
        ))}
      </div>
    );
  }
  if (orders.isError) {
    return (
      <Alert>
        Couldn't load the orders. {orders.error.message}{" "}
        <button type="button" className="font-medium underline" onClick={() => orders.refetch()}>
          Try again
        </button>
      </Alert>
    );
  }
  const { rows, total, page } = orders.data;
  if (!rows.length) {
    if (!hasFilters && !filters.status) {
      return (
        <EmptyState
          icon={Inbox}
          title="Nothing to reconcile yet"
          action={
            <Link to="/uploads" className="text-sm font-medium text-accent hover:text-accent-strong">
              Go to Uploads
            </Link>
          }
        >
          Upload the orders export and the partners' settlement reports. Matching runs as each file loads.
        </EmptyState>
      );
    }
    return (
      <EmptyState
        icon={FileSearch}
        title="No orders match"
        action={
          <Button variant="secondary" onClick={onClear}>
            Show all orders
          </Button>
        }
      >
        Try another status, partner or date range.
      </EmptyState>
    );
  }

  return (
    <section aria-label="Orders" className={cx(orders.isPlaceholderData && "opacity-60 transition-opacity")}>
      {/* Phone: one card per order */}
      <ul className="space-y-2 md:hidden">
        {rows.map((o) => (
          <li key={o.id}>
            <button
              type="button"
              onClick={() => onOpen(o.id)}
              className="w-full rounded-lg border border-line bg-surface p-3.5 text-left text-sm active:bg-canvas"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate font-medium">{o.order_id}</p>
                  <p className="text-xs text-muted">
                    {o.channel} · {formatDate(o.order_date)}
                  </p>
                </div>
                <ReconBadge status={o.status} />
              </div>
              <div className="num mt-2 flex justify-between text-xs">
                <span className="text-muted">Expected {formatRupees(o.expected_paise)}</span>
                <span className={cx("font-medium", diffClass(o.diff_paise))}>{formatSigned(o.diff_paise)}</span>
              </div>
            </button>
          </li>
        ))}
      </ul>

      {/* Desktop: a table */}
      <div className="hidden overflow-x-auto rounded-xl border border-line bg-surface md:block">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs font-medium text-muted">
              <th className="px-4 py-2.5 font-medium">Order</th>
              <th className="px-4 py-2.5 font-medium">Date</th>
              <th className="px-4 py-2.5 text-right font-medium">Order value</th>
              <th className="px-4 py-2.5 text-right font-medium">Expected</th>
              <th className="px-4 py-2.5 text-right font-medium">Received</th>
              <th className="px-4 py-2.5 text-right font-medium">Difference</th>
              <th className="px-4 py-2.5 font-medium">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((o) => (
              <OrderTableRow key={o.id} order={o} onOpen={onOpen} />
            ))}
          </tbody>
        </table>
      </div>
      <Pager page={page} pageSize={PAGE_SIZE} total={total} onPage={onPage} />
    </section>
  );
}

function OrderTableRow({ order: o, onOpen }: { order: OrderRow; onOpen: (id: number) => void }) {
  return (
    <tr className="cursor-pointer hover:bg-canvas/60" onClick={() => onOpen(o.id)}>
      <td className="px-4 py-2.5">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onOpen(o.id);
          }}
          className="font-medium text-ink hover:text-accent"
        >
          {o.order_id}
        </button>
        <p className="text-xs text-muted">
          {o.channel}
          {o.courier && ` · ${o.courier}`}
        </p>
      </td>
      <td className="px-4 py-2.5 whitespace-nowrap text-muted">{formatDate(o.order_date)}</td>
      <td className="num px-4 py-2.5 text-right text-muted">{formatRupees(o.value_paise)}</td>
      <td className="num px-4 py-2.5 text-right">{formatRupees(o.expected_paise)}</td>
      <td className="num px-4 py-2.5 text-right">{formatRupees(o.received_paise)}</td>
      <td className={cx("num px-4 py-2.5 text-right font-medium", diffClass(o.diff_paise))}>
        {formatSigned(o.diff_paise)}
      </td>
      <td className="px-4 py-2.5">
        <div className="flex flex-wrap gap-1">
          {o.issues.length > 1 ? (
            o.issues.map((issue) => <ReconBadge key={issue} status={issue as OrderRow["status"]} />)
          ) : (
            <ReconBadge status={o.status} />
          )}
        </div>
      </td>
    </tr>
  );
}

const COURIER_COMPONENTS = ["freight_forward", "freight_return", "cod_fee"];

/** The gap on the courier's components, or on the channel's. */
function gapFrom(o: OrderDetail, courier: boolean): string {
  const gap = o.components
    .filter((c) => COURIER_COMPONENTS.includes(c.component) === courier)
    .reduce((sum, c) => sum + c.gap_paise, 0);
  return formatRupees(Math.abs(gap));
}

function explain(o: OrderDetail, status: OrderDetail["status"]): string {
  const short = formatRupees(Math.abs(o.diff_paise));
  switch (status) {
    case "return_not_received":
      return `Marked as returned, but the warehouse hasn't scanned it back in. The goods, worth ${formatRupees(o.value_paise)}, are claimed from the partner.`;
    case "unpaid_past_due":
      return o.due_date
        ? `Payment was due by ${formatDate(o.due_date)} and the partner's reports since then don't include it.`
        : "The partner's reports don't include a payment for this order.";
    case "short_paid":
      return `${o.channel} paid ${gapFrom(o, false)} less than its rate card says.`;
    case "freight_overcharged":
      return `${o.courier ?? "The courier"} billed ${gapFrom(o, true)} more freight than its rate card says.`;
    case "overpaid":
      return `The partner paid ${short} more than expected. Worth checking before it's taken back from a later payout.`;
    case "pending":
      return o.due_date
        ? `Not due yet, or the partner's report covering ${formatDate(o.due_date)} hasn't been uploaded.`
        : "Waiting for the partner's report.";
    case "within_tolerance":
      return `Off by ${short} in total, which is within the ₹10 tolerance. Small gaps are still watched for a pattern.`;
    case "settled":
      return "Paid as the rate cards say.";
    case "not_applicable":
      return "Cancelled before dispatch, so nothing is owed either way.";
  }
}

const DELIVERY_LABELS: Record<string, string> = {
  pending: "Not shipped",
  in_transit: "In transit",
  delivered: "Delivered",
  return_in_transit: "Return in transit",
  returned_to_warehouse: "Returned",
  cancelled: "Cancelled",
};

function OrderPanel({ id, onClose }: { id: number; onClose: () => void }) {
  const detail = useOrderDetail(id);
  const o = detail.data;
  return (
    <SidePanel
      title={o?.order_id ?? "Order"}
      subtitle={o && `${o.channel}${o.courier ? `, shipped by ${o.courier}` : ""} · ordered ${formatDate(o.order_date)}`}
      onClose={onClose}
    >
      {detail.isPending ? (
        <div className="grid place-items-center py-16 text-muted">
          <Spinner />
        </div>
      ) : detail.isError ? (
        <Alert>{detail.error.message}</Alert>
      ) : (
        <OrderDetailBody order={detail.data} />
      )}
    </SidePanel>
  );
}

function Stat({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className="rounded-lg bg-canvas px-3 py-2.5">
      <p className="text-xs text-muted">{label}</p>
      <p className={cx("num mt-0.5 text-base font-semibold", className)}>{value}</p>
    </div>
  );
}

function OrderDetailBody({ order: o }: { order: OrderDetail }) {
  const taxes = o.tcs_paise !== 0 || o.tds_paise !== 0;
  const facts: [string, string | null][] = [
    ["Payment", o.payment_mode === "cod" ? "Cash on delivery" : "Prepaid"],
    ["Delivery", DELIVERY_LABELS[o.delivery_status] ?? o.delivery_status],
    ["Units", String(o.units)],
    ["Declared weight", o.declared_weight_g == null ? null : `${(o.declared_weight_g / 1000).toFixed(2)} kg`],
    ["Zone", o.zone],
    ["AWB", o.awb],
    ["Delivered", o.delivered_at && formatDate(o.delivered_at)],
    ["Returned", o.returned_at && formatDate(o.returned_at)],
    ["Payment due", o.due_date && formatDate(o.due_date)],
  ];

  const shown = (o.issues.length ? o.issues : [o.status]) as OrderDetail["status"][];

  return (
    <>
      <div className="mb-4 flex flex-wrap gap-1.5">
        {shown.map((s) => (
          <ReconBadge key={s} status={s} />
        ))}
      </div>
      <div className="mb-4 space-y-1.5 text-sm text-ink">
        {shown.map((s) => (
          <p key={s}>{explain(o, s)}</p>
        ))}
      </div>

      <div className="mb-6 grid grid-cols-3 gap-2">
        <Stat label="Expected" value={formatRupees(o.expected_paise)} />
        <Stat label="Received" value={formatRupees(o.received_paise)} />
        <Stat label="Difference" value={formatSigned(o.diff_paise)} className={diffClass(o.diff_paise)} />
      </div>

      {o.components.length > 0 && (
        <PanelSection title="By component">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th className="py-1.5 font-medium">Component</th>
                <th className="py-1.5 text-right font-medium">Expected</th>
                <th className="py-1.5 text-right font-medium">Received</th>
                <th className="py-1.5 text-right font-medium">Gap</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {o.components.map((c) => (
                <tr key={c.component}>
                  <td className="py-2">{c.label}</td>
                  <td className="num py-2 text-right text-muted">{formatRupees(c.expected_paise)}</td>
                  <td className="num py-2 text-right">{formatRupees(c.actual_paise)}</td>
                  <td className={cx("num py-2 text-right font-medium", c.gap_paise ? diffClass(c.gap_paise) : "text-faint")}>
                    {c.gap_paise ? formatSigned(c.gap_paise) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-line-strong font-semibold">
                <td className="py-2">Net</td>
                <td className="num py-2 text-right">{formatRupees(o.expected_paise)}</td>
                <td className="num py-2 text-right">{formatRupees(o.received_paise)}</td>
                <td className={cx("num py-2 text-right", diffClass(o.diff_paise))}>{formatSigned(o.diff_paise)}</td>
              </tr>
            </tfoot>
          </table>
          {taxes && (
            <p className="mt-3 rounded-md bg-canvas px-3 py-2 text-xs text-muted">
              Also withheld: TCS {formatRupees(Math.abs(o.tcs_paise))} and TDS {formatRupees(Math.abs(o.tds_paise))}.
              These are statutory deductions, credited back through tax filings, so they aren't part of the
              difference or of any claim.
            </p>
          )}
        </PanelSection>
      )}

      {o.claims.length > 0 && (
        <PanelSection title="Claims">
          <ul className="divide-y divide-line rounded-lg border border-line">
            {o.claims.map((c) => (
              <li key={c.id}>
                <Link
                  to={`/claims?claim=${c.id}`}
                  className="flex items-center justify-between gap-3 px-3 py-2.5 text-sm hover:bg-canvas"
                >
                  <span className="min-w-0">
                    <span className="font-medium">{CLAIM_TYPE_LABELS[c.type]}</span>
                    <span className="text-muted"> · {c.partner}</span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    <span className="num font-medium">{formatRupees(c.amount_paise)}</span>
                    <ClaimBadge status={c.status} />
                    <ChevronRight className="size-4 text-faint" aria-hidden />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </PanelSection>
      )}

      <PanelSection title="Where the numbers came from">
        {o.lines.length === 0 ? (
          <p className="text-sm text-muted">No partner has reported this order yet.</p>
        ) : (
          <ul className="divide-y divide-line rounded-lg border border-line">
            {o.lines.map((line) => (
              <SourceLine key={`${line.upload_id}-${line.row_no}-${line.component}`} line={line} />
            ))}
          </ul>
        )}
        {o.scans.length > 0 && (
          <ul className="mt-2 space-y-1 text-sm text-muted">
            {o.scans.map((s) => (
              <li key={`${s.upload_id}-${s.row_no}`}>
                Scanned back in at the warehouse on {formatDate(s.scanned_at)}
                {s.condition && ` (${s.condition})`} · {s.filename ?? "warehouse file"}, row {s.row_no}
              </li>
            ))}
          </ul>
        )}
      </PanelSection>

      <PanelSection title="Order">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          {facts
            .filter(([, value]) => value)
            .map(([label, value]) => (
              <div key={label}>
                <dt className="text-xs text-muted">{label}</dt>
                <dd className="num">{value}</dd>
              </div>
            ))}
        </dl>
      </PanelSection>

      <p className="border-t border-line pt-4 text-xs text-faint">
        {o.rate_cards.length > 0 &&
          `Priced with ${o.rate_cards.map((c) => `${c.partner} rate card v${c.version} (from ${formatDate(c.effective_from)})`).join(" and ")}. `}
        Matched {formatDateTime(o.computed_at)}.
      </p>
    </>
  );
}

function SourceLine({ line }: { line: OrderDetail["lines"][number] }) {
  const [open, setOpen] = useState(false);
  const raw = useSourceRow(line.upload_id, line.row_no, open);
  return (
    <li className="text-sm">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-start justify-between gap-3 px-3 py-2.5 text-left hover:bg-canvas"
      >
        <span className="min-w-0">
          <span className="font-medium">{COMPONENT_LABELS[line.component] ?? line.component}</span>
          <span className="text-muted"> · {line.partner}</span>
          <span className="block truncate text-xs text-faint">
            {formatDate(line.txn_date)} · {line.filename ?? "file"}, row {line.row_no}
            {line.billed_weight_g != null && ` · billed ${(line.billed_weight_g / 1000).toFixed(2)} kg`}
            {line.zone && ` · zone ${line.zone}`}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-1.5">
          <span className="num">{formatRupees(line.amount_paise)}</span>
          <ChevronDown className={cx("size-4 text-faint transition-transform", open && "rotate-180")} aria-hidden />
        </span>
      </button>
      {open && (
        <div className="border-t border-line bg-canvas px-3 py-2.5">
          {raw.isPending ? (
            <Spinner className="size-4 text-muted" />
          ) : raw.isError ? (
            <p className="text-xs text-loss">{raw.error.message}</p>
          ) : (
            <>
              <p className="mb-1.5 text-xs text-muted">
                As it appears in {raw.data.source}'s file, row {raw.data.row_no}
              </p>
              <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-xs">
                {raw.data.cells.map((cell) => (
                  <div key={cell.column} className="contents">
                    <dt className="text-muted">{cell.column}</dt>
                    <dd className="num truncate" title={cell.value ?? ""}>
                      {cell.value ?? "—"}
                    </dd>
                  </div>
                ))}
              </dl>
            </>
          )}
        </div>
      )}
    </li>
  );
}
