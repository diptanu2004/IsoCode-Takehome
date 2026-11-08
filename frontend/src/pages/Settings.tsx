import { KeyRound, UserPlus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { ROLE_LABELS, can, useMe, type Role } from "../auth/session";
import { Pager } from "../components/Pager";
import { Alert, Badge, Button, Notice, PageHeader, TextField, cx, useTitle } from "../components/ui";
import { formatCount, formatDate, formatDateTime, formatRupees } from "../lib/format";
import { parseRupees, usePartners, type Partner } from "../lib/recon";
import {
  useAudit,
  useChangePartner,
  useChangeUser,
  useCreateUser,
  useUsers,
  type AuditEntry,
  type Rematched,
} from "../lib/settings";

type Tab = "partners" | "people" | "activity";

export function Settings() {
  useTitle("Settings");
  const { data: me } = useMe();
  const [params, setParams] = useSearchParams();
  const tabs: { key: Tab; label: string; show: boolean }[] = [
    { key: "partners", label: "Partners", show: true },
    { key: "people", label: "People", show: can(me, "user.manage") },
    { key: "activity", label: can(me, "audit.view_all") ? "Activity" : "My activity", show: true },
  ];
  const visible = tabs.filter((t) => t.show);
  const tab = visible.find((t) => t.key === params.get("tab"))?.key ?? "partners";

  return (
    <>
      <PageHeader title="Settings" description="Partner terms, the people who can sign in, and a record of every change." />
      <div className="-mx-4 mb-5 overflow-x-auto overflow-y-hidden border-b border-line px-4 sm:mx-0 sm:px-0">
        <div className="flex gap-1" role="group" aria-label="Settings section">
          {visible.map((t) => (
            <button
              key={t.key}
              type="button"
              aria-pressed={t.key === tab}
              onClick={() => setParams({ tab: t.key }, { replace: true })}
              className={cx(
                "-mb-px shrink-0 border-b-2 px-3 py-2 text-sm font-medium whitespace-nowrap",
                t.key === tab ? "border-accent text-accent" : "border-transparent text-muted hover:text-ink",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      {tab === "partners" && <PartnerSettings editable={can(me, "settings.edit")} />}
      {tab === "people" && <People myId={me?.id ?? 0} />}
      {tab === "activity" && <Activity />}
    </>
  );
}

// --- Partners ----------------------------------------------------------------------------

const KIND_LABELS: Record<Partner["kind"], string> = {
  storefront: "Own website",
  marketplace: "Marketplace",
  quick_commerce: "Quick commerce",
  courier: "Courier",
  internal: "Internal",
};

function PartnerSettings({ editable }: { editable: boolean }) {
  const partners = usePartners();
  const [editing, setEditing] = useState<number | null>(null);
  const [saved, setSaved] = useState<{ name: string; result: Rematched } | null>(null);
  const list = (partners.data ?? []).filter((p) => p.kind !== "internal");

  if (partners.isPending) return <div className="h-64 animate-pulse rounded-xl bg-surface" />;
  if (partners.isError) return <Alert>Couldn't load the partners. {partners.error.message}</Alert>;

  return (
    <section className="space-y-4">
      <dl className="grid gap-3 text-sm sm:grid-cols-3">
        {[
          ["Pays within", "Days after delivery (or the return) by which the partner should have paid."],
          ["Tolerance", "Gaps up to this are not claimed order by order. They're added up to spot a pattern."],
          ["Return window", "Days a return may take to reach the warehouse before it counts as lost."],
        ].map(([term, text]) => (
          <div key={term} className="rounded-lg border border-line bg-surface px-3 py-2.5">
            <dt className="font-medium text-ink">{term}</dt>
            <dd className="text-xs text-muted">{text}</dd>
          </div>
        ))}
      </dl>
      {saved && (
        <Notice tone="gain">
          {saved.name} saved. {formatCount(saved.result.orders_matched)} orders re-checked:{" "}
          {formatCount(saved.result.claims_opened)} claims opened, {formatCount(saved.result.claims_updated)} updated,{" "}
          {formatCount(saved.result.claims_resolved)} closed.
        </Notice>
      )}
      {!editable && <p className="text-sm text-muted">Only the co-founder can change these.</p>}

      <div className="overflow-x-auto rounded-xl border border-line bg-surface">
        <table className="w-full min-w-[40rem] text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              <th className="px-4 py-2.5 font-medium">Partner</th>
              <th className="px-4 py-2.5 text-right font-medium">Pays within</th>
              <th className="px-4 py-2.5 text-right font-medium">Tolerance</th>
              <th className="px-4 py-2.5 text-right font-medium">Return window</th>
              <th className="w-32 px-4 py-2.5" />
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {list.map((p) =>
              editing === p.id ? (
                <PartnerEditRow
                  key={p.id}
                  partner={p}
                  onDone={(result) => {
                    setEditing(null);
                    if (result) setSaved({ name: p.name, result });
                  }}
                />
              ) : (
                <tr key={p.id}>
                  <td className="px-4 py-2.5">
                    <p className="font-medium">{p.name}</p>
                    <p className="text-xs text-muted">{KIND_LABELS[p.kind]}</p>
                  </td>
                  <td className="num px-4 py-2.5 text-right">{p.settlement_cycle_days} days</td>
                  <td className="num px-4 py-2.5 text-right">{formatRupees(p.tolerance_paise)}</td>
                  <td className="num px-4 py-2.5 text-right">{p.return_window_days} days</td>
                  <td className="px-4 py-2.5 text-right">
                    {editable && (
                      <Button
                        variant="ghost"
                        className="h-8"
                        onClick={() => {
                          setSaved(null);
                          setEditing(p.id);
                        }}
                      >
                        Change
                      </Button>
                    )}
                  </td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

const cell =
  "num block h-8 w-20 rounded-md border border-line-strong bg-surface px-2 text-right text-sm focus:border-accent focus:ring-3 focus:ring-accent/15 focus:outline-none";

function PartnerEditRow({ partner: p, onDone }: { partner: Partner; onDone: (result: Rematched | null) => void }) {
  const change = useChangePartner();
  const [cycle, setCycle] = useState(String(p.settlement_cycle_days));
  const [tolerance, setTolerance] = useState((p.tolerance_paise / 100).toFixed(2));
  const [window, setWindow] = useState(String(p.return_window_days));
  const [problem, setProblem] = useState<string | null>(null);

  function save(event: FormEvent) {
    event.preventDefault();
    const days = Number(cycle);
    const returnDays = Number(window);
    const paise = parseRupees(tolerance);
    if (!Number.isInteger(days) || days < 1 || days > 90) return setProblem("Pays within: 1 to 90 days.");
    if (paise === null || paise > 100_000) return setProblem("Tolerance: ₹0 to ₹1,000.");
    if (!Number.isInteger(returnDays) || returnDays < 0 || returnDays > 60) {
      return setProblem("Return window: 0 to 60 days.");
    }
    setProblem(null);
    change.mutate(
      { id: p.id, settlement_cycle_days: days, tolerance_paise: paise, return_window_days: returnDays },
      { onSuccess: (result) => onDone(result) },
    );
  }

  const error = problem ?? change.error?.message;
  return (
    <tr className="bg-accent-soft/40">
      <td className="px-4 py-2.5 align-top">
        <p className="font-medium">{p.name}</p>
        {error && <p className="mt-1 text-xs text-loss">{error}</p>}
        {change.isPending && <p className="mt-1 text-xs text-muted">Saving and re-checking its orders…</p>}
      </td>
      <td className="px-4 py-2.5">
        <input aria-label="Pays within (days)" inputMode="numeric" value={cycle} onChange={(e) => setCycle(e.target.value)} className={cx(cell, "ml-auto")} />
      </td>
      <td className="px-4 py-2.5">
        <input aria-label="Tolerance (₹)" inputMode="decimal" value={tolerance} onChange={(e) => setTolerance(e.target.value)} className={cx(cell, "ml-auto")} />
      </td>
      <td className="px-4 py-2.5">
        <input aria-label="Return window (days)" inputMode="numeric" value={window} onChange={(e) => setWindow(e.target.value)} className={cx(cell, "ml-auto")} />
      </td>
      <td className="px-4 py-2.5">
        <form onSubmit={save} className="flex justify-end gap-1">
          <Button type="submit" className="h-8 px-2.5" loading={change.isPending}>
            Save
          </Button>
          <Button variant="ghost" className="h-8 px-2" onClick={() => onDone(null)} disabled={change.isPending}>
            Cancel
          </Button>
        </form>
      </td>
    </tr>
  );
}

// --- People ------------------------------------------------------------------------------

function People({ myId }: { myId: number }) {
  const users = useUsers(true);
  const change = useChangeUser();
  const [adding, setAdding] = useState(false);

  if (users.isPending) return <div className="h-48 animate-pulse rounded-xl bg-surface" />;
  if (users.isError) return <Alert>Couldn't load the people. {users.error.message}</Alert>;

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted">
          Finance executives upload files, map columns and work claims. Co-founders can also write claims off, change rate
          cards and settings, and manage people.
        </p>
        {!adding && (
          <Button variant="secondary" onClick={() => setAdding(true)} className="shrink-0">
            <UserPlus className="size-4" aria-hidden />
            Add person
          </Button>
        )}
      </div>
      {adding && <AddPerson onDone={() => setAdding(false)} />}
      {change.error && <Alert>{change.error.message}</Alert>}

      <ul className="divide-y divide-line rounded-xl border border-line bg-surface">
        {users.data.map((u) => {
          const self = u.id === myId;
          return (
            <li key={u.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="flex items-center gap-2 font-medium">
                  <span className={cx(!u.is_active && "text-muted line-through")}>{u.name}</span>
                  {self && <Badge>You</Badge>}
                  {!u.is_active && <Badge tone="warn">Can't sign in</Badge>}
                </p>
                <p className="truncate text-xs text-muted">
                  {u.email} · added {formatDateTime(u.created_at)}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <select
                  aria-label={`Role of ${u.name}`}
                  value={u.role}
                  disabled={self || change.isPending}
                  onChange={(e) => change.mutate({ id: u.id, role: e.target.value as Role })}
                  className="h-8 rounded-md border border-line-strong bg-surface px-2 text-sm disabled:opacity-60"
                >
                  {(Object.keys(ROLE_LABELS) as Role[]).map((role) => (
                    <option key={role} value={role}>
                      {ROLE_LABELS[role]}
                    </option>
                  ))}
                </select>
                {!self && (
                  <Button
                    variant="ghost"
                    className="h-8"
                    disabled={change.isPending}
                    onClick={() => change.mutate({ id: u.id, is_active: !u.is_active })}
                  >
                    {u.is_active ? "Remove access" : "Restore access"}
                  </Button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function randomPassword(): string {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
}

function AddPerson({ onDone }: { onDone: () => void }) {
  const create = useCreateUser();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("exec");
  const [password, setPassword] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) {
      return setProblem("Enter a name and a valid email.");
    }
    if (password.length < 12) return setProblem("The first password needs at least 12 characters.");
    setProblem(null);
    create.mutate({ name: name.trim(), email: email.trim(), role, password }, { onSuccess: onDone });
  }

  const error = problem ?? create.error?.message;
  return (
    <form onSubmit={submit} className="space-y-3 rounded-xl border border-line bg-surface p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField label="Name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        <TextField label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        <label className="block space-y-1.5 text-sm">
          <span className="font-medium text-ink">Role</span>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
            className="block h-10 w-full rounded-md border border-line-strong bg-surface px-3 text-sm"
          >
            {(Object.keys(ROLE_LABELS) as Role[]).map((r) => (
              <option key={r} value={r}>
                {ROLE_LABELS[r]}
              </option>
            ))}
          </select>
        </label>
        <div className="space-y-1.5">
          <TextField
            label="First password"
            type="text"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <button
            type="button"
            onClick={() => setPassword(randomPassword())}
            className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:text-accent-strong"
          >
            <KeyRound className="size-3.5" aria-hidden />
            Generate one
          </button>
        </div>
      </div>
      <p className="text-xs text-muted">Share the first password with them directly. It isn't shown again.</p>
      {error && <Alert>{error}</Alert>}
      <div className="flex gap-2">
        <Button type="submit" loading={create.isPending}>
          Add person
        </Button>
        <Button variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

// --- Activity ----------------------------------------------------------------------------

const AREAS = [
  { value: "", label: "Everything" },
  { value: "claim", label: "Claims" },
  { value: "rate_card", label: "Rate cards" },
  { value: "source", label: "Column mappings" },
  { value: "partner", label: "Partner settings" },
  { value: "user", label: "People" },
];

const FIELD_LABELS: Record<string, string> = {
  settlement_cycle_days: "pays within (days)",
  tolerance_paise: "tolerance",
  return_window_days: "return window (days)",
  is_active: "can sign in",
  role: "role",
  status: "status",
  recovered_paise: "recovered",
  partner_ref: "partner reference",
  version: "layout version",
};

function show(key: string, value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (key.endsWith("_paise") && typeof value === "number") return formatRupees(value);
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (key === "role" && typeof value === "string") return ROLE_LABELS[value as Role] ?? value;
  return String(value).replaceAll("_", " ");
}

function describe(e: AuditEntry): string {
  const subject = e.subject ?? `${e.entity} ${e.entity_id}`;
  const after = e.after ?? {};
  switch (e.action) {
    case "claim.raise":
      return `Raised ${subject} with the partner${after.partner_ref ? ` (ref ${after.partner_ref})` : ""}`;
    case "claim.recover":
      return `Recorded ${show("recovered_paise", after.recovered_paise)} recovered on ${subject}`;
    case "claim.reject":
      return `Marked ${subject} as rejected by the partner`;
    case "claim.write_off":
      return `Wrote off ${subject}`;
    case "rate_card.create":
      return `Added a rate card: ${subject}, from ${formatDate(String(after.effective_from))}`;
    case "mapping.approve":
      return `Approved column layout v${after.version} for ${subject}`;
    case "mapping.activate":
      return `Switched ${subject} to column layout v${after.version}`;
    case "partner.update":
      return `Changed ${subject}'s settings`;
    case "user.create":
      return `Added ${after.email} as ${show("role", after.role)}`;
    case "user.update":
      return `Changed access for ${subject}`;
    default:
      return `${e.action} on ${subject}`;
  }
}

function changes(e: AuditEntry): string | null {
  if (!["partner.update", "user.update"].includes(e.action) || !e.after) return null;
  return Object.entries(e.after)
    .map(([k, v]) => `${FIELD_LABELS[k] ?? k}: ${show(k, e.before?.[k])} → ${show(k, v)}`)
    .join("; ");
}

function Activity() {
  const [params, setParams] = useSearchParams();
  const area = params.get("area") ?? "";
  const page = params.get("page") ?? "";
  const audit = useAudit(area, page);
  const update = (key: string, value: string) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (value) next.set(key, value);
        else next.delete(key);
        if (key !== "page") next.delete("page");
        return next;
      },
      { replace: true },
    );

  return (
    <section className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted">
          {audit.data?.everyone === false
            ? "Your own changes. The co-founder sees everyone's."
            : "Every change anyone made, newest first. Nothing here can be edited or deleted."}
        </p>
        <select
          aria-label="Area"
          value={area}
          onChange={(e) => update("area", e.target.value)}
          className="h-9 rounded-md border border-line-strong bg-surface px-3 text-sm sm:w-48"
        >
          {AREAS.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
        </select>
      </div>

      {audit.isPending ? (
        <div className="h-48 animate-pulse rounded-xl bg-surface" />
      ) : audit.isError ? (
        <Alert>Couldn't load the activity. {audit.error.message}</Alert>
      ) : audit.data.rows.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line-strong bg-surface px-4 py-10 text-center text-sm text-muted">
          Nothing recorded yet.
        </p>
      ) : (
        <>
          <ol className={cx("divide-y divide-line rounded-xl border border-line bg-surface", audit.isPlaceholderData && "opacity-60")}>
            {audit.data.rows.map((e) => (
              <li key={e.id} className="flex flex-col gap-0.5 px-4 py-2.5 text-sm sm:flex-row sm:gap-4">
                <span className="num shrink-0 text-xs text-muted sm:w-28 sm:pt-0.5">{formatDateTime(e.at)}</span>
                <span className="min-w-0">
                  <span className="text-ink">{describe(e)}</span>
                  {changes(e) && <span className="block text-xs text-muted">{changes(e)}</span>}
                  <span className="block text-xs text-faint">{e.actor ?? "Settle"}</span>
                </span>
              </li>
            ))}
          </ol>
          <Pager
            page={audit.data.page}
            pageSize={audit.data.page_size}
            total={audit.data.total}
            onPage={(p) => update("page", String(p))}
          />
        </>
      )}
    </section>
  );
}
