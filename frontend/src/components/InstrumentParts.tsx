"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState } from "react";
import { ComponentBars, DrawdownChart, PriceChart } from "@/components/charts";
import { StatusTag, Trail } from "@/components/provenance";
import { MetricValue, Note, Skeleton } from "@/components/ui";
import { api, type Analysis, type ModelScore } from "@/lib/api";
import { ago, dateFmt, isNum, pct } from "@/lib/format";
import { COMPONENT_LABEL, METRICS } from "@/lib/metrics";

export function MetricGrid({ a, groups }: { a: Analysis; groups: string[] }) {
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {groups.map((g) => {
        const keys = Object.keys(METRICS).filter((k) => METRICS[k].group === g && a.metrics[k]);
        if (!keys.length) return null;
        return (
          <div key={g} className="card p-4">
            <h3 className="label mb-2">{g}</h3>
            <dl className="space-y-1.5">
              {keys.map((k) => (
                <div key={k} className="flex items-baseline justify-between gap-3 text-sm">
                  <dt className="text-ink2" title={METRICS[k].help}>{METRICS[k].label}</dt>
                  <dd><MetricValue k={k} m={a.metrics[k]} /></dd>
                </div>))}
            </dl>
          </div>);
      })}
    </div>
  );
}


/* ================================================================== Price & risk */
export function PriceRisk({ id, a }: { id: string; a: Analysis }) {
  const [range, setRange] = useState("5y");
  const [mode, setMode] = useState<"price" | "tr">("tr");
  const q = useQuery({ queryKey: ["prices", id, range], queryFn: () => api.prices(id, range) });
  return (
    <div className="space-y-4">
      <div className="card p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-ink">{mode === "tr" ? "Total return (rebased to 100)" : `Close price (${a.currency})`}</h3>
          <div className="ml-auto flex gap-1">
            {(["tr", "price"] as const).map((m) => <button key={m} className={`chip ${mode === m ? "chip-on" : ""}`} onClick={() => setMode(m)}>{m === "tr" ? "Total return" : "Price"}</button>)}
            <span className="mx-1 w-px bg-line" />
            {["1y", "3y", "5y", "10y"].map((r) => <button key={r} className={`chip ${range === r ? "chip-on" : ""}`} onClick={() => setRange(r)}>{r}</button>)}
          </div>
        </div>
        <p className="mb-2 text-xs text-muted">{q.data ? `${q.data.adjustment} · source ${q.data.provider}${range === "5y" || range === "10y" ? " · weekly points" : " · daily"}` : ""}</p>
        {q.data ? <PriceChart bars={q.data.bars} currency={a.currency} mode={mode} /> : <Skeleton className="h-[260px]" />}
        <h4 className="mt-4 text-xs font-semibold text-ink2">Drawdown from running peak (total return)</h4>
        {q.data ? <DrawdownChart bars={q.data.bars} /> : null}
      </div>
      <MetricGrid a={a} groups={["Performance", "Risk", "Liquidity", "Attention"]} />
      <p className="text-xs text-muted">Sharpe uses a currency-matched cash rate ({a.run.manifest.cash_rates?.[a.currency]?.source ?? "none"}). Benchmark-relative figures use ETF proxies:{" "}
        {Object.entries(a.run.manifest.benchmarks ?? {}).map(([r, s]) => `${r} ${s}`).join(", ")}.</p>
    </div>
  );
}


/* ================================================================== Sources */
export function Sources({ a }: { a: Analysis }) {
  return (
    <div className="space-y-3">
      <Note>Every dataset walks its provider fallback chain. The first fully-valid result wins; partial results are kept only if nothing better is available. Missing values are never converted to zero.</Note>
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="border-b border-line text-left text-2xs uppercase tracking-wide text-muted">
            {["Dataset", "Provider used", "Status", "Fetched", "Fallback trail"].map((h) => <th key={h} className="px-3 py-2 font-semibold">{h}</th>)}</tr></thead>
          <tbody>{Object.entries(a.sources).map(([ds, s]) => (
            <tr key={ds} className="border-b border-line align-top last:border-0">
              <td className="px-3 py-2 font-medium text-ink">{ds}</td>
              <td className="px-3 py-2 text-ink2">{s?.provider ?? "—"}<div className="text-2xs text-muted">{s?.route}</div></td>
              <td className="px-3 py-2"><StatusTag s={s?.status ?? "missing"} /></td>
              <td className="px-3 py-2 text-ink2">{s?.fetched_at ? ago(s.fetched_at) : "—"}</td>
              <td className="px-3 py-2"><Trail attempts={s?.attempts ?? []} /></td>
            </tr>))}</tbody>
        </table>
      </div>
    </div>
  );
}


/* ================================================================== Notes */
export function Notes({ id }: { id: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["notes", id], queryFn: () => api.notes(id) });
  const [body, setBody] = useState("");
  const add = useMutation({ mutationFn: () => api.addNote(id, body), onSuccess: () => { setBody(""); qc.invalidateQueries({ queryKey: ["notes", id] }); } });
  const del = useMutation({ mutationFn: (nid: string) => api.deleteNote(nid), onSuccess: () => qc.invalidateQueries({ queryKey: ["notes", id] }) });
  return (
    <div className="space-y-3">
      <form className="card p-4" onSubmit={(e) => { e.preventDefault(); if (body.trim()) add.mutate(); }}>
        <label className="label" htmlFor="note">Research note</label>
        <textarea id="note" className="input mt-1 h-28 w-full" value={body} onChange={(e) => setBody(e.target.value)}
          placeholder="Thesis, risks, what would change your mind…" />
        <button className="btn-primary mt-2" disabled={!body.trim() || add.isPending}>Save note</button>
      </form>
      {(q.data ?? []).map((n) => (
        <div key={n.id} className="card flex gap-3 p-4">
          <div className="flex-1"><div className="text-2xs text-muted">{dateFmt(n.created_at, true)}</div><p className="mt-1 whitespace-pre-wrap text-sm text-ink">{n.body}</p></div>
          <button aria-label="Delete note" onClick={() => del.mutate(n.id)} className="text-muted hover:text-down"><Trash2 size={14} /></button>
        </div>))}
    </div>
  );
}

export function ScoreCard({ title, s, explain }: { title: string; s?: ModelScore; explain?: boolean }) {
  const [sel, setSel] = useState<string | null>(null);
  if (!s) return null;
  const comp = sel && s.components ? s.components[sel] : null;
  return (
    <div className="card p-4">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        {isNum(s.rank) ? <span className="text-xs text-muted">Rank #{s.rank}</span> : null}
      </div>
      {isNum(s.score) ? (
        <div className="mt-1 flex items-baseline gap-2"><span className="text-3xl font-semibold text-ink tnum">{s.score.toFixed(1)}</span>
          <span className="text-xs text-muted">/ 100 · input coverage {pct(s.coverage, 0)}</span></div>
      ) : (
        <div className="mt-2 space-y-1"><div className="text-sm font-medium text-ink2">{s.status === "not_applicable" ? "Not scored by this model" : "Insufficient data"}</div>
          {(s.reasons ?? []).map((r) => <div key={r} className="text-xs text-muted">· {r}</div>)}</div>
      )}
      {explain && s.components && isNum(s.score) ? (
        <div className="mt-4">
          <ComponentBars comps={s.components} selected={sel} onSelect={(k) => setSel(sel === k ? null : k)} />
          {comp ? (
            <div className="mt-3 overflow-x-auto rounded-lg border border-line">
              <table className="w-full text-xs">
                <thead className="bg-page"><tr className="text-left text-muted">
                  <th className="px-2 py-1.5 font-semibold">{COMPONENT_LABEL[sel!]} input</th><th className="px-2 font-semibold text-right">Value</th>
                  <th className="px-2 font-semibold text-right">Pctl</th><th className="px-2 font-semibold text-right">Weight</th><th className="px-2 font-semibold">Peer group</th></tr></thead>
                <tbody>{comp.inputs.map((i) => (
                  <tr key={i.metric} className="border-t border-line">
                    <td className="px-2 py-1.5 text-ink2" title={i.basis ?? i.reason}>{METRICS[i.metric]?.label ?? i.metric}
                      {i.direction === "lower" ? <span className="text-muted"> (lower better)</span> : null}</td>
                    <td className="px-2 text-right"><MetricValue k={i.metric} m={{ value: i.raw, status: i.status, basis: i.basis, reason: i.reason }} /></td>
                    <td className="px-2 text-right tnum">{isNum(i.percentile) ? i.percentile.toFixed(0) : "—"}</td>
                    <td className="px-2 text-right tnum text-ink2" title={`planned ${pct(i.planned_weight, 0)}`}>{pct(i.effective_weight, 0)}</td>
                    <td className="px-2 text-muted">{i.peer_level ? `${i.peer_level} (n=${i.peer_n})` : i.reason ?? "—"}</td>
                  </tr>))}</tbody>
              </table>
            </div>) : null}
        </div>
      ) : null}
    </div>
  );
}

