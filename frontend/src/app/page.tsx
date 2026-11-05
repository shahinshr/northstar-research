"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Calculator, Download, GitCompareArrows, Play, Plus, RefreshCw, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ValueQualityScatter } from "@/components/charts";
import { ColumnPicker, DataTable, usePersisted, type Col } from "@/components/DataTable";
import { JobPill, useJobTracker } from "@/components/jobs";
import { Confidence, Empty, MetricValue, Note, RegionDot, ScoreBar, Skeleton, Stat } from "@/components/ui";
import { api, type Dashboard, type Row } from "@/lib/api";
import { ago, compact, dateFmt, isNum, num, pct } from "@/lib/format";

const v = (r: Row, k: string) => (r.metrics[k]?.status === "valid" ? r.metrics[k].value : null);
const c = (r: Row, k: string) => r.components?.[k] ?? null;

/* ============================================================ stock presets (editable analytical rules, spec §3) */
const PRESETS: { id: string; label: string; rule: string; test: (r: Row) => boolean }[] = [
  { id: "all", label: "All", rule: "Every analysed stock", test: () => true },
  { id: "candidates", label: "Discovery candidates", rule: "On at least one discovery list (composite, value, quality, growth, revisions, attention)",
    test: (r) => (r.discovery?.length ?? 0) > 0 },
  { id: "qarp", label: "Quality at a reasonable price", rule: "Quality ≥ 60 and Value ≥ 50",
    test: (r) => (c(r, "quality") ?? 0) >= 60 && (c(r, "value") ?? 0) >= 50 },
  { id: "growth", label: "Profitable growth", rule: "Growth ≥ 60, Quality ≥ 50, operating margin > 0",
    test: (r) => (c(r, "growth") ?? 0) >= 60 && (c(r, "quality") ?? 0) >= 50 && (v(r, "operating_margin") ?? -1) > 0 },
  { id: "improving", label: "Improving fundamentals", rule: "EPS revisions > 0 and Growth ≥ 50",
    test: (r) => (v(r, "eps_revision") ?? -1) > 0 && (c(r, "growth") ?? 0) >= 50 },
  { id: "dividend", label: "Dividend sustainability", rule: "Yield ≥ 2%, dividends < 75% of FCF, Resilience ≥ 50",
    test: (r) => (v(r, "dividend_yield") ?? 0) >= 0.02 && (v(r, "payout_fcf") ?? 9) < 0.75 && (c(r, "resilience") ?? 0) >= 50 },
  { id: "watch", label: "Watchlist", rule: "Starred instruments", test: (r) => r.watch },
  { id: "excluded", label: "Not scored", rule: "Excluded from the composite (financials, loss-makers, insufficient data)",
    test: (r) => r.core.score === null },
];

const mcol = (id: string, label: string, def = false, title?: string): Col => ({
  id, label, def, align: "right", title, sort: (r) => v(r, id), cell: (r) => <MetricValue k={id} m={r.metrics[id]} />,
});
const priceCol: Col = { id: "price", label: "Price", def: true, align: "right", sort: (r) => r.price?.value ?? null,
  cell: (r) => r.price ? (
    <span className="tnum" title={`${r.price.note ?? ""} · ${dateFmt(r.price.date)}`}>
      {num(r.price.value)} <span className="text-2xs text-muted">{r.currency}</span>
      {isNum(r.price.change_1d) ? <span className={`ml-1 text-2xs ${r.price.change_1d >= 0 ? "text-up" : "text-down"}`}>{pct(r.price.change_1d, 1, true)}</span> : null}
    </span>) : "—" };
const confCol: Col = { id: "conf", label: "Data", def: true, title: "Data confidence: input completeness and freshness — not confidence of profit",
  sort: (r) => r.confidence?.score ?? null, cell: (r) => <Confidence c={r.confidence} /> };

const STOCK_COLS: Col[] = [
  { id: "region", label: "Mkt", def: true, sort: (r) => r.region, cell: (r) => <RegionDot region={r.region} /> },
  { id: "sector", label: "Sector", def: false, sort: (r) => r.sector, cell: (r) => <span className="text-xs text-ink2">{r.sector}</span> },
  priceCol,
  { id: "mcap", label: "Mkt cap", def: true, align: "right", title: "In presentation currency at reference FX", sort: (r) => r.market_cap?.presentation ?? null,
    cell: (r) => <span className="tnum text-ink2">{compact(r.market_cap?.presentation, r.market_cap?.presentation_currency)}</span> },
  { id: "core", label: "Core score", def: true, title: "Peer-relative research rank 0–100 (core_v1). Not an expected return.",
    sort: (r) => r.core.score, cell: (r) => <ScoreBar score={r.core.score} reason={r.core.reasons?.join("; ")} label="Core score" /> },
  { id: "why", label: "Why on list", def: true, title: "Discovery lists this stock is on (spec §4: union of lists)",
    sort: (r) => r.discovery?.length ?? 0,
    cell: (r) => <span className="flex max-w-[220px] flex-wrap gap-1">{(r.discovery ?? []).slice(0, 3).map((d) => <span key={d} className="chip py-0 text-2xs">{d}</span>)}</span> },
  { id: "estimate", label: "Est.-enh.", def: false, align: "right", title: "Separate model incl. EPS revisions (provisional)",
    sort: (r) => r.estimate.score, cell: (r) => <span className="tnum">{isNum(r.estimate.score) ? r.estimate.score.toFixed(1) : "—"}</span> },
  { id: "attention", label: "Attention", def: true, align: "right", title: "Market attention — NOT evidence of value",
    sort: (r) => r.attention.score, cell: (r) => <span className="tnum text-ink2" title={r.attention.model}>{isNum(r.attention.score) ? r.attention.score.toFixed(0) : "—"}</span> },
  ...(["value", "quality", "growth", "resilience", "momentum"] as const).map((k): Col => ({
    id: `c_${k}`, label: k[0].toUpperCase() + k.slice(1, 4), title: `${k} component percentile`, def: true, align: "right",
    sort: (r) => r.components?.[k] ?? null,
    cell: (r) => <span className="tnum text-ink2">{isNum(r.components?.[k]) ? (r.components[k] as number).toFixed(0) : "—"}</span>,
  })),
  mcol("pe", "P/E", true), mcol("forward_pe", "Fwd P/E"), mcol("ev_ebitda", "EV/EBITDA"), mcol("fcf_yield", "FCF yld", true),
  mcol("roic", "ROIC", true), mcol("operating_margin", "Op margin"), mcol("revenue_growth", "Rev gr.", true), mcol("eps_growth", "EPS gr."),
  mcol("net_debt_ebitda", "ND/EBITDA"), mcol("dividend_yield", "Div yld"), mcol("return_1y", "1y TR", true), mcol("return_ytd", "YTD"),
  mcol("volatility_1y", "Vol 1y"), mcol("max_drawdown_1y", "Max DD"), mcol("sharpe_1y", "Sharpe"), mcol("target_upside", "Tgt upside"),
  mcol("eps_revision", "EPS rev."), confCol,
];

const FUND_COLS: Col[] = [
  { id: "type", label: "Type", def: true, sort: (r) => r.asset_type,
    cell: (r) => <span className="text-xs text-ink2">{r.asset_type === "mutualfund" ? "Mutual fund" : "ETF"}{r.style === "active" ? " · active" : ""}{r.hedged ? " · hedged" : ""}</span> },
  { id: "region", label: "Listing", def: true, sort: (r) => r.region, cell: (r) => <RegionDot region={r.region} /> },
  priceCol,
  { id: "fscore", label: "Fund score", def: true, title: "Rank within cohort — passive model (cost, tracking, diversification, liquidity) or basic screen",
    sort: (r) => r.core.score, cell: (r) => (
      <span className="inline-flex items-center gap-2">
        <ScoreBar score={r.core.score} reason={r.core.reasons?.join("; ")} width={56} label={r.core.label ?? "Fund score"} />
        {r.core.rank === 1 && isNum(r.core.score) ? <span className="chip py-0 text-2xs text-up" title="Best in cohort by this model">best in cohort</span> : null}
      </span>) },
  mcol("expense_ratio", "Cost", true), mcol("tracking_difference", "Track. diff", true), mcol("tracking_error", "Track. err"),
  { id: "aum", label: "Assets", def: true, align: "right", sort: (r) => v(r, "aum_usd"),
    cell: (r) => <span className="tnum text-ink2">{compact(v(r, "aum_usd"), "USD")}</span> },
  mcol("top10_weight", "Top-10", true), mcol("return_1y", "1y TR", true), mcol("return_3y_ann", "3y ann.", true),
  mcol("return_5y_ann", "5y ann."), mcol("volatility_1y", "Vol 1y"), mcol("max_drawdown_3y", "Max DD 3y", true),
  mcol("distribution_yield", "Yield", true), mcol("duration", "Duration"), mcol("lookthrough_pe", "Look-thru P/E"),
  mcol("vendor_pe", "Vendor P/E"), mcol("sharpe_1y", "Sharpe"), confCol,
];

export default function DashboardPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const { job, track, busy } = useJobTracker();
  const [asset, setAsset] = usePersisted<"stock" | "fund">("asset", "stock");
  const q = useQuery({ queryKey: ["dashboard", asset], queryFn: () => api.dashboard(asset), refetchInterval: busy ? 8000 : false });
  const d = q.data;
  const [preset, setPreset] = usePersisted("preset", "all");
  const [stockCols, setStockCols] = usePersisted<string[]>("cols2", STOCK_COLS.filter((x) => x.def).map((x) => x.id));
  const [fundCols, setFundCols] = usePersisted<string[]>("fcols", FUND_COLS.filter((x) => x.def).map((x) => x.id));
  const [region, setRegion] = useState("all");
  const [sector, setSector] = useState("all");
  const [index, setIndex] = useState("all");
  const [fclass, setFclass] = useState("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [newSym, setNewSym] = useState("");

  useEffect(() => {  // deep link: /?asset=fund
    const a = new URLSearchParams(window.location.search).get("asset");
    if (a === "fund" || a === "stock") setAsset(a);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (d?.active_job && (!job || job.job_id !== d.active_job.job_id) && !busy) track(d.active_job); },
    [d?.active_job?.job_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = (fn: () => Promise<unknown>) => fn().then((j) => track(j as never)).catch((e: Error) => alert(e.message));
  const add = useMutation({ mutationFn: (s: string) => api.addInstrument(s), onSuccess: (j) => { track(j); setNewSym(""); },
    onError: (e: Error) => alert(e.message) });
  const watch = useMutation({ mutationFn: ({ id, on }: { id: string; on: boolean }) => api.watch(id, on),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dashboard"] }) });

  const rows = useMemo(() => d?.rows ?? [], [d]);
  const sectors = useMemo(() => [...new Set(rows.map((r) => r.sector).filter(Boolean))].sort() as string[], [rows]);
  const indices = useMemo(() => [...new Set(rows.flatMap((r) => r.indices ?? []))].sort(), [rows]);
  const p = PRESETS.find((x) => x.id === preset) ?? PRESETS[0];
  const filtered = useMemo(() => rows.filter((r) =>
    (asset === "fund" || p.test(r)) && (region === "all" || r.region === region)
    && (asset === "fund" ? (fclass === "all" || r.fund_asset_class === fclass || (fclass === "active" && r.style === "active"))
      : (sector === "all" || r.sector === sector) && (index === "all" || (r.indices ?? []).includes(index)))
    && (!search || `${r.symbol} ${r.name ?? ""} ${r.cohort ?? ""}`.toLowerCase().includes(search.toLowerCase()))),
  [rows, p, region, sector, index, fclass, search, asset]);

  const m = d?.run?.manifest;
  const top = asset === "stock" ? rows.find((r) => r.core.rank === 1) : undefined;
  if (q.isLoading) return <div className="space-y-4"><Skeleton className="h-20" /><Skeleton className="h-96" /></div>;
  if (q.isError) return <Empty title="Backend unreachable">Start the API: <code>cd backend && .venv/bin/uvicorn app.main:app --port 8000</code></Empty>;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Daily discovery</h1>
          <p className="mt-0.5 text-sm text-ink2">
            {d?.run ? <>Snapshot {dateFmt(d.run.created_at, true)} ({ago(d.run.created_at)}) · {m?.stocks ?? "—"} stocks, {m?.funds ?? 0} funds ·{" "}
              {m?.eligible_core} stocks eligible for core_v1{m?.failed ? `, ${m.failed} failed` : ""}</> : "No snapshot yet"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <JobPill job={job} />
          <button className="btn" disabled={busy} title="Latest end-of-day prices for every instrument (bulk); recalculates valuation"
            onClick={() => run(() => api.refresh({ scope: "universe", kind: "refresh_prices", mode: "force" }))}><RefreshCw size={14} /> Refresh prices</button>
          <button className="btn" disabled={busy} title="Re-check fundamentals, estimates, targets, fund data and news where stale, then recalculate"
            onClick={() => run(() => api.refresh({ scope: "universe", kind: "refresh_analysis", mode: "if_stale" }))}><RefreshCw size={14} /> Refresh analysis</button>
          <button className="btn" disabled={busy} title="Rerun formulas on stored data — no provider traffic" onClick={() => run(api.recalc)}><Calculator size={14} /> Recalculate</button>
          <button className="btn-primary" disabled={busy} onClick={() => run(() => api.discovery("if_stale"))}><Play size={14} /> Run discovery now</button>
        </div>
      </div>

      <div className="flex gap-1 border-b border-line" role="tablist" aria-label="Asset class">
        {([["stock", "Stocks"], ["fund", "ETFs & funds"]] as const).map(([k, l]) => (
          <button key={k} role="tab" aria-selected={asset === k} onClick={() => { setAsset(k); setSelected([]); }}
            className={`border-b-2 px-4 py-2 text-sm ${asset === k ? "border-accent font-semibold text-ink" : "border-transparent text-ink2 hover:text-ink"}`}>{l}</button>))}
      </div>

      {d?.state === "first_run_backfill" ? (
        <Empty title="Building the first snapshot">Fetching prices, statements, estimates, fund data and news through the provider fallback chains.
          The table appears automatically when the run completes.</Empty>
      ) : (
        <>
          {asset === "stock" ? (
            <>
              {m?.peer_note ? <Note>{m.peer_note}</Note> : null}
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                <Stat label="Top core score" value={top ? <Link className="hover:underline" href={`/instrument/${top.id}`}>{top.symbol} · {top.core.score?.toFixed(1)}</Link> : "—"} sub={top?.name ?? undefined} />
                <Stat label="Discovery candidates" value={rows.filter((r) => (r.discovery?.length ?? 0) > 0).length}
                  sub={Object.entries(m?.discovery_lists ?? {}).slice(0, 3).map(([k, n]) => `${k} ${n}`).join(" · ")} />
                <Stat label="Changes vs last run" value={`${d?.changes?.entrants.length ?? 0} in · ${d?.changes?.exits.length ?? 0} out`}
                  sub={d?.changes?.compared_to ? `${d.changes.movers.length} moved ≥ 5 pts` : "first snapshot — nothing to compare"} />
                <Stat label="Data health" value={`${(d?.health?.stale_prices ?? 0) + (d?.health?.failed ?? 0)} issues`}
                  sub={<Link href="/data-health" className="hover:underline">{d?.health?.stale_prices ?? 0} stale · {d?.health?.failed ?? 0} failed →</Link>} />
                <Stat label="Reference FX" value={<span className="tnum">USD/CAD {num(m?.fx.USDCAD, 4)}</span>}
                  sub={`GBP/CAD ${num(m?.fx.GBPCAD, 4)} · ${m?.fx.provider ?? "—"} ${m?.fx.as_of ?? ""}`} />
              </div>
              <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
                <section className="card p-4" aria-labelledby="vq">
                  <h2 id="vq" className="text-sm font-semibold text-ink">Value vs quality</h2>
                  <p className="mb-2 text-xs text-muted">Component percentiles vs sector peers · {filtered.filter((r) => r.core.score !== null).length} scored stocks in current filter · top 12 labelled</p>
                  <ValueQualityScatter rows={filtered.filter((r) => r.core.score !== null)} />
                </section>
                {d ? <ChangesPanel d={d} /> : null}
              </div>
            </>
          ) : (
            <Note>Funds are ranked <b>within their cohort</b> (same benchmark or strategy), led by cost — the most persistent predictor of fund outcomes.
              Passive funds with an index series use the full model (cost 40 · tracking 30 · diversification 20 · liquidity 10); others use the basic screen.
              Bond and active funds show a dashboard without a composite (spec §7).</Note>
          )}

          <section className="card">
            <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
              {asset === "stock" ? (
                <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Screening presets">
                  {PRESETS.map((x) => (
                    <button key={x.id} role="radio" aria-checked={preset === x.id} title={x.rule}
                      className={`chip ${preset === x.id ? "chip-on" : ""}`} onClick={() => setPreset(x.id)}>{x.label}</button>))}
                </div>
              ) : (
                <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Fund type">
                  {[["all", "All funds"], ["equity", "Equity"], ["allocation", "Asset allocation"], ["fixed_income", "Fixed income"], ["active", "Active"]].map(([k, l]) => (
                    <button key={k} role="radio" aria-checked={fclass === k} className={`chip ${fclass === k ? "chip-on" : ""}`} onClick={() => setFclass(k)}>{l}</button>))}
                </div>
              )}
              <div className="ml-auto flex flex-wrap items-center gap-2">
                <input className="input w-40" placeholder="Search" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search" />
                <select className="input" value={region} onChange={(e) => setRegion(e.target.value)} aria-label="Market">
                  <option value="all">All markets</option><option value="US">US</option><option value="CA">Canada</option><option value="UK">UK</option>
                </select>
                {asset === "stock" ? (
                  <>
                    <select className="input" value={index} onChange={(e) => setIndex(e.target.value)} aria-label="Index">
                      <option value="all">All indices</option>{indices.map((s) => <option key={s}>{s}</option>)}
                    </select>
                    <select className="input max-w-[170px]" value={sector} onChange={(e) => setSector(e.target.value)} aria-label="Sector">
                      <option value="all">All sectors</option>{sectors.map((s) => <option key={s}>{s}</option>)}
                    </select>
                  </>) : null}
                <ColumnPicker cols={asset === "stock" ? STOCK_COLS : FUND_COLS} visible={asset === "stock" ? stockCols : fundCols}
                  setVisible={asset === "stock" ? setStockCols : setFundCols} />
                <a className="btn" href={`/api/v1/exports/${asset}.csv`} title="CSV with currency, period basis, scores and snapshot date"><Download size={14} /> CSV</a>
                <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); if (newSym.trim()) add.mutate(newSym.trim()); }}>
                  <input className="input w-28" placeholder="Add ticker" value={newSym} onChange={(e) => setNewSym(e.target.value.toUpperCase())}
                    aria-label="Add ticker (Yahoo format, e.g. BNS.TO, BP.L)" title="Yahoo-style symbol: AAPL, BNS.TO, BP.L" />
                  <button className="btn px-2" disabled={add.isPending || busy} aria-label="Add ticker"><Plus size={14} /></button>
                </form>
              </div>
            </div>
            <div className="px-3 py-1.5 text-2xs text-muted">{asset === "stock" ? p.rule : "Grouped by cohort, best first"} · {filtered.length} shown · hover any value for its basis or why it is unavailable</div>
            <DataTable rows={filtered} cols={asset === "stock" ? STOCK_COLS : FUND_COLS} visible={asset === "stock" ? stockCols : fundCols}
              defaultSort={{ id: asset === "stock" ? "core" : "fscore", dir: -1 }} selected={selected} setSelected={setSelected}
              onWatch={(r) => watch.mutate({ id: r.id, on: !r.watch })}
              groupBy={asset === "fund" ? (r) => r.cohort ?? "Other" : undefined} pageSize={asset === "fund" ? 100 : 50}
              empty={<>Nothing meets this filter today. That is a valid outcome — relax the filter or widen the universe.</>} />
          </section>
        </>
      )}

      {selected.length ? (
        <div className="fixed inset-x-0 bottom-4 z-40 mx-auto flex w-fit max-w-[95vw] flex-wrap items-center gap-2 rounded-xl border border-line bg-raised px-4 py-2.5 shadow-2xl">
          <span className="text-sm text-ink2">{selected.length} selected</span>
          {selected.map((id) => {
            const r = rows.find((x) => x.id === id);
            return <button key={id} className="chip" onClick={() => setSelected(selected.filter((x) => x !== id))}>{r?.symbol}<X size={11} /></button>;
          })}
          <button className="btn-primary" disabled={selected.length < 2} onClick={() => router.push(`/compare?ids=${selected.join(",")}`)}>
            <GitCompareArrows size={14} /> Compare {selected.length < 2 ? "(pick 2–8)" : ""}</button>
          <button className="btn" onClick={() => setSelected([])}>Clear</button>
        </div>) : null}
    </div>
  );
}

function ChangesPanel({ d }: { d: Dashboard }) {
  const m = d.run?.manifest;
  return (
    <section className="card p-4" aria-labelledby="chg">
      <h2 id="chg" className="text-sm font-semibold text-ink">Significant changes</h2>
      {!d.changes?.compared_to ? (
        <p className="mt-3 text-sm text-ink2">First published snapshot for this universe. Entrants, exits and rank moves appear after the next run.</p>
      ) : (
        <div className="mt-2 space-y-3 text-sm">
          <p className="text-xs text-muted">vs snapshot {dateFmt(d.changes.compared_to.created_at, true)}</p>
          {([["New on shortlist (≥ 60)", d.changes.entrants], ["Left shortlist", d.changes.exits]] as const).map(([t, list]) => (
            <div key={t}><div className="label mb-1">{t}</div>
              {list.length ? list.slice(0, 12).map((e) => <Link key={e.id} href={`/instrument/${e.id}`} className="chip mb-1 mr-1">{e.symbol} {isNum(e.score) ? e.score.toFixed(0) : ""}</Link>)
                : <span className="text-xs text-muted">None</span>}
            </div>))}
          <div><div className="label mb-1">Largest score moves</div>
            {d.changes.movers.length ? d.changes.movers.slice(0, 6).map((mv) => (
              <Link key={mv.id} href={`/instrument/${mv.id}`} className="flex items-center justify-between py-0.5 hover:underline">
                <span>{mv.symbol}</span>
                <span className={`tnum inline-flex items-center gap-1 ${mv.delta > 0 ? "text-up" : "text-down"}`}>
                  {mv.delta > 0 ? <ArrowUp size={12} /> : <ArrowDown size={12} />}{mv.from.toFixed(0)} → {mv.to.toFixed(0)}</span>
              </Link>)) : <span className="text-xs text-muted">None</span>}
          </div>
        </div>
      )}
      <div className="mt-4 border-t border-line pt-3">
        <div className="label mb-1.5">Model notes</div>
        <ul className="space-y-1 text-xs text-ink2">
          <li>Core = Value 30 · Quality 25 · Growth 20 · Resilience 15 · Momentum 10, percentiles vs sector peers.</li>
          <li>Attention ({m?.attention_model}) ranks market interest separately; it never raises the core score.</li>
          <li>Financials, REITs and loss-makers show metrics but no composite until their models are validated.</li>
          <li><Link href="/validation" className="text-accent-ink hover:underline">See the validation report →</Link></li>
        </ul>
      </div>
    </section>
  );
}
