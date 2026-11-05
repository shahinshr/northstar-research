"use client";

import { useQuery } from "@tanstack/react-query";
import { Play } from "lucide-react";
import { CompareLines, FinBars } from "@/components/charts";
import { JobPill, useJobTracker } from "@/components/jobs";
import { Empty, Note, Skeleton, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { ago, isNum, num, pct } from "@/lib/format";

/* eslint-disable @typescript-eslint/no-explicit-any */
function PassTile({ title, d, fmtDiff = (x: number) => pct(x, 2) }: { title: string; d: any; fmtDiff?: (x: number) => string }) {
  if (!d || d.error) return <Stat label={title} value="—" sub={d?.error ?? "not run"} />;
  if (!d.n) return <Stat label={title} value="N/A" sub="No comparable data (key missing?)" />;
  const tone = d.pass_rate >= 0.95 ? "text-up" : d.pass_rate >= 0.8 ? "text-ink" : "text-down";
  return <Stat label={title} value={<span className={tone}>{pct(d.pass_rate, 0)} within tolerance</span>} sub={`n=${d.n} · median diff ${fmtDiff(d.median_diff)}`} />;
}

function Exceptions({ rows, cols }: { rows: any[]; cols: [string, string, (r: any) => string][] }) {
  if (!rows?.length) return <p className="text-xs text-muted">No exceptions.</p>;
  return (
    <table className="w-full text-xs tnum">
      <thead><tr className="text-left text-muted">{cols.map(([k, l]) => <th key={k} className="py-1 pr-3 font-semibold">{l}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i} className="border-t border-line">{cols.map(([k, , f]) => <td key={k} className="py-1 pr-3 text-ink2">{f(r)}</td>)}</tr>)}</tbody>
    </table>
  );
}

export default function ValidationPage() {
  const { job, track, busy } = useJobTracker();
  const q = useQuery({ queryKey: ["validation"], queryFn: api.validation, refetchInterval: busy ? 10000 : false });
  const r = q.data?.report;
  const run = () => api.runValidation().then(track).catch((e: Error) => alert(e.message));
  if (q.isLoading) return <Skeleton className="h-96" />;
  const bt = r?.backtest;
  const data = r?.data;
  const funds = r?.funds;
  const an = r?.analysts;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Validation</h1>
          <p className="text-sm text-ink2">Is the data right, and does the ranking carry information? {q.data?.generated_at ? `Last run ${ago(q.data.generated_at)}.` : ""}</p>
        </div>
        <div className="flex items-center gap-2"><JobPill job={job} />
          <button className="btn-primary" disabled={busy} onClick={run}><Play size={14} /> Run validation</button></div>
      </div>
      {!r ? <Empty title="No validation report yet">Run validation after the first full discovery run. It downloads SEC XBRL frames (~150 MB, cached) and samples Finnhub.</Empty> : (
        <>
          {/* ---------------------------------------------------------------- 1. data */}
          <section className="space-y-3">
            <h2 className="text-lg font-semibold text-ink">1 · Data accuracy vs independent sources</h2>
            <div className="grid gap-3 md:grid-cols-3">
              <PassTile title="Prices vs Finnhub (±0.5%)" d={data?.prices_vs_finnhub} />
              <PassTile title="Statements vs SEC 10-K (±2%)" d={data?.statements_vs_sec} />
              <PassTile title="Metrics vs Finnhub (definitional)" d={data?.metrics_vs_finnhub} fmtDiff={(x) => num(x, 3)} />
            </div>
            {data?.metrics_vs_finnhub?.by ? (
              <div className="card p-4 text-sm">
                <div className="label mb-2">By metric</div>
                <div className="flex flex-wrap gap-6">{Object.entries(data.metrics_vs_finnhub.by).map(([k, v]: [string, any]) => (
                  <span key={k} className="text-ink2">{k}: <b className="text-ink">{pct(v.pass_rate, 0)}</b> <span className="text-muted">(n={v.n}, median diff {num(v.median_diff, 3)})</span></span>))}</div>
                <div className="label mb-1 mt-4">Largest exceptions (for review)</div>
                <Exceptions rows={[...(data.statements_vs_sec?.exceptions ?? []), ...(data.metrics_vs_finnhub?.exceptions ?? []).slice(0, 5)]}
                  cols={[["symbol", "Symbol", (x) => x.symbol], ["what", "Field", (x) => x.field ?? x.metric],
                    ["ours", "Ours", (x) => (Math.abs(x.ours) > 1e5 ? `${(x.ours / 1e9).toFixed(2)}B` : num(x.ours, 3))],
                    ["ref", "Reference", (x) => { const v = x.sec ?? x.finnhub; return Math.abs(v) > 1e5 ? `${(v / 1e9).toFixed(2)}B` : num(v, 3); }],
                    ["diff", "Diff", (x) => num(x.diff, 3)]]} />
              </div>) : null}
            <ul className="space-y-0.5 text-xs text-muted">{(data?.notes ?? []).map((n: string) => <li key={n}>· {n}</li>)}</ul>
          </section>

          {/* ---------------------------------------------------------------- 2. backtest */}
          <section className="space-y-3">
            <h2 className="text-lg font-semibold text-ink">2 · Does the core score rank future returns? (walk-forward backtest)</h2>
            {!bt || bt.error ? <Note tone="warn">{bt?.error ?? "Backtest not run"}</Note> : (
              <>
                <p className="text-sm text-ink2">{bt.universe} · {bt.period} · {bt.rebalances} quarterly rebalances · {bt.forward_horizon} forward total returns ·
                  SEC data used only {bt.pit_lag_days} days after period end.</p>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                  <Stat label="Top quintile (gross)" value={pct(bt.quintile_annual_returns["5"], 1)} sub={`net of costs ${pct(bt.top_quintile_net_of_costs, 1)}`} />
                  <Stat label="Bottom quintile" value={pct(bt.quintile_annual_returns["1"], 1)} sub={`spread ${pct(bt.spread_top_minus_bottom, 1, true)}/yr`} />
                  <Stat label="Equal-weight universe" value={pct(bt.equal_weight_annual, 1)} sub={isNum(bt.spy_annual) ? `SPY ${pct(bt.spy_annual, 1)}` : undefined} />
                  <Stat label="Mean rank IC" value={num(bt.mean_ic, 3)} sub={`t = ${num(bt.ic_t_stat, 1)} · positive ${pct(bt.ic_positive_share, 0)} of quarters`} />
                  <Stat label="Top beats universe" value={pct(bt.top_beats_ew_share, 0)} sub={`of quarters · turnover ${pct(bt.avg_top_turnover, 0)}/qtr`} />
                </div>
                <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
                  <div className="card p-4">
                    <h3 className="text-sm font-semibold text-ink">Growth of 100 — top vs bottom quintile, equal-weight universe and SPY</h3>
                    <p className="mb-2 text-xs text-muted">Gross of costs; quarterly rebalanced; survivorship-biased universe (see caveats)</p>
                    <CompareLines series={(bt.cumulative ?? []).map((p: any) => ({ d: p.d, "Top quintile": p.top, "Equal weight": p.ew, "Bottom quintile": p.bottom, SPY: p.spy }))}
                      symbols={["Top quintile", "Equal weight", "Bottom quintile", "SPY"]} />
                  </div>
                  <div className="card space-y-4 p-4">
                    <FinBars data={[1, 2, 3, 4, 5].map((k) => ({ p: `Q${k}`, v: bt.quintile_annual_returns[String(k)] * 100 }))} label="Annualised return by score quintile (Q5 = best)" currency="%" />
                    <div>
                      <div className="label mb-2">Which components carried signal? (mean rank IC)</div>
                      {Object.entries(bt.component_mean_ic ?? {}).map(([k, v]: [string, any]) => (
                        <div key={k} className="grid grid-cols-[90px_1fr_52px] items-center gap-2 py-0.5 text-sm">
                          <span className="capitalize text-ink2">{k}</span>
                          <span className="relative h-2 rounded-full bg-grid"><span className="absolute inset-y-0 left-1/2 w-px" style={{ background: "var(--axis)" }} />
                            <span className="absolute inset-y-0 rounded-full" style={{ background: v >= 0 ? "var(--s1)" : "var(--s8)",
                              left: v >= 0 ? "50%" : `${50 + Math.max(-50, v * 500)}%`, width: `${Math.min(50, Math.abs(v) * 500)}%` }} /></span>
                          <span className="tnum text-right text-ink">{num(v, 3)}</span>
                        </div>))}
                      <p className="mt-1 text-2xs text-muted">IC = Spearman correlation of score with next-quarter return. 0.02–0.05 is typical for useful equity factors.</p>
                    </div>
                  </div>
                </div>
                <Note tone="warn"><b>Biases you should know:</b><ul className="mt-1 list-disc space-y-0.5 pl-4">{(bt.biases ?? []).map((b: string) => <li key={b}>{b}</li>)}</ul></Note>
              </>)}
          </section>

          {/* ---------------------------------------------------------------- 3. analysts */}
          <section className="space-y-3">
            <h2 className="text-lg font-semibold text-ink">3 · Agreement with external opinions</h2>
            {an?.n ? (
              <div className="grid gap-3 md:grid-cols-3">
                <Stat label="Score vs analyst target upside" value={num(an.corr_score_vs_target_upside, 2)} sub="Spearman, current snapshot" />
                <Stat label="Score vs consensus rating" value={num(an.corr_score_vs_rating, 2)} sub="positive = analysts rate our top names higher" />
                <Stat label="Avg rating, best vs worst quintile" value={an.avg_rating_by_quintile ? `${num(an.avg_rating_by_quintile["5"], 2)} vs ${num(an.avg_rating_by_quintile["1"], 2)}` : "—"} sub="1 = strong buy, 5 = sell" />
              </div>) : <Note>{an?.error ?? "Not enough data"}</Note>}
            <p className="text-xs text-muted">{an?.note}</p>
          </section>

          {/* ---------------------------------------------------------------- 4. funds */}
          <section className="space-y-3">
            <h2 className="text-lg font-semibold text-ink">4 · Funds: does lower cost mean a better outcome?</h2>
            {funds?.funds?.length ? (
              <div className="card overflow-x-auto p-4">
                <p className="mb-2 text-sm text-ink2">Cohort: {funds.cohort} · correlation (expense ratio vs tracking difference): <b>{num(funds.corr_expense_vs_tracking, 2)}</b> ·
                  mean unexplained gap: US-listed {pct(funds.us_domiciled_mean_residual, 2, true)}, Canada/UK-listed {pct(funds.foreign_domiciled_mean_residual, 2, true)}</p>
                <table className="w-full text-sm tnum">
                  <thead><tr className="text-left text-2xs uppercase tracking-wide text-muted">
                    {["Fund", "Ccy", "Expense ratio", "Tracking diff (3y)", "Unexplained", "Fund score"].map((h) => <th key={h} className="py-1 pr-4 font-semibold">{h}</th>)}</tr></thead>
                  <tbody>{funds.funds.sort((a: any, b: any) => b.tracking_difference - a.tracking_difference).map((f: any) => (
                    <tr key={f.symbol} className="border-t border-line"><td className="py-1 pr-4 font-medium text-ink">{f.symbol}{f.hedged ? " (hedged)" : ""}</td>
                      <td className="pr-4 text-ink2">{f.currency}</td><td className="pr-4">{pct(f.expense_ratio, 2)}</td>
                      <td className={`pr-4 ${f.tracking_difference >= -0.001 ? "text-up" : "text-down"}`}>{pct(f.tracking_difference, 2, true)}</td>
                      <td className="pr-4 text-ink2">{pct(f.residual, 2, true)}</td><td className="pr-4">{isNum(f.score) ? f.score.toFixed(0) : "—"}</td></tr>))}</tbody>
                </table>
                <p className="mt-2 text-xs text-muted">{funds.note}</p>
              </div>) : <Note>{funds?.error ?? "Fund data not available yet"}</Note>}
          </section>
        </>
      )}
    </div>
  );
}
