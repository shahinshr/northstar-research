"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { CompareLines, OverlapMatrix, ReturnRiskScatter } from "@/components/charts";
import { Empty, MetricValue, Note, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { compact, isNum, pct } from "@/lib/format";
import { COMPONENT_LABEL, METRICS, SERIES } from "@/lib/metrics";

const GENERAL = ["return_1y", "return_3y_ann", "return_5y_ann", "volatility_1y", "max_drawdown_1y", "max_drawdown_3y", "max_drawdown_5y", "sharpe_1y", "beta_1y",
  "dividend_yield", "distribution_yield", "expense_ratio", "tracking_difference", "aum_usd", "top10_weight", "lookthrough_pe"];
const PEER = ["pe", "forward_pe", "ev_ebitda", "fcf_yield", "roic", "operating_margin", "gross_margin", "revenue_growth", "eps_growth",
  "net_debt_ebitda", "interest_coverage", "cash_conversion", "eps_revision", "target_upside"];

function ComparePicker({ initial }: { initial: string[] }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const found = useQuery({ queryKey: ["search", q], queryFn: () => api.search(q), enabled: q.length >= 1 });
  const [sel, setSel] = useState<string[]>(initial);
  const rows = found.data ?? [];
  return (
    <div className="card p-4">
      <div className="label mb-2">Choose 2–8 instruments</div>
      <input className="input mb-2 w-64" placeholder="Search ticker or name (stocks or funds)" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="mb-2 text-xs text-muted">{sel.length} selected</div>
      <div className="flex flex-wrap gap-1.5">
        {rows.map((r) => {
          const on = sel.includes(r.id);
          return <button key={r.id} className={`chip ${on ? "chip-on" : ""}`} disabled={!on && sel.length >= 8}
            onClick={() => setSel(on ? sel.filter((x) => x !== r.id) : [...sel, r.id])}>{r.symbol}</button>;
        })}
      </div>
      <button className="btn-primary mt-3" disabled={sel.length < 2} onClick={() => router.push(`/compare?ids=${sel.join(",")}`)}>Compare {sel.length}</button>
    </div>
  );
}

function CompareInner() {
  const sp = useSearchParams();
  const ids = (sp.get("ids") ?? "").split(",").filter(Boolean);
  const [currency, setCurrency] = useState<"CAD" | "USD" | "GBP">("CAD");
  const [window, setWindow] = useState<"1y" | "3y" | "5y">("3y");
  const [view, setView] = useState<"general" | "peer">("general");
  const q = useQuery({ queryKey: ["compare", ids, currency, window], queryFn: () => api.compare(ids, currency, window), enabled: ids.length >= 2 });

  if (ids.length < 2) return <div className="space-y-4"><h1 className="text-2xl font-semibold text-ink">Compare</h1><ComparePicker initial={ids} /></div>;
  if (q.isLoading) return <Skeleton className="h-[500px]" />;
  if (q.isError || !q.data) return <Empty title="Comparison failed">{(q.error as Error)?.message}</Empty>;
  const c = q.data;
  const syms = c.items.map((i) => i.symbol);
  const keys = view === "general" ? GENERAL : PEER;
  const mixedTypes = new Set(c.items.map((i) => i.eligibility?.model_family)).size > 1;
  const fundIds = c.items.filter((i) => i.asset_class === "fund").map((i) => i.id);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Compare</h1>
          <p className="text-sm text-ink2">{syms.join(" · ")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="label">Currency</span>
          {(["CAD", "USD", "GBP"] as const).map((x) => <button key={x} className={`chip ${currency === x ? "chip-on" : ""}`} onClick={() => setCurrency(x)}>{x}</button>)}
          <span className="label ml-2">Window</span>
          {(["1y", "3y", "5y"] as const).map((x) => <button key={x} className={`chip ${window === x ? "chip-on" : ""}`} onClick={() => setWindow(x)}>{x}</button>)}
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.6fr_1fr]">
        <section className="card p-4">
          <h2 className="text-sm font-semibold text-ink">Total return in {currency}, rebased to 100</h2>
          <p className="mb-2 text-xs text-muted">{c.dates.start} → {c.dates.end} · {c.dates.observations} common trading days · {c.note}</p>
          <CompareLines series={c.series} symbols={syms} />
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm tnum">
              <thead><tr className="text-2xs uppercase tracking-wide text-muted"><th className="py-1 text-left font-semibold">Period return</th>
                {syms.map((s) => <th key={s} className="px-2 text-right font-semibold">{s}</th>)}</tr></thead>
              <tbody>
                <tr className="border-t border-line"><td className="py-1 text-ink2">In {currency} (incl. FX)</td>
                  {syms.map((s) => { const v = c.period_stats[s]?.return_converted; return <td key={s} className={`px-2 text-right ${isNum(v) ? (v >= 0 ? "text-up" : "text-down") : "text-muted"}`}>{pct(v, 1, true)}</td>; })}</tr>
                <tr className="border-t border-line"><td className="py-1 text-ink2">In local currency</td>
                  {syms.map((s) => { const v = c.period_stats[s]?.return_local; return <td key={s} className="px-2 text-right text-ink2">{pct(v, 1, true)}</td>; })}</tr>
              </tbody>
            </table>
          </div>
        </section>
        <section className="card p-4">
          <h2 className="text-sm font-semibold text-ink">Return vs risk (1y, local currency)</h2>
          <p className="mb-2 text-xs text-muted">Each instrument in its own trading currency</p>
          <ReturnRiskScatter points={c.items.map((it, i) => ({ sym: it.symbol, i, ret: it.metrics.return_1y?.value as number, vol: it.metrics.volatility_1y?.value as number }))
            .filter((p) => isNum(p.ret) && isNum(p.vol))} />
        </section>
      </div>

      <section className="card">
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
          <button className={`chip ${view === "general" ? "chip-on" : ""}`} onClick={() => setView("general")}>General investment view</button>
          <button className={`chip ${view === "peer" ? "chip-on" : ""}`} onClick={() => setView("peer")}>Peer analysis view</button>
          <span className="ml-auto text-xs text-muted">N/A = inapplicable or missing (never coloured as bad) · hover for basis</span>
        </div>
        {view === "peer" && mixedTypes ? <div className="p-3"><Note>Mixed model families (e.g. a bank vs an operating company): operating metrics are N/A for financials by design.</Note></div> : null}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-line">
              <th className="sticky left-0 bg-surface px-3 py-2 text-left text-2xs font-semibold uppercase tracking-wide text-muted">Metric</th>
              {c.items.map((it, i) => (
                <th key={it.id} className="px-3 py-2 text-right">
                  <Link href={`/instrument/${it.id}`} className="inline-flex items-center gap-1.5 font-semibold text-ink hover:underline">
                    <span className="h-2 w-2 rounded-full" style={{ background: SERIES[i % 8] }} aria-hidden />{it.symbol}</Link>
                  <div className="text-2xs font-normal text-muted">{it.region} · {it.currency}</div>
                </th>))}
            </tr></thead>
            <tbody>
              <tr className="border-b border-line"><td className="sticky left-0 bg-surface px-3 py-1.5 text-ink2" title="Stocks: core_v1. Funds: rank model within their cohort.">Score (own model)</td>
                {c.items.map((it) => <td key={it.id} className="px-3 text-right tnum font-semibold">{isNum(it.core.score) ? it.core.score.toFixed(1) : <span className="font-normal text-muted" title={it.core.reasons?.join("; ")}>N/A</span>}</td>)}</tr>
              {view === "peer" ? Object.keys(COMPONENT_LABEL).filter((k) => k !== "revisions").map((k) => (
                <tr key={k} className="border-b border-line"><td className="sticky left-0 bg-surface px-3 py-1.5 text-ink2">↳ {COMPONENT_LABEL[k]}</td>
                  {c.items.map((it) => <td key={it.id} className="px-3 text-right tnum text-ink2">{isNum(it.components?.[k]) ? (it.components[k] as number).toFixed(0) : <span className="text-muted">N/A</span>}</td>)}</tr>)) : null}
              <tr className="border-b border-line"><td className="sticky left-0 bg-surface px-3 py-1.5 text-ink2">Market cap ({c.items.find((i) => i.market_cap)?.market_cap?.presentation_currency ?? "CAD"})</td>
                {c.items.map((it) => <td key={it.id} className="px-3 text-right tnum text-ink2">{it.market_cap ? compact(it.market_cap.presentation) : <span className="text-muted" title="Funds: see Assets">N/A</span>}</td>)}</tr>
              {keys.map((k) => (
                <tr key={k} className="border-b border-line last:border-0">
                  <td className="sticky left-0 bg-surface px-3 py-1.5 text-ink2" title={METRICS[k]?.help}>{METRICS[k]?.label ?? k}</td>
                  {c.items.map((it) => <td key={it.id} className="px-3 text-right"><MetricValue k={k} m={it.metrics[k]} /></td>)}
                </tr>))}
            </tbody>
          </table>
        </div>
      </section>
      {fundIds.length >= 2 ? <OverlapSection ids={fundIds} /> : null}
    </div>
  );
}

function OverlapSection({ ids }: { ids: string[] }) {
  const q = useQuery({ queryKey: ["overlap", ids], queryFn: () => api.overlap(ids) });
  if (!q.data) return <Skeleton className="h-40" />;
  return (
    <section className="card p-4">
      <h2 className="text-sm font-semibold text-ink">Holdings overlap</h2>
      <p className="mb-3 text-xs text-muted">{q.data.note} Complete holdings: {Object.entries(q.data.complete).map(([k, v]) => `${k} ${v ? "✓" : "top-10"}`).join(" · ")}</p>
      <OverlapMatrix symbols={q.data.symbols} cells={q.data.cells} />
    </section>
  );
}

export default function ComparePage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><CompareInner /></Suspense>;
}
