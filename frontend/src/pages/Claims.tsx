import { ArrowUpRight, CheckCircle2, Download, FileSearch, Search, Send } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { can, useMe } from "../auth/session";
import { Pager } from "../components/Pager";
import { PanelSection, SidePanel } from "../components/SidePanel";
import { ClaimBadge } from "../components/StatusBadges";
import { Alert, Button, EmptyState, Notice, PageHeader, Spinner, TextField, cx, useTitle } from "../components/ui";
import { formatCompact, formatCount, formatDate, formatDateTime, formatMonth, formatRupees } from "../lib/format";
import { AGE_LABELS } from "../lib/overview";
import { useDebouncedSearch, useFilterParams } from "../lib/params";
import {
  CLAIM_STATUS_LABELS,
  CLAIM_TYPE_LABELS,
  COMPONENT_LABELS,
  parseRupees,
  useClaimAction,
  useClaimDetail,
  useClaims,
  usePartnerTotals,
  useRaiseAll,
  type ClaimDetail,
  type ClaimRow,
  type ClaimStatus,
  type ClaimType,
  type PartnerTotal,
} from "../lib/recon";

const FILTER_KEYS = ["group", "partner_id", "type", "age", "q", "page"] as const;
const PAGE_SIZE = 50;
const LIVE: ClaimStatus[] = ["open", "raised", "partner_rejected"];

const GROUPS = [
  { value: "", label: "All live" },
  { value: "open", label: "To raise" },
  { value: "raised", label: "Raised" },
  { value: "partner_rejected", label: "Rejected" },
  { value: "closed", label: "Closed" },
];

export function Claims() {
  useTitle("Claims");
  const { values: filters, params, update } = useFilterParams(FILTER_KEYS);
  const selected = Number(params.get("claim")) || null;

  return (
    <>
      <PageHeader
        title="Claims"
        description="Money to recover from partners. Download the evidence, raise it with the partner, and record what comes back."
      />
      <PartnerCards
        activePartner={filters.partner_id}
        onPick={(id) => update({ partner_id: filters.partner_id === String(id) ? null : String(id) })}
      />
      <Tabs active={filters.group} onPick={(group) => update({ group })} />
      <ClaimFilters filters={filters} update={update} />
      <ClaimList
        filters={filters}
        onOpen={(id) => update({ claim: String(id) }, true)}
        onPage={(page) => update({ page: String(page) })}
      />
      {selected && <ClaimPanel key={selected} id={selected} onClose={() => update({ claim: null })} />}
    </>
  );
}

function PartnerCards({ activePartner, onPick }: { activePartner: string; onPick: (id: number) => void }) {
  const totals = usePartnerTotals();
  if (totals.isPending) {
    return (
      <div className="mb-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="h-32 animate-pulse rounded-xl bg-surface" />
        ))}
      </div>
    );
  }
  if (totals.isError || !totals.data.length) return null;
  return (
    // On a phone the cards swipe sideways, so the claims list isn't a screen away.
    <section
      aria-label="By partner"
      className="-mx-4 mb-6 flex snap-x gap-3 overflow-x-auto px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-2 sm:overflow-visible sm:px-0 xl:grid-cols-3"
    >
      {totals.data.map((t) => (
        <PartnerCard key={t.partner_id} total={t} active={activePartner === String(t.partner_id)} onPick={onPick} />
      ))}
    </section>
  );
}

function PartnerCard({
  total: t,
  active,
  onPick,
}: {
  total: PartnerTotal;
  active: boolean;
  onPick: (id: number) => void;
}) {
  const { data: me } = useMe();
  const raiseAll = useRaiseAll();
  const [raising, setRaising] = useState(false);
  const [reference, setReference] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    raiseAll.mutate(
      { partner_id: t.partner_id, partner_ref: reference.trim() || undefined },
      { onSuccess: () => setRaising(false) },
    );
  }

  return (
    <div
      className={cx(
        "w-[80vw] max-w-xs shrink-0 snap-start rounded-xl border bg-surface p-4 sm:w-auto sm:max-w-none",
        active ? "border-accent ring-3 ring-accent/10" : "border-line",
      )}
    >
      <button
        type="button"
        onClick={() => onPick(t.partner_id)}
        aria-pressed={active}
        className="block w-full text-left"
        title={active ? "Show every partner" : `Show only ${t.partner}`}
      >
        <p className="text-sm font-medium text-ink">{t.partner}</p>
        <p className="num mt-1 text-2xl font-semibold tracking-tight text-loss">{formatCompact(t.outstanding_paise)}</p>
        <p className="mt-0.5 text-xs text-muted">
          {formatCount(t.live_claims)} live {t.live_claims === 1 ? "claim" : "claims"}
          {t.open_claims > 0 &&
            (t.open_claims === t.live_claims ? ", none raised yet" : ` · ${formatCount(t.open_claims)} not yet raised`)}
          {t.recovered_paise > 0 && (
            <span className="text-gain"> · {formatCompact(t.recovered_paise)} recovered</span>
          )}
        </p>
      </button>

      {raiseAll.isSuccess && !raising && (
        <p className="mt-3 flex items-center gap-1.5 text-xs text-gain">
          <CheckCircle2 className="size-3.5" aria-hidden />
          Raised {formatCount(raiseAll.data.raised)} {raiseAll.data.raised === 1 ? "claim" : "claims"}
        </p>
      )}

      {raising ? (
        <form onSubmit={submit} className="mt-3 space-y-2 border-t border-line pt-3">
          <TextField
            label="Partner ticket or reference"
            placeholder="Optional"
            value={reference}
            maxLength={80}
            onChange={(e) => setReference(e.target.value)}
            autoFocus
          />
          {raiseAll.error && <Alert>{raiseAll.error.message}</Alert>}
          <div className="flex gap-2">
            <Button type="submit" loading={raiseAll.isPending}>
              Raise {formatCount(t.open_claims)}
            </Button>
            <Button variant="ghost" onClick={() => setRaising(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        (t.live_claims > 0 || t.open_claims > 0) && (
          <div className="mt-3 flex flex-wrap gap-2 border-t border-line pt-3">
            {can(me, "claim.export") && t.live_claims > 0 && (
              <a
                href={`/api/claims/evidence/${t.partner_id}.zip`}
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line-strong px-2.5 text-xs font-medium text-ink hover:bg-canvas"
              >
                <Download className="size-3.5" aria-hidden />
                Evidence (ZIP)
              </a>
            )}
            {can(me, "claim.work") && t.open_claims > 0 && (
              <button
                type="button"
                onClick={() => {
                  raiseAll.reset();
                  setRaising(true);
                }}
                className="inline-flex h-8 items-center gap-1.5 rounded-md bg-accent px-2.5 text-xs font-medium text-white hover:bg-accent-strong"
              >
                <Send className="size-3.5" aria-hidden />
                Raise all {formatCount(t.open_claims)}
              </button>
            )}
          </div>
        )
      )}
    </div>
  );
}

function Tabs({ active, onPick }: { active: string; onPick: (group: string | null) => void }) {
  return (
    <div className="-mx-4 mb-4 overflow-x-auto overflow-y-hidden border-b border-line px-4 sm:mx-0 sm:px-0">
      <div className="flex gap-1" role="group" aria-label="Claim status">
        {GROUPS.map((g) => {
          const on = g.value === active;
          return (
            <button
              key={g.value || "live"}
              type="button"
              aria-pressed={on}
              onClick={() => onPick(g.value || null)}
              className={cx(
                "-mb-px shrink-0 border-b-2 px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors",
                on ? "border-accent text-accent" : "border-transparent text-muted hover:text-ink",
              )}
            >
              {g.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

const control =
  "block h-9 w-full rounded-md border border-line-strong bg-surface px-3 text-sm focus:border-accent focus:ring-3 focus:ring-accent/15 focus:outline-none";

function ClaimFilters({
  filters,
  update,
}: {
  filters: Record<(typeof FILTER_KEYS)[number], string>;
  update: (changes: Partial<Record<string, string | null>>) => void;
}) {
  const [text, setText] = useDebouncedSearch(filters.q, (q) => update({ q }));
  return (
    <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-[minmax(0,16rem)_14rem_10rem]">
      <div className="relative col-span-2 sm:col-span-1">
        <Search className="pointer-events-none absolute top-2.5 left-3 size-4 text-faint" aria-hidden />
        <input
          type="search"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Order ID"
          aria-label="Search by order ID"
          maxLength={40}
          className={cx(control, "pl-9")}
        />
      </div>
      <select aria-label="Problem" value={filters.type} onChange={(e) => update({ type: e.target.value })} className={control}>
        <option value="">Every problem</option>
        {(Object.keys(CLAIM_TYPE_LABELS) as ClaimType[]).map((type) => (
          <option key={type} value={type}>
            {CLAIM_TYPE_LABELS[type]}
          </option>
        ))}
      </select>
      <select aria-label="Age" value={filters.age} onChange={(e) => update({ age: e.target.value })} className={control}>
        <option value="">Any age</option>
        {Object.entries(AGE_LABELS).map(([key, label]) => (
          <option key={key} value={key}>
            {label}
          </option>
        ))}
      </select>
    </div>
  );
}

function claimTitle(c: ClaimRow): string {
  if (c.order_id) return c.order_id;
  const component = c.component ? (COMPONENT_LABELS[c.component] ?? c.component) : "Small gaps";
  return `${component}${c.period_month ? `, ${formatMonth(c.period_month)}` : ""}`;
}

function ageClass(days: number): string {
  if (days > 60) return "text-loss font-medium";
  if (days > 30) return "text-warn font-medium";
  return "text-muted";
}

function ClaimList({
  filters,
  onOpen,
  onPage,
}: {
  filters: Record<(typeof FILTER_KEYS)[number], string>;
  onOpen: (id: number) => void;
  onPage: (page: number) => void;
}) {
  const claims = useClaims({ ...filters, group: filters.group || undefined, page: filters.page || undefined });
  const closed = filters.group === "closed";

  if (claims.isPending) {
    return (
      <div className="space-y-2" aria-label="Loading claims">
        {Array.from({ length: 8 }, (_, i) => (
          <div key={i} className="h-12 animate-pulse rounded-lg bg-surface" />
        ))}
      </div>
    );
  }
  if (claims.isError) {
    return (
      <Alert>
        Couldn't load the claims. {claims.error.message}{" "}
        <button type="button" className="font-medium underline" onClick={() => claims.refetch()}>
          Try again
        </button>
      </Alert>
    );
  }
  const { rows, total, total_paise, page } = claims.data;
  if (!rows.length) {
    const filtered = filters.partner_id || filters.type || filters.age || filters.q;
    return (
      <EmptyState icon={filtered ? FileSearch : CheckCircle2} title={filtered ? "No claims match" : "Nothing here"}>
        {filtered
          ? "Try another partner, problem or order ID."
          : closed
            ? "Claims appear here once they're recovered, written off, or paid in a later file."
            : "No claims in this state. New ones appear as partner files are matched."}
      </EmptyState>
    );
  }

  return (
    <section aria-label="Claims" className={cx(claims.isPlaceholderData && "opacity-60 transition-opacity")}>
      <p className="mb-2 text-sm text-muted">
        <span className="num font-medium text-ink">{formatCount(total)}</span> {total === 1 ? "claim" : "claims"}
        {!closed && (
          <>
            {" · "}
            <span className="num font-medium text-loss">{formatRupees(total_paise)}</span> to recover
          </>
        )}
      </p>

      {/* Phone: one card per claim */}
      <ul className="space-y-2 md:hidden">
        {rows.map((c) => (
          <li key={c.id}>
            <button
              type="button"
              onClick={() => onOpen(c.id)}
              className="w-full rounded-lg border border-line bg-surface p-3.5 text-left text-sm active:bg-canvas"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate font-medium">{claimTitle(c)}</p>
                  <p className="text-xs text-muted">
                    {c.type_label} · {c.partner}
                  </p>
                </div>
                <ClaimBadge status={c.status} />
              </div>
              <div className="num mt-2 flex justify-between text-xs">
                <span className="font-medium text-ink">{formatRupees(c.amount_paise)}</span>
                <span className={ageClass(c.age_days)}>{formatCount(c.age_days)} days</span>
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
              <th className="px-4 py-2.5 font-medium">Claim</th>
              <th className="px-4 py-2.5 font-medium">Problem</th>
              <th className="px-4 py-2.5 text-right font-medium">Amount</th>
              <th className="px-4 py-2.5 text-right font-medium" title="Days since the money was due">
                Age
              </th>
              <th className="px-4 py-2.5 font-medium">Status</th>
              <th className="px-4 py-2.5 font-medium">Reference</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((c) => (
              <tr key={c.id} className="cursor-pointer hover:bg-canvas/60" onClick={() => onOpen(c.id)}>
                <td className="px-4 py-2.5">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpen(c.id);
                    }}
                    className="font-medium text-ink hover:text-accent"
                  >
                    {claimTitle(c)}
                  </button>
                  <p className="text-xs text-muted">{c.partner}</p>
                </td>
                <td className="px-4 py-2.5 whitespace-nowrap">{c.type_label}</td>
                <td className="num px-4 py-2.5 text-right">
                  {formatRupees(c.amount_paise)}
                  {c.recovered_paise > 0 && (
                    <p className="text-xs text-gain">{formatRupees(c.recovered_paise)} back</p>
                  )}
                </td>
                <td className={cx("num px-4 py-2.5 text-right whitespace-nowrap", ageClass(c.age_days))}>
                  {formatCount(c.age_days)} d
                </td>
                <td className="px-4 py-2.5">
                  <ClaimBadge status={c.status} />
                </td>
                <td className="max-w-40 truncate px-4 py-2.5 text-muted">{c.partner_ref ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager page={page} pageSize={PAGE_SIZE} total={total} onPage={onPage} />
    </section>
  );
}

function ClaimPanel({ id, onClose }: { id: number; onClose: () => void }) {
  const detail = useClaimDetail(id);
  const c = detail.data;
  return (
    <SidePanel title={c ? claimTitle(c) : "Claim"} subtitle={c && `${c.type_label} · ${c.partner}`} onClose={onClose}>
      {detail.isPending ? (
        <div className="grid place-items-center py-16 text-muted">
          <Spinner />
        </div>
      ) : detail.isError ? (
        <Alert>{detail.error.message}</Alert>
      ) : (
        <ClaimDetailBody claim={detail.data} />
      )}
    </SidePanel>
  );
}

function describe(c: ClaimDetail): string {
  switch (c.type) {
    case "return_not_received":
      return `${c.partner} marked this order as returned, but the warehouse never scanned it back in. The claim is for the goods.`;
    case "unpaid_past_due":
      return `${c.partner} hasn't paid for this order, and its reports already cover the date it was due.`;
    case "short_paid":
      return `${c.partner} paid less than its rate card says for this order.`;
    case "freight_overcharged":
      return `${c.partner} billed more freight than its rate card says.`;
    case "systematic_gap":
      return `Each gap was under ₹10, so no single order was claimed, but together they go one way: ${c.partner} keeps paying a little less on ${COMPONENT_LABELS[c.component ?? ""]?.toLowerCase() ?? "this component"}.`;
  }
}

function ClaimDetailBody({ claim: c }: { claim: ClaimDetail }) {
  const live = LIVE.includes(c.status);
  // Kept here, not in the actions, so it stays on screen after the claim closes.
  const [done, setDone] = useState<string | null>(null);
  const facts: [string, string | null][] = [
    ["Claimed", formatRupees(c.amount_paise)],
    ["Recovered", c.recovered_paise ? formatRupees(c.recovered_paise) : null],
    ["Ageing from", formatDate(c.aging_from)],
    ["Age", `${formatCount(c.age_days)} days`],
    ["Partner reference", c.partner_ref],
    ["Opened", formatDateTime(c.opened_at)],
  ];

  return (
    <>
      <div className="mb-3 flex items-center gap-2">
        <ClaimBadge status={c.status} />
        {c.order_pk && (
          <Link
            to={`/reconciliation?order=${c.order_pk}`}
            className="inline-flex items-center gap-1 text-sm font-medium text-accent hover:text-accent-strong"
          >
            See the order's numbers
            <ArrowUpRight className="size-3.5" aria-hidden />
          </Link>
        )}
      </div>
      <p className="mb-5 text-sm text-ink">{describe(c)}</p>
      {done && (
        <div className="mb-5">
          <Notice tone="gain">{done}</Notice>
        </div>
      )}

      <dl className="mb-6 grid grid-cols-2 gap-x-4 gap-y-2.5 text-sm">
        {facts
          .filter(([, value]) => value)
          .map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs text-muted">{label}</dt>
              <dd className="num font-medium">{value}</dd>
            </div>
          ))}
      </dl>

      {live && <ClaimActions claim={c} onDone={setDone} />}

      {c.orders.length > 0 && (
        <PanelSection
          title={`Orders in this pattern (${formatCount(c.order_count)})`}
          aside={
            c.order_count > c.orders.length && (
              <span className="text-xs text-faint">largest {formatCount(c.orders.length)} shown</span>
            )
          }
        >
          <ul className="max-h-64 divide-y divide-line overflow-y-auto rounded-lg border border-line text-sm">
            {c.orders.map((o) => (
              <li key={o.id}>
                <Link
                  to={`/reconciliation?order=${o.id}`}
                  className="flex justify-between gap-3 px-3 py-2 hover:bg-canvas"
                >
                  <span>{o.order_id}</span>
                  <span className="num text-loss">{formatRupees(o.amount_paise)}</span>
                </Link>
              </li>
            ))}
          </ul>
        </PanelSection>
      )}

      <PanelSection title="History">
        <ol className="space-y-3 border-l border-line pl-4">
          {c.events.map((e, i) => (
            <li key={i} className="relative text-sm">
              <span className="absolute top-1.5 -left-[1.3rem] size-2 rounded-full bg-line-strong" aria-hidden />
              <p className="font-medium">{CLAIM_STATUS_LABELS[e.to_status as ClaimStatus] ?? e.to_status}</p>
              {e.note && <p className="text-muted">{e.note}</p>}
              <p className="text-xs text-faint">
                {formatDateTime(e.at)}
                {e.by ? ` · ${e.by}` : " · Settle"}
              </p>
            </li>
          ))}
        </ol>
      </PanelSection>
    </>
  );
}

function rupeesInput(paise: number): string {
  return (paise / 100).toFixed(2);
}

function ClaimActions({ claim: c, onDone }: { claim: ClaimDetail; onDone: (message: string | null) => void }) {
  const { data: me } = useMe();
  const action = useClaimAction(c.id);
  const [mode, setMode] = useState<null | "recover" | "write_off">(null);
  const [reference, setReference] = useState(c.partner_ref ?? "");
  const [amount, setAmount] = useState(rupeesInput(c.amount_paise));
  const [problem, setProblem] = useState<string | null>(null);

  if (!can(me, "claim.work")) return null;
  const canRaise = c.status === "open" || c.status === "partner_rejected";

  function run(body: Parameters<typeof action.mutate>[0], message: string) {
    setProblem(null);
    onDone(null);
    // Not mutate's onSuccess: a closing action unmounts this block before it would fire.
    action.mutateAsync(body).then(
      () => {
        setMode(null);
        onDone(message);
      },
      () => {}, // shown from action.error
    );
  }

  function raise(event: FormEvent) {
    event.preventDefault();
    run({ action: "raise", partner_ref: reference.trim() || undefined }, `Marked as raised with ${c.partner}.`);
  }

  function recover(event: FormEvent) {
    event.preventDefault();
    const paise = parseRupees(amount);
    if (paise === null || paise <= 0) return setProblem("Enter the amount in rupees, for example 1409.50.");
    if (paise > c.amount_paise) return setProblem(`That's more than the claim of ${formatRupees(c.amount_paise)}.`);
    run({ action: "recover", recovered_paise: paise }, `Recorded ${formatRupees(paise)} recovered.`);
  }

  const error = problem ?? action.error?.message;
  return (
    <PanelSection title="Next step">
      <div className="space-y-3 rounded-lg border border-line p-3.5">

        {canRaise && mode === null && (
          <form onSubmit={raise} className="space-y-2">
            <TextField
              label={c.status === "partner_rejected" ? "Raise again, with a new ticket or reference" : `Raise with ${c.partner}`}
              placeholder="Ticket or reference (optional)"
              value={reference}
              maxLength={80}
              onChange={(e) => setReference(e.target.value)}
            />
            <Button type="submit" loading={action.isPending && action.variables?.action === "raise"}>
              Mark as raised
            </Button>
          </form>
        )}

        {mode === "recover" ? (
          <form onSubmit={recover} className="space-y-2">
            <TextField
              label="Amount recovered (₹)"
              inputMode="decimal"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              autoFocus
            />
            <div className="flex gap-2">
              <Button type="submit" loading={action.isPending}>
                Record recovery
              </Button>
              <Button variant="ghost" onClick={() => setMode(null)}>
                Cancel
              </Button>
            </div>
          </form>
        ) : mode === "write_off" ? (
          <div className="space-y-2">
            <p className="text-sm">
              Write off {formatRupees(c.amount_paise)}? The claim closes and the money counts as lost. This is recorded
              in the audit log.
            </p>
            <div className="flex gap-2">
              <Button
                variant="danger"
                loading={action.isPending}
                onClick={() => run({ action: "write_off" }, "Written off.")}
              >
                Write off
              </Button>
              <Button variant="ghost" onClick={() => setMode(null)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className={cx("flex flex-wrap gap-2", canRaise && "border-t border-line pt-3")}>
            <Button
              variant={c.status === "raised" ? "primary" : "secondary"}
              onClick={() => {
                setAmount(rupeesInput(c.amount_paise));
                setMode("recover");
              }}
            >
              Money came back
            </Button>
            {c.status === "raised" && (
              <Button
                variant="secondary"
                loading={action.isPending && action.variables?.action === "reject"}
                onClick={() => run({ action: "reject" }, `Marked as rejected by ${c.partner}.`)}
              >
                Partner rejected it
              </Button>
            )}
            {can(me, "claim.write_off") && (
              <Button variant="ghost" onClick={() => setMode("write_off")}>
                Write off
              </Button>
            )}
          </div>
        )}

        {error && <Alert>{error}</Alert>}
      </div>
    </PanelSection>
  );
}
