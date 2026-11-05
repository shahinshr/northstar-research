"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, ExternalLink } from "lucide-react";
import { StatusTag, Trail } from "@/components/provenance";
import { Empty, Note, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import { useState } from "react";

const DS = ["profile", "prices", "financials", "estimates", "price_targets", "news", "fund_profile", "holdings"];

export default function DataHealthPage() {
  const q = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 30000 });
  const [filter, setFilter] = useState("");
  const [problems, setProblems] = useState(true);
  if (q.isLoading) return <Skeleton className="h-96" />;
  if (!q.data) return <Empty title="Backend unreachable" />;
  const h = q.data;
  const missing = h.providers.filter((p) => p.requires_key && !p.configured);
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Data health</h1>
        <p className="text-sm text-ink2">Provider status, fallback order and the latest fetch per dataset. Failures never become zeros.</p>
      </div>
      {missing.length ? (
        <Note>Running on free sources. Add keys to <code>backend/.env</code> to enable: {missing.map((p) => p.name).join(", ")} — see <code>docs/api-keys.md</code>, then restart the API.</Note>
      ) : null}

      {h.universe ? (
        <section className="card p-4 text-sm">
          <h2 className="label mb-1">Universe</h2>
          <p className="text-ink2">Mode <b className="text-ink">{h.universe.mode}</b> · {h.universe.stocks} stocks · {h.universe.funds} funds · constituents refreshed {ago(h.universe.fetched_at)}</p>
          <ul className="mt-1 text-xs text-muted">{h.universe.notes.map((n) => <li key={n}>· {n}</li>)}</ul>
        </section>) : null}

      <section>
        <h2 className="label mb-2">Providers</h2>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {h.providers.map((p) => (
            <div key={p.id} className="card p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-ink">{p.name}</span>
                {p.configured ? <span className="inline-flex items-center gap-1 text-xs text-up"><CheckCircle2 size={13} /> Active</span>
                  : <span className="inline-flex items-center gap-1 text-xs text-muted"><CircleDashed size={13} /> Key needed</span>}
              </div>
              <div className="mt-1 text-xs text-muted">{p.datasets.join(" · ")} · {p.markets.join("/")}</div>
              {p.calls_today ? <div className="mt-1 text-2xs text-ink2">{p.calls_today.toLocaleString()} calls today</div> : null}
              {!p.configured && p.signup_url ? <a href={p.signup_url} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs text-accent-ink hover:underline">Get a key <ExternalLink size={11} /></a> : null}
            </div>))}
        </div>
      </section>

      <section className="card p-4">
        <h2 className="label mb-2">Fallback chains (edit via CHAIN_* in backend/.env)</h2>
        <div className="grid gap-x-6 gap-y-1.5 md:grid-cols-2">
          {Object.entries(h.chains).map(([ds, chain]) => (
            <div key={ds} className="flex flex-wrap items-center gap-1 text-xs">
              <span className="w-24 font-medium text-ink">{ds}</span>
              {chain.map((pid, i) => {
                const p = h.providers.find((x) => x.id === pid);
                const on = ds === "fx" ? true : p?.configured;
                return <span key={pid} className={`chip py-0.5 text-2xs ${on ? "" : "opacity-50 line-through"}`}>{i + 1}. {pid}</span>;
              })}
            </div>))}
        </div>
      </section>

      <section className="card p-4">
        <h2 className="label mb-2">Macro series</h2>
        <div className="space-y-2">
          {Object.entries(h.macro).map(([k, v]) => (
            <div key={k} className="flex flex-wrap items-center gap-3 text-sm">
              <span className="w-24 font-medium text-ink">{k === "fx" ? "FX (CAD base)" : "Cash rates"}</span>
              {v ? <><StatusTag s={v.status} /><span className="text-xs text-ink2">{v.provider} · {ago(v.fetched_at)}</span><Trail attempts={v.attempts ?? []} /></> : <span className="text-xs text-muted">not fetched</span>}
            </div>))}
        </div>
      </section>

      <section className="card overflow-x-auto">
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
          <input className="input w-48" placeholder="Filter symbol" value={filter} onChange={(e) => setFilter(e.target.value.toUpperCase())} />
          <label className="flex items-center gap-1.5 text-xs text-ink2"><input type="checkbox" checked={problems} onChange={(e) => setProblems(e.target.checked)} /> Only rows with partial / failed data</label>
          <span className="ml-auto text-xs text-muted">{h.matrix.length} instruments</span>
        </div>
        <table className="w-full text-sm">
          <thead><tr className="border-b border-line text-left text-2xs uppercase tracking-wide text-muted">
            <th className="sticky left-0 bg-surface px-3 py-2 font-semibold">Instrument</th>
            {DS.map((d) => <th key={d} className="px-3 py-2 font-semibold">{d}</th>)}</tr></thead>
          <tbody>{h.matrix.filter((r) => (!filter || r.symbol.includes(filter)) &&
            (!problems || Object.values(r.datasets).some((c) => c && c.status !== "valid"))).slice(0, 300).map((r) => (
            <tr key={r.id} className="border-b border-line last:border-0">
              <td className="sticky left-0 bg-surface px-3 py-2 font-medium text-ink">{r.symbol}{r.asset_type !== "stock" ? <span className="ml-1 text-2xs text-muted">{r.asset_type}</span> : null}</td>
              {DS.map((d) => {
                const c = r.datasets[d];
                if (c === undefined) return <td key={d} className="px-3 text-2xs text-muted">n/a</td>;
                if (c === null) return <td key={d} className="px-3 text-2xs text-muted">pending</td>;
                const tip = (c.attempts ?? []).filter((a) => a.provider).map((a) => `${a.provider}: ${a.status}${a.error ? ` (${a.error})` : ""}`).join("\n");
                return (
                  <td key={d} className="px-3 py-2" title={tip}>
                    <StatusTag s={c.status} /><div className="text-2xs text-muted">{c.provider ?? "all failed"} · {ago(c.fetched_at)}</div>
                  </td>);
              })}
            </tr>))}</tbody>
        </table>
      </section>
    </div>
  );
}
