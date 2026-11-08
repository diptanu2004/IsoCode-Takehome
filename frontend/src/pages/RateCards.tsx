import { AlertTriangle, ChevronDown, Plus, Receipt } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { can, useMe } from "../auth/session";
import { PanelSection, SidePanel } from "../components/SidePanel";
import { Alert, Badge, Button, EmptyState, Notice, PageHeader, Spinner, TextField, cx, useTitle } from "../components/ui";
import { formatCount, formatDate, formatDateTime, formatMonth, formatRupees } from "../lib/format";
import {
  KIND_LABELS,
  ZONES,
  fromForm,
  toForm,
  useAddVersion,
  useRateCards,
  type CardVersion,
  type CourierRules,
  type MarketplaceRules,
  type PartnerCards,
  type RuleForm,
  type Rules,
  type SavedVersion,
} from "../lib/ratecards";
import { COMPONENT_LABELS } from "../lib/recon";

export function RateCards() {
  useTitle("Rate cards");
  const cards = useRateCards();
  const [params, setParams] = useSearchParams();
  const [saved, setSaved] = useState<{ partner: string; result: SavedVersion } | null>(null);

  const list = cards.data ?? [];
  const selected = list.find((p) => String(p.partner_id) === params.get("partner")) ?? list[0];
  const pick = (id: number) => {
    setSaved(null);
    setParams({ partner: String(id) }, { replace: true });
  };

  return (
    <>
      <PageHeader
        title="Rate cards"
        description="What each channel and courier should charge. Expected amounts on every order come from these, by order date."
      />
      {cards.isPending ? (
        <div className="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
          <div className="h-80 animate-pulse rounded-xl bg-surface" />
          <div className="h-80 animate-pulse rounded-xl bg-surface" />
        </div>
      ) : cards.isError ? (
        <Alert>Couldn't load the rate cards. {cards.error.message}</Alert>
      ) : !selected ? (
        <EmptyState icon={Receipt} title="No rate cards yet">
          Rate cards are created when the partners are set up.
        </EmptyState>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)] lg:items-start">
          <PartnerPicker list={list} selected={selected} onPick={pick} />
          <PartnerDetail
            key={selected.partner_id}
            partner={selected}
            saved={saved?.partner === selected.partner ? saved.result : null}
            onSaved={(result) => setSaved({ partner: selected.partner, result })}
          />
        </div>
      )}
    </>
  );
}

function PartnerPicker({
  list,
  selected,
  onPick,
}: {
  list: PartnerCards[];
  selected: PartnerCards;
  onPick: (id: number) => void;
}) {
  return (
    <>
      <select
        aria-label="Partner"
        value={selected.partner_id}
        onChange={(e) => onPick(Number(e.target.value))}
        className="block h-10 w-full rounded-md border border-line-strong bg-surface px-3 text-sm lg:hidden"
      >
        {list.map((p) => (
          <option key={p.partner_id} value={p.partner_id}>
            {p.partner}
            {p.suspected.length ? " (check rates)" : ""}
          </option>
        ))}
      </select>
      <nav aria-label="Partners" className="hidden rounded-xl border border-line bg-surface p-1.5 lg:block">
        {list.map((p) => {
          const current = p.versions[0];
          const on = p.partner_id === selected.partner_id;
          return (
            <button
              key={p.partner_id}
              type="button"
              aria-current={on || undefined}
              onClick={() => onPick(p.partner_id)}
              className={cx(
                "flex w-full items-start justify-between gap-2 rounded-md px-3 py-2 text-left text-sm",
                on ? "bg-accent-soft" : "hover:bg-canvas",
              )}
            >
              <span className="min-w-0">
                <span className={cx("block font-medium", on ? "text-accent" : "text-ink")}>{p.partner}</span>
                <span className="block text-xs text-muted">
                  {KIND_LABELS[p.kind]}
                  {current && ` · v${current.version}`}
                </span>
              </span>
              {p.suspected.length > 0 && (
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" aria-label="Rates to check" />
              )}
            </button>
          );
        })}
      </nav>
    </>
  );
}

function PartnerDetail({
  partner: p,
  saved,
  onSaved,
}: {
  partner: PartnerCards;
  saved: SavedVersion | null;
  onSaved: (result: SavedVersion) => void;
}) {
  const { data: me } = useMe();
  const [editing, setEditing] = useState(false);
  const [current, ...earlier] = p.versions;

  return (
    <section aria-labelledby="card-title" className="min-w-0 rounded-xl border border-line bg-surface">
      <div className="flex flex-col gap-3 border-b border-line px-4 py-4 sm:flex-row sm:items-start sm:justify-between sm:px-5">
        <div>
          <h2 id="card-title" className="text-base font-semibold text-ink">
            {p.partner}
          </h2>
          <p className="text-sm text-muted">
            {KIND_LABELS[p.kind]}
            {current && ` · version ${current.version}, in force since ${formatDate(current.effective_from)}`}
          </p>
        </div>
        {current && can(me, "rate_card.edit") && (
          <Button variant="secondary" onClick={() => setEditing(true)}>
            <Plus className="size-4" aria-hidden />
            New version
          </Button>
        )}
      </div>

      <div className="space-y-5 px-4 py-5 sm:px-5">
        {saved && (
          <Notice tone="gain">
            Version {saved.version} saved. {formatCount(saved.orders_matched)} orders re-checked against it:{" "}
            {formatCount(saved.claims_opened)} claims opened, {formatCount(saved.claims_updated)} updated,{" "}
            {formatCount(saved.claims_resolved)} closed.
          </Notice>
        )}
        {p.suspected.length > 0 && <Suspected partner={p} />}

        {current ? (
          <>
            <VersionMeta version={current} current />
            <RulesView rules={current.rules} />
          </>
        ) : (
          <p className="text-sm text-muted">No rate card yet, so orders through {p.partner} can't be checked.</p>
        )}

        {earlier.length > 0 && (
          <PanelSection title="Earlier versions">
            <ul className="divide-y divide-line rounded-lg border border-line">
              {earlier.map((v) => (
                <EarlierVersion key={v.id} version={v} />
              ))}
            </ul>
          </PanelSection>
        )}
      </div>

      {editing && current && (
        <NewVersionPanel
          partner={p}
          current={current}
          onClose={() => setEditing(false)}
          onSaved={(result) => {
            setEditing(false);
            onSaved(result);
          }}
        />
      )}
    </section>
  );
}

function Suspected({ partner: p }: { partner: PartnerCards }) {
  return (
    <div role="status" className="rounded-md bg-warn-soft px-3 py-2.5 text-sm text-warn ring-1 ring-warn/20 ring-inset">
      <p className="flex items-center gap-1.5 font-medium">
        <AlertTriangle className="size-4" aria-hidden />
        A rate may have changed without notice
      </p>
      <ul className="mt-1 space-y-0.5">
        {p.suspected.map((s) => (
          <li key={s.claim_id}>
            Small gaps on {(COMPONENT_LABELS[s.component] ?? s.component).toLowerCase()} in{" "}
            {formatMonth(s.period_month)} all go one way, {formatRupees(s.amount_paise)} in total.{" "}
            <Link to={`/claims?claim=${s.claim_id}`} className="font-medium underline">
              See the claim
            </Link>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-xs">
        Check with {p.partner}. If the rate did change, add a new version from the date it changed.
      </p>
    </div>
  );
}

function VersionMeta({ version: v, current = false }: { version: CardVersion; current?: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
      {current && <Badge tone="gain">In force</Badge>}
      <span>
        {formatDate(v.effective_from)} – {v.effective_to ? formatDate(v.effective_to) : "now"}
      </span>
      <span>Priced {formatCount(v.orders_priced)} orders</span>
      <span>
        Added {formatDateTime(v.created_at)}
        {v.created_by ? ` by ${v.created_by}` : " at setup"}
      </span>
      {v.note && <span className="w-full text-ink">“{v.note}”</span>}
    </div>
  );
}

function EarlierVersion({ version: v }: { version: CardVersion }) {
  const [open, setOpen] = useState(false);
  return (
    <li>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left text-sm hover:bg-canvas"
      >
        <span>
          <span className="font-medium">Version {v.version}</span>
          <span className="text-muted">
            {" "}
            · {formatDate(v.effective_from)} – {v.effective_to ? formatDate(v.effective_to) : "now"}
          </span>
        </span>
        <ChevronDown className={cx("size-4 text-faint transition-transform", open && "rotate-180")} aria-hidden />
      </button>
      {open && (
        <div className="space-y-3 border-t border-line bg-canvas/50 px-3 py-3">
          <VersionMeta version={v} />
          <RulesView rules={v.rules} />
        </div>
      )}
    </li>
  );
}

function Rule({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(0,11rem)_minmax(0,1fr)] gap-3 py-2 text-sm">
      <dt className="text-muted">{label}</dt>
      <dd className="text-ink">{children}</dd>
    </div>
  );
}

const rupees = (value: number) => formatRupees(Math.round(value * 100));
const yesNo = (value: boolean) => (value ? "Yes" : "No");

function RulesView({ rules }: { rules: Rules }) {
  return rules.type === "marketplace" ? <MarketplaceView rules={rules} /> : <CourierView rules={rules} />;
}

function MarketplaceView({ rules: r }: { rules: MarketplaceRules }) {
  return (
    <dl className="divide-y divide-line">
      <Rule label="Commission">
        {r.commission_pct}% of the sale price, rounded {r.commission_rounding === "rupee" ? "to the rupee" : "to the paisa"}
      </Rule>
      <Rule label="Fixed fee">{rupees(r.fixed_fee)} per order</Rule>
      <Rule label="Shipping fee">
        {r.shipping
          ? `${rupees(r.shipping.first)} for the first ${r.shipping.slab_g} g, then ${rupees(r.shipping.additional)} for each further ${r.shipping.slab_g} g`
          : "None: this channel doesn't charge for shipping"}
      </Rule>
      <Rule label="On a return">
        Commission refunded: {yesNo(r.on_return.refund_commission)}. Fixed fee refunded:{" "}
        {yesNo(r.on_return.refund_fixed_fee)}. Return shipping: {r.on_return.return_shipping_pct_of_forward}% of the
        forward shipping fee.
      </Rule>
    </dl>
  );
}

function CourierView({ rules: r }: { rules: CourierRules }) {
  const zones = ZONES.filter((z) => r.forward[z]);
  return (
    <>
      <dl className="divide-y divide-line">
        <Rule label="Weight slab">{r.slab_g} g; weight is rounded up to whole slabs</Rule>
        <Rule label="Return freight">{r.return_pct_of_forward}% of the forward freight</Rule>
        <Rule label="COD fee">
          {r.cod_fee.pct}% of the order value, at least {rupees(r.cod_fee.min)}
        </Rule>
      </dl>
      <table className="mt-2 w-full max-w-md text-sm">
        <thead>
          <tr className="border-b border-line text-xs text-muted">
            <th className="py-1.5 text-left font-medium">Zone</th>
            <th className="py-1.5 text-right font-medium">First {r.slab_g} g</th>
            <th className="py-1.5 text-right font-medium">Each further {r.slab_g} g</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {zones.map((z) => (
            <tr key={z}>
              <td className="py-1.5">{z}</td>
              <td className="num py-1.5 text-right">{rupees(r.forward[z].first)}</td>
              <td className="num py-1.5 text-right">{rupees(r.forward[z].additional)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

const input =
  "block h-9 w-full rounded-md border border-line-strong bg-surface px-2.5 text-sm focus:border-accent focus:ring-3 focus:ring-accent/15 focus:outline-none";

function Field({
  label,
  name,
  form,
  set,
  unit,
}: {
  label: string;
  name: string;
  form: RuleForm;
  set: (name: string, value: string) => void;
  unit?: string;
}) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="font-medium text-ink">{label}</span>
      <span className="flex items-center gap-1.5">
        <input
          inputMode="decimal"
          value={form[name] ?? ""}
          onChange={(e) => set(name, e.target.value)}
          className={cx(input, "num")}
        />
        {unit && <span className="shrink-0 text-xs text-muted">{unit}</span>}
      </span>
    </label>
  );
}

function YesNo({ label, name, form, set }: { label: string; name: string; form: RuleForm; set: (n: string, v: string) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input
        type="checkbox"
        checked={form[name] === "yes"}
        onChange={(e) => set(name, e.target.checked ? "yes" : "no")}
        className="size-4 accent-[var(--color-accent)]"
      />
      {label}
    </label>
  );
}

function NewVersionPanel({
  partner,
  current,
  onClose,
  onSaved,
}: {
  partner: PartnerCards;
  current: CardVersion;
  onClose: () => void;
  onSaved: (result: SavedVersion) => void;
}) {
  const add = useAddVersion(partner.partner_id);
  const [form, setForm] = useState<RuleForm>(() => toForm(current.rules));
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const set = (name: string, value: string) => setForm((f) => ({ ...f, [name]: value }));

  function submit(event: FormEvent) {
    event.preventDefault();
    setProblem(null);
    if (!effectiveFrom) return setProblem("Choose the date the new rates start.");
    if (effectiveFrom <= current.effective_from) {
      return setProblem(`The new version must start after ${formatDate(current.effective_from)}.`);
    }
    if (note.trim().length < 3) return setProblem("Say why the rates changed, so the change can be traced.");
    const built = fromForm(current.rules.type, form);
    if ("problem" in built) return setProblem(built.problem);
    add.mutate({ effective_from: effectiveFrom, rules: built.rules, note: note.trim() }, { onSuccess: onSaved });
  }

  const error = problem ?? add.error?.message;
  return (
    <SidePanel
      title={`New rate card for ${partner.partner}`}
      subtitle={`Starts from version ${current.version}'s rates. Orders from the start date are checked again when you save.`}
      onClose={onClose}
    >
      <form onSubmit={submit} className="space-y-6">
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField
            label="Starts on"
            type="date"
            value={effectiveFrom}
            min={current.effective_from}
            onChange={(e) => setEffectiveFrom(e.target.value)}
          />
        </div>
        <TextField
          label="Why the rates changed"
          placeholder="e.g. Email from the partner, 28 Sep: commission 15% → 12%"
          value={note}
          maxLength={500}
          onChange={(e) => setNote(e.target.value)}
        />

        {current.rules.type === "marketplace" ? (
          <>
            <PanelSection title="Fees on a sale">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Commission" name="commission_pct" form={form} set={set} unit="% of sale" />
                <label className="block space-y-1 text-sm">
                  <span className="font-medium text-ink">Commission rounded</span>
                  <select
                    value={form.commission_rounding}
                    onChange={(e) => set("commission_rounding", e.target.value)}
                    className={input}
                  >
                    <option value="paise">To the paisa</option>
                    <option value="rupee">To the rupee</option>
                  </select>
                </label>
                <Field label="Fixed fee" name="fixed_fee" form={form} set={set} unit="₹ per order" />
              </div>
            </PanelSection>
            <PanelSection title="Shipping fee">
              <YesNo label="This channel charges for shipping" name="shipping" form={form} set={set} />
              {form.shipping === "yes" && (
                <div className="mt-3 grid gap-3 sm:grid-cols-3">
                  <Field label="Slab" name="shipping.slab_g" form={form} set={set} unit="g" />
                  <Field label="First slab" name="shipping.first" form={form} set={set} unit="₹" />
                  <Field label="Each further slab" name="shipping.additional" form={form} set={set} unit="₹" />
                </div>
              )}
            </PanelSection>
            <PanelSection title="On a return">
              <div className="space-y-2">
                <YesNo label="Commission is refunded" name="on_return.refund_commission" form={form} set={set} />
                <YesNo label="Fixed fee is refunded" name="on_return.refund_fixed_fee" form={form} set={set} />
                <div className="max-w-56 pt-1">
                  <Field
                    label="Return shipping"
                    name="on_return.return_shipping_pct_of_forward"
                    form={form}
                    set={set}
                    unit="% of forward"
                  />
                </div>
              </div>
            </PanelSection>
          </>
        ) : (
          <>
            <PanelSection title="Freight">
              <div className="mb-3 grid gap-3 sm:grid-cols-2">
                <Field label="Weight slab" name="slab_g" form={form} set={set} unit="g" />
                <Field label="Return freight" name="return_pct_of_forward" form={form} set={set} unit="% of forward" />
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-muted">
                    <th className="py-1 text-left font-medium">Zone</th>
                    <th className="py-1 text-left font-medium">First slab (₹)</th>
                    <th className="py-1 text-left font-medium">Each further slab (₹)</th>
                  </tr>
                </thead>
                <tbody>
                  {ZONES.map((z) => (
                    <tr key={z}>
                      <td className="py-1 pr-2 font-medium">{z}</td>
                      {(["first", "additional"] as const).map((part) => (
                        <td key={part} className="py-1 pr-2">
                          <input
                            inputMode="decimal"
                            aria-label={`Zone ${z}, ${part === "first" ? "first slab" : "each further slab"}`}
                            value={form[`forward.${z}.${part}`] ?? ""}
                            onChange={(e) => set(`forward.${z}.${part}`, e.target.value)}
                            className={cx(input, "num")}
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-1 text-xs text-muted">Leave a zone empty if the courier doesn't serve it.</p>
            </PanelSection>
            <PanelSection title="COD fee">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Fee" name="cod_fee.pct" form={form} set={set} unit="% of order value" />
                <Field label="Minimum" name="cod_fee.min" form={form} set={set} unit="₹" />
              </div>
            </PanelSection>
          </>
        )}

        {error && <Alert>{error}</Alert>}
        <div className="flex items-center gap-2 border-t border-line pt-4">
          <Button type="submit" loading={add.isPending}>
            {add.isPending ? "Saving and re-checking orders…" : "Save version " + (current.version + 1)}
          </Button>
          <Button variant="ghost" onClick={onClose} disabled={add.isPending}>
            Cancel
          </Button>
          {add.isPending && <Spinner className="size-4 text-muted" />}
        </div>
      </form>
    </SidePanel>
  );
}
