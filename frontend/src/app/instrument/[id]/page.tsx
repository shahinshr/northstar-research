"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink, RefreshCw, Star } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useState } from "react";
import { FinBars } from "@/components/charts";
import Dcf from "@/components/Dcf";
import { JobPill, useJobTracker } from "@/components/jobs";
import { MetricGrid, Notes, PriceRisk, ScoreCard, Sources } from "@/components/InstrumentParts";
import FundDetail from "@/components/FundDetail";
import NewsStream from "@/components/NewsStream";
import { Confidence, Empty, Flags, LivePrice, MetricValue, Note, Skeleton } from "@/components/ui";
import { api, type Analysis, type Period } from "@/lib/api";
import { ago, compact, dateFmt, isNum, num, pct } from "@/lib/format";
import { METRICS } from "@/lib/metrics";

const TABS = ["Overview", "Fundamentals", "Valuation", "Analysts", "Price & risk", "Sources", "Notes"] as const;
type Tab = (typeof TABS)[number];

export default function InstrumentPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const { job, track, busy } = useJobTracker();
  const q = useQuery({ queryKey: ["analysis", id], queryFn: () => api.analysis(id), refetchInterval: 60_000, refetchOnWindowFocus: true });
  const live = useQuery({ queryKey: ["live", id], queryFn: () => api.live(id), refetchInterval: (x) => (x.state.data?.refreshing ? 10_000 : 30_000) });
  const sp = useSearchParams();
  const slug = (t: string) => t.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const [tab, setTab] = useState<Tab>(() => TABS.find((t) => slug(t) === sp.get("tab")) ?? "Overview");
  const watch = useMutation({ mutationFn: (on: boolean) => api.watch(id, on), onSuccess: () => qc.invalidateQueries({ queryKey: ["analysis", id] }) });

  if (q.isLoading) return <div className="space-y-4"><Skeleton className="h-24" /><Skeleton className="h-96" /></div>;
  if (q.isError || !q.data) return <Empty title="Not in the latest snapshot">{(q.error as Error)?.message} — it may still be loading; run a refresh from the dashboard.</Empty>;
  const a = q.data;
  const refresh = (kind: "refresh_prices" | "refresh_analysis") =>
    api.refresh({ scope: "selection", instrument_ids: [id], kind, mode: "force" }).then(track).catch((e: Error) => alert(e.message));

  if (a.asset_class === "fund") return <FundDetail a={a} job={job} track={track} busy={busy} live={live.data ?? null} />;
  const lq = live.data?.quote;
  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
    <div className="min-w-0 space-y-5">
      <Link href="/" className="inline-flex items-center gap-1 text-sm text-ink2 hover:text-ink"><ArrowLeft size={14} /> Dashboard</Link>

      {/* ------------------------------------------------ header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight text-ink">{a.name}</h1>
            <button onClick={() => watch.mutate(!a.watch)} aria-label={a.watch ? "Remove from watchlist" : "Add to watchlist"}>
              <Star size={18} className={a.watch ? "fill-[var(--warn)] text-[var(--warn)]" : "text-muted hover:text-ink"} />
            </button>
          </div>
          <p className="mt-0.5 text-sm text-ink2">
            <b className="text-ink">{a.symbol}</b> · {a.exchange} · {a.sector} / {a.industry} · {a.country}
            {a.financial_currency && a.financial_currency !== a.currency ? <> · reports in {a.financial_currency}</> : null}
          </p>
        </div>
        <div className="text-right">
          <LivePrice official={a.price.value} currency={a.currency} live={lq} size="lg" />
          <div className="text-sm">
            {lq?.provisional && isNum(lq.change) ? <span className={lq.change >= 0 ? "text-up" : "text-down"}>{pct(lq.change, 2, true)} today</span>
              : isNum(a.price.change_1d) ? <span className={a.price.change_1d >= 0 ? "text-up" : "text-down"}>{pct(a.price.change_1d, 2, true)}</span> : null}
            <span className="text-muted"> · close {dateFmt(a.price.date)} {num(a.price.value)} · mkt cap {compact(a.market_cap.value, a.currency)}</span>
          </div>
          <div className="text-2xs text-muted">{lq?.provisional ? `${lq.note}${lq.fetched_at ? ` · quote ${ago(lq.fetched_at)}` : ""}` : a.price.note}</div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn" disabled={busy} onClick={() => refresh("refresh_prices")}><RefreshCw size={14} /> Refresh price</button>
        <button className="btn" disabled={busy} onClick={() => refresh("refresh_analysis")}><RefreshCw size={14} /> Refresh analysis</button>
        <JobPill job={job} />
        <span className="ml-auto text-xs text-muted">Snapshot {dateFmt(a.run.created_at, true)} ({ago(a.run.created_at)})</span>
      </div>

      {/* ------------------------------------------------ tabs */}
      <div className="seg max-w-full overflow-x-auto" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className="whitespace-nowrap">{t}</button>
        ))}
      </div>

      {tab === "Overview" && <Overview a={a} />}
      {tab === "Fundamentals" && <Fundamentals id={id} a={a} />}
      {tab === "Valuation" && <Dcf a={a} />}
      {tab === "Analysts" && <Analysts a={a} />}
      {tab === "Price & risk" && <PriceRisk id={id} a={a} />}
      {tab === "Sources" && <Sources a={a} />}
      {tab === "Notes" && <Notes id={id} />}
    </div>
    <aside className="xl:sticky xl:top-20 xl:self-start">
      <NewsStream title="Breaking news" items={live.data?.news ?? []} loading={live.isLoading} social={live.data?.social} maxHeight={620}
        subtitle={live.data?.news_source ? <>Source {live.data.news_source.provider} · fetched {ago(live.data.news_source.fetched_at)}
          {live.data.refreshing ? " · refreshing…" : ""} · severity is a keyword screen, not advice</> : "Fetching…"} />
    </aside>
    </div>
  );
}

/* ================================================================== Overview */
function Overview({ a }: { a: Analysis }) {
  return (
    <div className="space-y-4">
      {!a.eligibility.core ? <Note tone="warn"><b>No composite score:</b> {a.eligibility.reasons.join(" · ")}</Note> : null}
      <div className="grid gap-4 lg:grid-cols-[1.3fr_1fr]">
        <ScoreCard title="Core score v1" s={a.scores.core} explain />
        <div className="space-y-4">
          <ScoreCard title="Estimate-enhanced v1 (provisional)" s={a.scores.estimate} />
          <div className="card p-4">
            <h3 className="text-sm font-semibold text-ink">Attention</h3>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="text-2xl font-semibold text-ink tnum">{isNum(a.scores.attention?.score) ? a.scores.attention!.score!.toFixed(0) : "—"}</span>
              <span className="text-xs text-muted">{a.scores.attention?.model} · market interest, not evidence of value</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 text-xs text-ink2">
              {(a.scores.attention?.inputs ?? []).map((i) => <span key={i.metric}>{METRICS[i.metric]?.label}: <b className="tnum">{i.percentile.toFixed(0)}</b></span>)}
            </div>
          </div>
          <div className="card p-4">
            <div className="flex items-center justify-between"><h3 className="text-sm font-semibold text-ink">Data confidence</h3><Confidence c={a.confidence} /></div>
            <p className="mt-1 text-xs text-muted">{a.confidence.valid_inputs}/{a.confidence.total_inputs} key inputs valid · prices {a.confidence.freshness.price_sessions_old} session(s) old ·
              latest statement {a.confidence.freshness.latest_statement ?? "—"} ({a.confidence.freshness.reporting_cadence})
              {a.confidence.unofficial_source ? " · includes unofficial source (Yahoo)" : ""}</p>
            {a.flags.length ? <div className="mt-3"><Flags flags={a.flags} /></div> : null}
          </div>
        </div>
      </div>
      <MetricGrid a={a} groups={["Valuation", "Quality", "Growth", "Resilience", "Performance", "Risk"]} />
      {a.description ? (
        <div className="card p-4"><h3 className="label mb-1">Business</h3><p className="text-sm leading-relaxed text-ink2">{a.description}</p>
          {a.website ? <a href={a.website} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs text-accent-ink hover:underline">{a.website} <ExternalLink size={11} /></a> : null}
        </div>) : null}
    </div>
  );
}

/* ================================================================== Fundamentals */
const ROWS: [string, string][] = [
  ["revenue", "Revenue"], ["gross_profit", "Gross profit"], ["operating_income", "Operating income"], ["ebitda", "EBITDA"],
  ["net_income", "Net income"], ["eps_diluted", "Diluted EPS"], ["diluted_shares", "Diluted shares"], ["operating_cash_flow", "Operating cash flow"],
  ["capex", "Capex"], ["sbc", "Stock-based comp."], ["dividends_paid", "Dividends paid"], ["total_debt", "Total debt"], ["cash_and_st_investments", "Cash & ST inv."],
  ["total_equity", "Shareholders' equity"], ["total_assets", "Total assets"],
];

function Fundamentals({ id, a }: { id: string; a: Analysis }) {
  const q = useQuery({ queryKey: ["fin", id], queryFn: () => api.financials(id) });
  const [mode, setMode] = useState<"annual" | "quarterly">("annual");
  if (q.isLoading) return <Skeleton className="h-80" />;
  if (!q.data) return <Empty title="No financial statements">No provider in the fallback chain returned statements.</Empty>;
  const f = q.data;
  const periods: Period[] = (mode === "annual" ? f.annual : f.quarterly).slice(0, mode === "annual" ? 5 : 8);
  const chrono = [...f.annual].slice(0, 5).reverse();
  const lab = (p: Period) => (p.fiscal_period === "FY" ? `FY${p.period_end.slice(2, 4)}` : p.period_end.slice(0, 7));
  const series = (k: string) => chrono.map((p) => ({ p: lab(p), v: p.fields[k] ?? null }));
  const fcf = chrono.map((p) => ({ p: lab(p), v: isNum(p.fields.operating_cash_flow) ? p.fields.operating_cash_flow - (p.fields.capex ?? 0) : null }));
  return (
    <div className="space-y-4">
      <div className="card grid gap-4 p-4 md:grid-cols-2 xl:grid-cols-4">
        <FinBars data={series("revenue")} label="Revenue (annual)" currency={f.financial_currency} />
        <FinBars data={series("operating_income")} label="Operating income" currency={f.financial_currency} />
        <FinBars data={series("net_income")} label="Net income" currency={f.financial_currency} />
        <FinBars data={fcf} label="Free cash flow (OCF − capex)" currency={f.financial_currency} />
      </div>
      <div className="card">
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
          {(["annual", "quarterly"] as const).map((m) => (
            <button key={m} className={`chip capitalize ${mode === m ? "chip-on" : ""}`} onClick={() => setMode(m)}>{m}</button>))}
          <span className="ml-auto text-xs text-muted">Source: {f.provider} · {f.route} · fetched {ago(f.fetched_at)} · reporting currency {f.financial_currency}
            {a.financial_currency !== a.currency ? ` (trades in ${a.currency})` : ""}</span>
        </div>
        {periods.length === 0 ? <p className="p-6 text-sm text-ink2">No {mode} periods from this provider{mode === "quarterly" ? " — semi-annual reporters publish H1/H2 only." : "."}</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm tnum">
              <thead><tr className="border-b border-line text-2xs uppercase tracking-wide text-muted">
                <th className="sticky left-0 bg-surface px-3 py-2 text-left font-semibold">{f.financial_currency}</th>
                {periods.map((p) => <th key={p.period_end} className="px-3 py-2 text-right font-semibold">{p.fiscal_period === "FY" ? "FY " : "Q "}{p.period_end}</th>)}</tr></thead>
              <tbody>{ROWS.map(([k, l]) => (
                <tr key={k} className="border-b border-line last:border-0">
                  <td className="sticky left-0 bg-surface px-3 py-1.5 text-ink2">{l}</td>
                  {periods.map((p) => <td key={p.period_end} className="px-3 py-1.5 text-right">
                    {isNum(p.fields[k]) ? (k === "eps_diluted" ? num(p.fields[k]) : compact(p.fields[k])) : <span className="text-muted">N/A</span>}</td>)}
                </tr>))}</tbody>
            </table>
          </div>)}
      </div>
    </div>
  );
}

/* ================================================================== Analysts */
function Analysts({ a }: { a: Analysis }) {
  const t = a.targets;
  const per = a.estimates?.periods ?? [];
  const lo = Math.min(t.low ?? a.price.value, a.price.value), hi = Math.max(t.high ?? a.price.value, a.price.value);
  const pos = (v: number) => `${((v - lo) / (hi - lo || 1)) * 100}%`;
  const label: Record<string, string> = { "0q": "Current quarter", "+1q": "Next quarter", "0y": "Current FY", "+1y": "Next FY" };
  return (
    <div className="space-y-4">
      <div className="card p-4">
        <h3 className="text-sm font-semibold text-ink">Analyst price targets</h3>
        {!t.available ? <p className="mt-2 text-sm text-ink2">No target data from the provider chain.</p> : (
          <>
            <p className="text-xs text-muted">{t.note}. Analysts: {t.analysts ?? "unknown"}. Target dates not supplied by provider.</p>
            <div className="relative mx-2 mb-8 mt-10 h-2 rounded-full bg-grid">
              {isNum(t.low) && isNum(t.high) ? <div className="absolute inset-y-0 rounded-full" style={{ left: pos(t.low), right: `calc(100% - ${pos(t.high)})`, background: "var(--s1)", opacity: 0.35 }} /> : null}
              {([["Low", t.low], ["Median", t.median ?? t.mean], ["High", t.high]] as [string, number | null | undefined][]).filter(([, v]) => isNum(v)).map(([l, v]) => (
                <div key={l} className="absolute -top-7 -translate-x-1/2 text-center" style={{ left: pos(v as number) }}>
                  <div className="text-2xs text-muted">{l}</div><div className="text-xs font-semibold tnum text-ink">{num(v as number)}</div>
                  <div className="mx-auto mt-1 h-4 w-0.5 bg-accent" /></div>))}
              <div className="absolute top-3 -translate-x-1/2 text-center" style={{ left: pos(a.price.value) }}>
                <div className="mx-auto h-3 w-3 -translate-y-4 rotate-45 border-2 border-surface bg-ink" />
                <div className="-mt-2 text-2xs text-ink2">Price {num(a.price.value)}</div></div>
            </div>
            <div className="flex gap-6 text-sm">
              <span>Upside to median: <MetricValue k="target_upside" m={a.metrics.target_upside} /></span>
              {isNum(t.dispersion) ? <span className="text-ink2">Dispersion (high−low)/median: <b className="tnum">{pct(t.dispersion, 0)}</b></span> : null}
            </div>
          </>)}
      </div>
      <div className="card overflow-x-auto p-4">
        <h3 className="text-sm font-semibold text-ink">Consensus estimates</h3>
        <p className="mb-2 text-xs text-muted">{a.estimates?.basis}. EPS trend shows the consensus for the <b>same</b> target period 7/30/60/90 days ago — the basis of the revisions signal.</p>
        {per.length === 0 ? <p className="text-sm text-ink2">No estimates.</p> : (
          <table className="w-full text-sm tnum">
            <thead><tr className="border-b border-line text-2xs uppercase tracking-wide text-muted">
              {["Period", "EPS avg", "Low – High", "# EPS", "Growth", "Revenue avg", "90d ago", "30d ago", "7d ago", "Now", "90d rev."].map((h) =>
                <th key={h} className={`px-2 py-2 font-semibold ${h === "Period" ? "text-left" : "text-right"}`}>{h}</th>)}</tr></thead>
            <tbody>{per.map((p) => {
              const tr = p.eps_trend;
              const rev90 = tr && isNum(tr.current) && isNum(tr.d90) && tr.d90 !== 0 ? (tr.current - tr.d90) / Math.abs(tr.d90) : null;
              return (
                <tr key={p.period} className="border-b border-line last:border-0">
                  <td className="px-2 py-1.5 text-left text-ink2">{label[p.period] ?? p.period}</td>
                  <td className="px-2 text-right">{num(p.eps_avg)}</td>
                  <td className="px-2 text-right text-ink2">{num(p.eps_low)} – {num(p.eps_high)}</td>
                  <td className="px-2 text-right text-ink2">{p.eps_analysts ?? "—"}</td>
                  <td className="px-2 text-right">{pct(p.eps_growth, 1, true)}</td>
                  <td className="px-2 text-right">{compact(p.revenue_avg)}</td>
                  <td className="px-2 text-right text-ink2">{num(tr?.d90)}</td><td className="px-2 text-right text-ink2">{num(tr?.d30)}</td>
                  <td className="px-2 text-right text-ink2">{num(tr?.d7)}</td><td className="px-2 text-right">{num(tr?.current)}</td>
                  <td className={`px-2 text-right ${isNum(rev90) ? (rev90 >= 0 ? "text-up" : "text-down") : "text-muted"}`}>{pct(rev90, 1, true)}</td>
                </tr>);
            })}</tbody>
          </table>)}
        <p className="mt-2 text-2xs text-muted">Estimates are assumed to be in the reporting currency ({a.financial_currency}); the provider does not state it, so forward P/E is suppressed if it looks inconsistent with trailing EPS.</p>
      </div>
    </div>
  );
}


