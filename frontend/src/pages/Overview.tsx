import { AlertTriangle, ArrowRight, CheckCircle2, FileSpreadsheet } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { Alert, EmptyState, PageHeader, cx, useTitle } from "../components/ui";
import { formatCompact, formatCount, formatDate, formatDateTime, formatRupees } from "../lib/format";
import { AGE_LABELS, heatStep, useOverview, type OverviewData } from "../lib/overview";

/** Sequential shading in the loss hue (TDD §11.1: red is money at risk); text stays in ink. */
const HEAT = ["", "7%", "14%", "22%", "32%", "44%"];

function heat(step: number): string | undefined {
  return step ? `color-mix(in oklab, var(--color-loss) ${HEAT[step]}, var(--color-surface))` : undefined;
}

export function Overview() {
  useTitle("Overview");
  const overview = useOverview();

  if (overview.isPending) {
    return (
      <>
        <PageHeader title="Overview" description="Money at risk across partners, by age." />
        <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="h-32 animate-pulse rounded-xl bg-surface" />
          ))}
        </div>
        <div className="h-72 animate-pulse rounded-xl bg-surface" />
      </>
    );
  }
  if (overview.isError) {
    return (
      <>
        <PageHeader title="Overview" />
        <Alert>
          Couldn't load the overview. {overview.error.message}{" "}
          <button type="button" className="font-medium underline" onClick={() => overview.refetch()}>
            Try again
          </button>
        </Alert>
      </>
    );
  }

  const o = overview.data;
  const freshness = `As of ${formatDate(o.as_of)}${o.last_file_at ? `. Last file loaded ${formatDateTime(o.last_file_at)}` : ""}.`;
  if (!o.orders) {
    return (
      <>
        <PageHeader title="Overview" description="Money at risk across partners, by age." />
        <EmptyState
          icon={FileSpreadsheet}
          title="Nothing matched yet"
          action={
            <Link to="/uploads" className="text-sm font-medium text-accent hover:text-accent-strong">
              Go to Uploads
            </Link>
          }
        >
          Upload the orders export and the partners' settlement, COD remittance and freight files. Money at risk
          appears here by partner and age as each file is matched.
        </EmptyState>
      </>
    );
  }

  return (
    <>
      <PageHeader title="Overview" description={freshness} />
      <Kpis o={o} />
      <RiskTable o={o} />
      <Taxes o={o} />
    </>
  );
}

function Tile({ label, children, footer }: { label: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="flex flex-col rounded-xl border border-line bg-surface p-4">
      <p className="text-sm text-muted">{label}</p>
      <div className="mt-1 flex-1">{children}</div>
      {footer && <div className="mt-3 border-t border-line pt-2.5 text-xs text-muted">{footer}</div>}
    </div>
  );
}

function Kpis({ o }: { o: OverviewData }) {
  const { at_risk: risk, recovery, auto_match: match, within_tolerance: small } = o;
  const share = recovery.recovered_paise / recovery.target_paise;
  const onTarget = match.rate !== null && match.rate >= match.target;

  return (
    <section aria-label="Key numbers" className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Tile
        label="Money at risk"
        footer={
          <Link to="/claims" className="inline-flex items-center gap-1 font-medium text-accent hover:text-accent-strong">
            {formatCompact(risk.not_raised_paise)} not yet raised
            <ArrowRight className="size-3.5" aria-hidden />
          </Link>
        }
      >
        <p className="num text-2xl font-semibold tracking-tight text-loss">{formatCompact(risk.total_paise)}</p>
        <p className="text-xs text-muted">across {formatCount(risk.claims)} live claims</p>
      </Tile>

      <Tile
        label="Recovered"
        footer={
          recovery.written_off_paise || recovery.paid_late_paise ? (
            <>
              {recovery.paid_late_paise > 0 && `${formatCompact(recovery.paid_late_paise)} paid late after flagging`}
              {recovery.paid_late_paise > 0 && recovery.written_off_paise > 0 && " · "}
              {recovery.written_off_paise > 0 && `${formatCompact(recovery.written_off_paise)} written off`}
            </>
          ) : (
            "Recorded on the Claims screen as money comes back"
          )
        }
      >
        <p className="num text-2xl font-semibold tracking-tight text-ink">{formatCompact(recovery.recovered_paise)}</p>
        <div
          className="mt-2 h-1.5 overflow-hidden rounded-full bg-gain-soft ring-1 ring-gain/15 ring-inset"
          role="meter"
          aria-label="Recovered against the year-one target"
          aria-valuemin={0}
          aria-valuemax={recovery.target_paise}
          aria-valuenow={recovery.recovered_paise}
        >
          <div
            className="h-full rounded-full bg-gain"
            style={{ width: `${Math.min(100, share * 100)}%`, minWidth: recovery.recovered_paise > 0 ? 3 : 0 }}
          />
        </div>
        <p className="mt-1 text-xs text-muted">
          {share < 0.001 && recovery.recovered_paise > 0 ? "<0.1" : (share * 100).toFixed(1)}% of the{" "}
          {formatCompact(recovery.target_paise)} year-one target
        </p>
      </Tile>

      <Tile
        label="Orders matched automatically"
        footer={`${formatCount(match.unmatched_rows)} partner ${match.unmatched_rows === 1 ? "row matches" : "rows match"} no order`}
      >
        <p className="num text-2xl font-semibold tracking-tight text-ink">
          {match.rate === null ? "—" : `${(match.rate * 100).toFixed(2)}%`}
        </p>
        {match.rate !== null && (
          <p className={cx("mt-0.5 inline-flex items-center gap-1 text-xs font-medium", onTarget ? "text-gain" : "text-warn")}>
            {onTarget ? <CheckCircle2 className="size-3.5" aria-hidden /> : <AlertTriangle className="size-3.5" aria-hidden />}
            {onTarget ? "On target" : "Below target"} ({Math.round(match.target * 100)}%)
          </p>
        )}
      </Tile>

      <Tile
        label="Within tolerance"
        footer={
          small.patterns > 0 ? (
            <Link
              to="/claims?type=systematic_gap"
              className="inline-flex items-center gap-1 font-medium text-accent hover:text-accent-strong"
            >
              {formatCount(small.patterns)} one-way {small.patterns === 1 ? "pattern" : "patterns"} claimed
              <ArrowRight className="size-3.5" aria-hidden />
            </Link>
          ) : (
            "No one-way pattern in the small gaps"
          )
        }
      >
        <p className="num text-2xl font-semibold tracking-tight text-ink">{formatCompact(small.gap_paise)}</p>
        <p className="text-xs text-muted">
          across {formatCount(small.orders)} orders, each off by ₹10 or less
        </p>
      </Tile>
    </section>
  );
}

function RiskTable({ o }: { o: OverviewData }) {
  const risk = o.at_risk;
  const max = Math.max(0, ...risk.partners.flatMap((p) => p.by_age));
  const cellTitle = (partner: string, key: string, paise: number, claims: number) =>
    `${partner}, ${AGE_LABELS[key].toLowerCase()}: ${formatRupees(paise)} across ${formatCount(claims)} ${claims === 1 ? "claim" : "claims"}`;

  return (
    <section aria-labelledby="risk-title" className="mb-6 rounded-xl border border-line bg-surface">
      <div className="flex flex-col gap-1 px-4 pt-4 pb-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 id="risk-title" className="text-sm font-semibold text-ink">
            Money at risk by partner and age
          </h2>
          <p className="text-xs text-muted">Days since the money was due. Open a cell to see its claims.</p>
        </div>
        <Legend />
      </div>

      {risk.partners.length === 0 ? (
        <p className="px-4 pb-5 text-sm text-muted">No live claims. Nothing is at risk right now.</p>
      ) : (
        <>
          {/* Phone: one block per partner */}
          <ul className="divide-y divide-line border-t border-line md:hidden">
            {risk.partners.map((p) => (
              <li key={p.partner_id} className="px-4 py-3">
                <Link to={`/claims?partner_id=${p.partner_id}`} className="flex items-baseline justify-between">
                  <span className="text-sm font-medium">{p.partner}</span>
                  <span className="num text-sm font-semibold text-loss">{formatCompact(p.total_paise)}</span>
                </Link>
                <div className="mt-2 grid grid-cols-5 gap-1">
                  {risk.buckets.map((key, i) => (
                    <Link
                      key={key}
                      to={`/claims?partner_id=${p.partner_id}&age=${key}`}
                      title={cellTitle(p.partner, key, p.by_age[i], p.claims_by_age[i])}
                      className="rounded-md px-1 py-1.5 text-center ring-1 ring-line ring-inset"
                      style={{ background: heat(heatStep(p.by_age[i], max)) }}
                    >
                      <span className="block text-[10px] text-muted">{key}</span>
                      <span className="num block text-xs font-medium">
                        {p.by_age[i] ? formatCompact(p.by_age[i]) : "—"}
                      </span>
                    </Link>
                  ))}
                </div>
              </li>
            ))}
          </ul>

          {/* Desktop: partner × age */}
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full border-separate border-spacing-0.5 px-3.5 pb-3.5 text-sm">
              <thead>
                <tr className="text-xs text-muted">
                  <th className="px-2 py-2 text-left font-medium">Partner</th>
                  {risk.buckets.map((key) => (
                    <th key={key} className="px-2 py-2 text-right font-medium">
                      {AGE_LABELS[key]}
                    </th>
                  ))}
                  <th className="px-2 py-2 text-right font-medium">Total</th>
                </tr>
              </thead>
              <tbody>
                {risk.partners.map((p) => (
                  <tr key={p.partner_id}>
                    <th scope="row" className="px-2 py-2.5 text-left font-medium">
                      <Link to={`/claims?partner_id=${p.partner_id}`} className="hover:text-accent">
                        {p.partner}
                      </Link>
                    </th>
                    {risk.buckets.map((key, i) => (
                      <td key={key} className="p-0 text-right">
                        {p.by_age[i] ? (
                          <Link
                            to={`/claims?partner_id=${p.partner_id}&age=${key}`}
                            title={cellTitle(p.partner, key, p.by_age[i], p.claims_by_age[i])}
                            className="num block rounded-md px-2 py-2.5 text-ink outline-offset-0 hover:ring-2 hover:ring-loss/40 hover:ring-inset"
                            style={{ background: heat(heatStep(p.by_age[i], max)) }}
                          >
                            {formatCompact(p.by_age[i])}
                          </Link>
                        ) : (
                          <span className="block px-2 py-2.5 text-faint">—</span>
                        )}
                      </td>
                    ))}
                    <td className="num px-2 py-2.5 text-right font-semibold">{formatCompact(p.total_paise)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="text-ink">
                  <th scope="row" className="border-t border-line-strong px-2 py-2.5 text-left font-semibold">
                    All partners
                  </th>
                  {risk.buckets.map((key, i) => (
                    <td key={key} className="num border-t border-line-strong px-2 py-2.5 text-right font-semibold">
                      {risk.by_age[i] ? (
                        <Link to={`/claims?age=${key}`} className="hover:text-accent">
                          {formatCompact(risk.by_age[i])}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                  ))}
                  <td className="num border-t border-line-strong px-2 py-2.5 text-right font-semibold text-loss">
                    {formatCompact(risk.total_paise)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

function Legend() {
  return (
    <div className="flex items-center gap-1.5 text-xs text-muted" aria-hidden>
      <span>Less</span>
      {[1, 2, 3, 4, 5].map((step) => (
        <span key={step} className="size-3.5 rounded-sm ring-1 ring-line ring-inset" style={{ background: heat(step) }} />
      ))}
      <span>More</span>
    </div>
  );
}

function Taxes({ o }: { o: OverviewData }) {
  if (!o.taxes.length) return null;
  const tcs = o.taxes.reduce((sum, t) => sum + t.tcs_paise, 0);
  const tds = o.taxes.reduce((sum, t) => sum + t.tds_paise, 0);
  return (
    <section aria-labelledby="tax-title" className="rounded-xl border border-line bg-surface p-4">
      <h2 id="tax-title" className="text-sm font-semibold text-ink">
        TCS and TDS withheld by partners
      </h2>
      <p className="mt-0.5 mb-3 max-w-2xl text-xs text-muted">
        Statutory deductions, not leakage. They're credited back through GST and income-tax filings, so they're
        kept out of every difference and every claim. Shown here so they can be reconciled with Form 26AS and GSTR-2B.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full max-w-2xl text-sm">
          <thead>
            <tr className="border-b border-line text-xs text-muted">
              <th className="py-2 text-left font-medium">Partner</th>
              <th className="py-2 text-right font-medium">TCS</th>
              <th className="py-2 text-right font-medium">TDS</th>
              <th className="py-2 text-right font-medium">Total</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {o.taxes.map((t) => (
              <tr key={t.partner_id}>
                <td className="py-2">{t.partner}</td>
                <td className="num py-2 text-right">{formatRupees(t.tcs_paise)}</td>
                <td className="num py-2 text-right">{formatRupees(t.tds_paise)}</td>
                <td className="num py-2 text-right text-muted">{formatRupees(t.tcs_paise + t.tds_paise)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-line-strong font-semibold">
              <td className="py-2">All partners</td>
              <td className="num py-2 text-right">{formatRupees(tcs)}</td>
              <td className="num py-2 text-right">{formatRupees(tds)}</td>
              <td className="num py-2 text-right">{formatRupees(tcs + tds)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}
