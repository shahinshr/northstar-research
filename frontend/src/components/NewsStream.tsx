"use client";

import { AlertOctagon, ArrowDownRight, ArrowUpRight, ExternalLink, Minus, Radio } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import type { StreamItem } from "@/lib/api";
import { ago } from "@/lib/format";

const SEV = {
  critical: { label: "Critical", color: "var(--bad)" },
  high: { label: "Important", color: "var(--serious)" },
  normal: { label: "", color: "var(--line-strong)" },
} as const;

function when(v: string | number | null | undefined) {
  if (v === null || v === undefined) return "";
  const iso = typeof v === "number" ? new Date(v * 1000).toISOString() : v;
  return ago(iso);
}

export function NewsItemRow({ n, showSymbol }: { n: StreamItem; showSymbol?: boolean }) {
  const s = SEV[n.severity] ?? SEV.normal;
  const Pol = n.polarity === "positive" ? ArrowUpRight : n.polarity === "negative" ? ArrowDownRight : Minus;
  return (
    <li className="group relative border-b border-line py-3 pl-4 pr-2 last:border-0">
      <span className="absolute inset-y-3 left-0 w-[3px] rounded-full" style={{ background: s.color }} aria-hidden />
      <div className="flex items-center gap-1.5 text-2xs text-muted">
        {n.severity !== "normal" ? (
          <span className="inline-flex items-center gap-1 font-semibold uppercase tracking-wide" style={{ color: s.color }}>
            <AlertOctagon size={11} aria-hidden />{s.label}
          </span>) : null}
        {showSymbol && n.symbol ? (
          <Link href={`/instrument/${n.instrument_id}`} className="rounded-md bg-sunken px-1.5 py-0.5 font-semibold text-ink hover:underline">{n.symbol}</Link>) : null}
        <span className="truncate">{n.source}</span><span>·</span><span className="whitespace-nowrap">{when(n.published_at)}</span>
        <Pol size={12} className={`ml-auto shrink-0 ${n.polarity === "positive" ? "text-up" : n.polarity === "negative" ? "text-down" : "text-muted"}`}
          aria-label={`${n.polarity} tone`} />
      </div>
      <a href={n.url} target="_blank" rel="noreferrer noopener" className="mt-1 block text-sm font-medium leading-snug text-ink hover:text-accent-ink">
        {n.title}
      </a>
      {n.tags?.length || n.match === "mention" ? (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {n.tags.map((t) => <span key={t} className="rounded-md border border-line px-1.5 py-0.5 text-2xs text-ink2">{t}</span>)}
          {n.match === "mention" ? <span className="rounded-md border border-dashed border-line px-1.5 py-0.5 text-2xs text-muted" title="Company named in the summary, not the headline">mention</span> : null}
        </div>) : null}
    </li>
  );
}

export default function NewsStream({ items, title, subtitle, showSymbol, social, loading, maxHeight = 640 }: {
  items: StreamItem[]; title: string; subtitle?: React.ReactNode; showSymbol?: boolean; loading?: boolean; maxHeight?: number;
  social?: { x_live: string; stocktwits: string; note: string };
}) {
  const [only, setOnly] = useState(false);
  const shown = only ? items.filter((n) => n.severity !== "normal") : items;
  const crit = items.filter((n) => n.severity === "critical").length;
  return (
    <section className="card flex flex-col overflow-hidden" aria-label={title}>
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <Radio size={15} className="text-accent" aria-hidden />
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {crit ? <span className="rounded-full px-2 py-0.5 text-2xs font-semibold text-white" style={{ background: "var(--bad)" }}>{crit} critical</span> : null}
        <div className="seg ml-auto" role="radiogroup" aria-label="Filter">
          <button role="radio" aria-checked={!only} onClick={() => setOnly(false)}>All</button>
          <button role="radio" aria-checked={only} onClick={() => setOnly(true)}>Important</button>
        </div>
      </div>
      {subtitle ? <div className="border-b border-line px-4 py-1.5 text-2xs text-muted">{subtitle}</div> : null}
      <ul className="scroll-thin overflow-auto px-2" style={{ maxHeight }}>
        {loading && !items.length ? Array.from({ length: 5 }).map((_, i) => <li key={i} className="shimmer my-3 h-12 rounded-lg" />) : null}
        {!loading && !shown.length ? <li className="px-2 py-6 text-sm text-muted">{only ? "No critical or important stories right now." : "No stories yet."}</li> : null}
        {shown.map((n, i) => <NewsItemRow key={`${n.url}-${i}`} n={n} showSymbol={showSymbol} />)}
      </ul>
      {social ? (
        <div className="border-t border-line bg-sunken px-4 py-3 text-xs">
          <div className="mb-1.5 font-semibold text-ink">Live social chatter</div>
          <div className="flex flex-wrap gap-2">
            <a className="btn rounded-full py-1 text-xs" href={social.x_live} target="_blank" rel="noreferrer noopener">Posts on X <ExternalLink size={11} /></a>
            <a className="btn rounded-full py-1 text-xs" href={social.stocktwits} target="_blank" rel="noreferrer noopener">StockTwits <ExternalLink size={11} /></a>
          </div>
          <p className="mt-1.5 text-2xs text-muted">{social.note}</p>
        </div>) : null}
    </section>
  );
}
