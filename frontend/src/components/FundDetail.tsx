"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, RefreshCw, Star } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { CompareLines, GapChart } from "@/components/charts";
import { MetricGrid, Notes, PriceRisk, ScoreCard, Sources } from "@/components/InstrumentParts";
import { JobPill } from "@/components/jobs";
import { Confidence, Flags, MetricValue, Note, Skeleton, Stat } from "@/components/ui";
import { api, type Analysis, type Job } from "@/lib/api";
import { ago, compact, dateFmt, isNum, num, pct } from "@/lib/format";

const TABS = ["Overview", "Costs & structure", "Holdings & exposure", "Tracking", "Price & risk", "Sources", "Notes"] as const;
type Tab = (typeof TABS)[number];

function Bars({ data, label }: { data: Record<string, number>; label: string }) {
  const rows = Object.entries(data).filter(([, v]) => v > 0.0005).sort((a, b) => b[1] - a[1]);
  if (!rows.length) return <p className="text-sm text-muted">{label}: not reported.</p>;
  const max = rows[0][1];
  return (
    <div className="space-y-1.5">
      {rows.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[150px_1fr_56px] items-center gap-3 text-sm" title={`${k}: ${pct(v)}`}>
          <span className="truncate text-ink2">{k.replace(/_/g, " ").replace(/Position$/, "")}</span>
          <span className="h-2 rounded-full bg-grid"><span className="block h-2 rounded-full bg-accent" style={{ width: `${(v / max) * 100}%` }} /></span>
          <span className="tnum text-right text-ink">{pct(v)}</span>
        </div>))}
    </div>
  );
}

export default function FundDetail({ a, job, track, busy }: { a: Analysis; job: Job | null; track: (j: Job) => void; busy: boolean }) {
  const qc = useQueryClient();
  const sp = useSearchParams();
  const slug = (t: string) => t.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const [tab, setTab] = useState<Tab>(() => TABS.find((t) => slug(t) === sp.get("tab")) ?? "Overview");
  const watch = useMutation({ mutationFn: (on: boolean) => api.watch(a.id, on), onSuccess: () => qc.invalidateQueries({ queryKey: ["analysis", a.id] }) });
  const refresh = (kind: "refresh_prices" | "refresh_analysis") =>
    api.refresh({ scope: "selection", instrument_ids: [a.id], kind, mode: "force" }).then(track).catch((e: Error) => alert(e.message));
  const m = a.metrics;
  const core = a.scores.core;
  const isBond = a.fund_asset_class === "fixed_income";

  return (
    <div className="space-y-5">
      <Link href="/" className="inline-flex items-center gap-1 text-sm text-ink2 hover:text-ink"><ArrowLeft size={14} /> Dashboard</Link>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight text-ink">{a.name}</h1>
            <button onClick={() => watch.mutate(!a.watch)} aria-label={a.watch ? "Remove from watchlist" : "Add to watchlist"}>
              <Star size={18} className={a.watch ? "fill-[var(--warn)] text-[var(--warn)]" : "text-muted hover:text-ink"} />
            </button>
          </div>
          <p className="mt-0.5 text-sm text-ink2">
            <b className="text-ink">{a.symbol}</b> · {a.asset_type === "mutualfund" ? "Mutual fund" : "ETF"} · {a.family ?? "—"} ·
            cohort <b className="text-ink">{a.cohort}</b>{a.hedged ? " · currency-hedged" : ""}{a.style === "active" ? " · actively managed" : ""}
          </p>
        </div>
        <div className="text-right">
          <div className="text-3xl font-semibold text-ink tnum">{num(a.price.value)} <span className="text-base font-normal text-muted">{a.currency}</span></div>
          <div className="text-sm">
            {isNum(a.price.change_1d) ? <span className={a.price.change_1d >= 0 ? "text-up" : "text-down"}>{pct(a.price.change_1d, 2, true)}</span> : null}
            <span className="text-muted"> · close {dateFmt(a.price.date)}</span>
          </div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn" disabled={busy} onClick={() => refresh("refresh_prices")}><RefreshCw size={14} /> Refresh price</button>
        <button className="btn" disabled={busy} onClick={() => refresh("refresh_analysis")}><RefreshCw size={14} /> Refresh fund data</button>
        <JobPill job={job} />
        <span className="ml-auto text-xs text-muted">Snapshot {dateFmt(a.run.created_at, true)} ({ago(a.run.created_at)})</span>
      </div>
      <div className="flex gap-1 overflow-x-auto border-b border-line" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm ${tab === t ? "border-accent font-medium text-ink" : "border-transparent text-ink2 hover:text-ink"}`}>{t}</button>))}
      </div>

      {tab === "Overview" && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Stat label="Cost (expense ratio)" value={<MetricValue k="expense_ratio" m={m.expense_ratio} />}
              sub={m.cost_vs_category?.status === "valid" ? `${pct(m.cost_vs_category.value, 2, true)} vs category average` : "as published"} />
            <Stat label="Tracking difference" value={<MetricValue k="tracking_difference" m={m.tracking_difference} />} sub="3y annualised vs index" />
            <Stat label="Assets" value={compact(m.aum_usd?.value, "USD")} sub={isNum(m.age_years?.value) ? `${m.age_years.value!.toFixed(0)} years old` : undefined} />
            <Stat label="Top-10 weight" value={<MetricValue k="top10_weight" m={m.top10_weight} />} sub={a.holdings?.complete ? `${a.holdings.count} holdings` : "top-10 list only"} />
            <Stat label="3y return (ann.)" value={<MetricValue k="return_3y_ann" m={m.return_3y_ann} />} sub={`max drawdown ${m.max_drawdown_3y?.status === "valid" ? pct(m.max_drawdown_3y.value) : "N/A"}`} />
            <Stat label={isBond ? "Duration" : "Distribution yield"} value={isBond ? <span className="tnum">{m.duration?.status === "valid" ? `${num(m.duration.value, 1)} yrs` : "N/A"}</span>
              : <MetricValue k="distribution_yield" m={m.distribution_yield} />} sub={isBond ? `yield ${m.distribution_yield?.status === "valid" ? pct(m.distribution_yield.value) : "N/A"}` : undefined} />
          </div>
          {!a.eligibility.core ? <Note tone="warn"><b>No composite score:</b> {a.eligibility.reasons.join(" · ")}</Note> : null}
          <div className="grid gap-4 lg:grid-cols-[1.3fr_1fr]">
            <ScoreCard title={isNum(core?.score) ? `${core?.label ?? "Fund score"} — rank ${core?.rank} of ${(core as { rank_of?: number })?.rank_of ?? "—"} in cohort` : "Fund score"} s={core} explain />
            <div className="card p-4">
              <div className="flex items-center justify-between"><h3 className="text-sm font-semibold text-ink">Data confidence</h3><Confidence c={a.confidence} /></div>
              <p className="mt-1 text-xs text-muted">{a.confidence.valid_inputs}/{a.confidence.total_inputs} key inputs valid · holdings as of {a.holdings?.as_of ?? "unknown"}
                {a.holdings?.complete ? " (complete, from issuer)" : " (top-10 only)"} · includes unofficial source (Yahoo)</p>
              {a.flags.length ? <div className="mt-3"><Flags flags={a.flags} /></div> : null}
              <h3 className="mt-4 text-sm font-semibold text-ink">How to read this</h3>
              <ul className="mt-1 space-y-1 text-xs text-ink2">
                <li>Funds are compared only with funds in the same cohort ({a.cohort}).</li>
                <li>Cost leads the model: across categories the cheapest funds have historically been far more likely to survive and beat peers.</li>
                <li>Tracking difference already includes fees and foreign withholding tax — it is what an investor actually lost vs the index.</li>
                <li>Past returns are shown for context and risk, not used to rank passive funds.</li>
              </ul>
            </div>
          </div>
          <MetricGrid a={a} groups={isBond ? ["Cost", "Bonds", "Scale", "Performance", "Risk"] : ["Cost", "Tracking", "Diversification", "Scale", "Valuation", "Performance", "Risk"]} />
        </div>
      )}
      {tab === "Costs & structure" && (
        <div className="space-y-4">
          <MetricGrid a={a} groups={["Cost", "Scale", "Tracking"]} />
          <Note>Expense ratio is taken as published (net expense ratio / MER / OCF) — label: {m.expense_ratio?.basis ?? "n/a"}. Trading costs (turnover), bid–ask spread and
            premium/discount to NAV are not included in it; spread and NAV premium need a licensed quote feed and are shown as unavailable rather than assumed zero.</Note>
        </div>
      )}
      {tab === "Holdings & exposure" && <Holdings a={a} />}
      {tab === "Tracking" && <TrackingTab a={a} />}
      {tab === "Price & risk" && <PriceRisk id={a.id} a={a} />}
      {tab === "Sources" && <Sources a={a} />}
      {tab === "Notes" && <Notes id={a.id} />}
    </div>
  );
}

function Holdings({ a }: { a: Analysis }) {
  const q = useQuery({ queryKey: ["holdings", a.id], queryFn: () => api.holdings(a.id) });
  const [filter, setFilter] = useState("");
  const ex = a.exposure;
  const rows = (q.data?.holdings ?? []).slice().sort((x, y) => y.weight - x.weight)
    .filter((h) => !filter || `${h.symbol} ${h.name}`.toLowerCase().includes(filter.toLowerCase()));
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="card p-4"><h3 className="label mb-2">Sector exposure</h3><Bars data={ex?.sectors ?? {}} label="Sectors" /></div>
        <div className="card p-4"><h3 className="label mb-2">Asset mix</h3><Bars data={ex?.asset_classes ?? {}} label="Asset mix" /></div>
        <div className="card p-4">
          <h3 className="label mb-2">{a.fund_asset_class === "fixed_income" ? "Credit quality" : "Look-through valuation"}</h3>
          {a.fund_asset_class === "fixed_income" ? <Bars data={ex?.bond?.ratings ?? {}} label="Ratings" /> : (
            <dl className="space-y-1.5 text-sm">
              {["lookthrough_earnings_yield", "lookthrough_pe", "lookthrough_coverage", "vendor_pe"].map((k) => (
                <div key={k} className="flex justify-between gap-3"><dt className="text-ink2">{({ lookthrough_earnings_yield: "Earnings yield (ours)",
                  lookthrough_pe: "Implied P/E (ours)", lookthrough_coverage: "Weight mapped", vendor_pe: "Vendor aggregate P/E" } as Record<string, string>)[k]}</dt>
                  <dd><MetricValue k={k} m={a.metrics[k]} /></dd></div>))}
              <p className="pt-1 text-2xs text-muted">Ours = Σ weight × earnings yield of holdings we analyse (never an average of P/Es). Needs complete holdings and ≥ 80% mapped weight.</p>
            </dl>)}
        </div>
      </div>
      <div className="card">
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
          <h3 className="text-sm font-semibold text-ink">Holdings</h3>
          <span className="text-xs text-muted">{q.data ? `${q.data.complete ? "Complete list" : "Top-10 only"} · ${q.data.holdings.length} rows · as of ${q.data.as_of ?? "unknown"} · ${q.data.provider} (${q.data.route})` : ""}</span>
          <input className="input ml-auto w-48" placeholder="Filter holdings" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        {q.isLoading ? <Skeleton className="m-3 h-40" /> : !q.data ? <p className="p-4 text-sm text-ink2">No holdings data from any provider.</p> : (
          <div className="max-h-[520px] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-surface"><tr className="border-b border-line text-left text-2xs uppercase tracking-wide text-muted">
                <th className="px-3 py-2 font-semibold">Symbol</th><th className="px-3 font-semibold">Name</th><th className="px-3 font-semibold">Sector</th>
                <th className="px-3 text-right font-semibold">Weight</th></tr></thead>
              <tbody>{rows.slice(0, 600).map((h, i) => (
                <tr key={`${h.symbol}-${i}`} className="border-b border-line last:border-0">
                  <td className="px-3 py-1.5 font-medium text-ink">{h.symbol}</td><td className="px-3 text-ink2">{h.name}</td>
                  <td className="px-3 text-xs text-muted">{h.sector ?? "—"}</td><td className="tnum px-3 text-right">{pct(h.weight, 2)}</td></tr>))}</tbody>
            </table>
          </div>)}
      </div>
    </div>
  );
}

function TrackingTab({ a }: { a: Analysis }) {
  const [years, setYears] = useState(3);
  const q = useQuery({ queryKey: ["tracking", a.id, years], queryFn: () => api.tracking(a.id, years) });
  const t = q.data;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Tracking difference (ann.)" value={<MetricValue k="tracking_difference" m={a.metrics.tracking_difference} />} />
        <Stat label="Tracking error (ann.)" value={<MetricValue k="tracking_error" m={a.metrics.tracking_error} />} />
        <Stat label="Expense ratio" value={<MetricValue k="expense_ratio" m={a.metrics.expense_ratio} />} />
        <Stat label="Unexplained by fees" value={isNum(a.metrics.tracking_difference?.value) && isNum(a.metrics.expense_ratio?.value)
          ? pct((a.metrics.tracking_difference.value as number) + (a.metrics.expense_ratio.value as number), 2, true) : "N/A"}
          sub="≈ withholding tax, securities lending, cash drag" />
      </div>
      {q.isLoading ? <Skeleton className="h-72" /> : !t?.available ? <Note>{t?.reason ?? "Tracking unavailable"} — the basic fund screen is used instead of the full passive model (spec §7).</Note> : (
        <div className="card p-4">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-ink">{a.symbol} vs {t.index} total return, rebased to 100 ({t.currency})</h3>
            <div className="ml-auto flex gap-1">{[1, 3, 5].map((y) => <button key={y} className={`chip ${years === y ? "chip-on" : ""}`} onClick={() => setYears(y)}>{y}y</button>)}</div>
          </div>
          <p className="mb-2 text-xs text-muted">{t.hedged ? "Hedged fund compared in the index currency; the residual includes hedging cost/carry." :
            "Index converted to the fund's trading currency at reference FX. Weekly points."}</p>
          <CompareLines series={(t.series ?? []).map((p) => ({ d: p.d, [a.symbol]: p.fund, [t.index!]: p.index }))} symbols={[a.symbol, t.index!]} />
          <h4 className="mt-4 text-xs font-semibold text-ink2">Cumulative gap (fund − index, index points)</h4>
          <GapChart data={(t.series ?? []).map((p) => ({ d: p.d, v: p.gap }))} />
        </div>
      )}
    </div>
  );
}
