"use client";

import { useRouter } from "next/navigation";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from "recharts";
import type { Row } from "@/lib/api";
import { compact, isNum, num, pct } from "@/lib/format";
import { REGION_COLOR, REGION_LABEL, SERIES } from "@/lib/metrics";

const AXIS = { tickLine: false, axisLine: { stroke: "var(--axis)" }, tick: { fill: "var(--muted)", fontSize: 11 } } as const;

function TipBox({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-line bg-raised px-3 py-2 text-xs text-ink shadow-lg">{children}</div>;
}

function LegendRow({ items }: { items: { label: string; color: string }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink2">
      {items.map((i) => (
        <span key={i.label} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: i.color }} aria-hidden />{i.label}
        </span>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ value vs quality bubble */
export function ValueQualityScatter({ rows }: { rows: Row[] }) {
  const router = useRouter();
  const pts = rows
    .filter((r) => isNum(r.components?.value) && isNum(r.components?.quality))
    .map((r) => ({ x: r.components.value, y: r.components.quality, z: r.market_cap?.usd ?? 1e9, sym: r.symbol, name: r.name,
      region: r.region, core: r.core.score, id: r.id, lbl: "" }));
  // label only the 12 highest core scores — hundreds of labels would collide (hover shows the rest)
  [...pts].sort((a, b) => (b.core ?? 0) - (a.core ?? 0)).slice(0, 12).forEach((p) => { p.lbl = p.sym; });
  const regions = (["US", "CA", "UK"] as const).filter((g) => pts.some((p) => p.region === g));
  if (!pts.length) return <p className="py-10 text-center text-sm text-muted">No scored stocks yet.</p>;
  return (
    <div>
      <div className="text-2xs text-muted">↑ Quality score (peer percentile)</div>
      <div className="h-[300px]">
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 10, right: 16, bottom: 22, left: 0 }}>
            <CartesianGrid strokeDasharray="0" />
            <XAxis type="number" dataKey="x" domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} {...AXIS}
              label={{ value: "Value score (peer percentile) →", position: "insideBottom", offset: -12, fill: "var(--muted)", fontSize: 11 }} />
            <YAxis type="number" dataKey="y" domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} width={36} {...AXIS} />
            <ZAxis type="number" dataKey="z" range={pts.length > 60 ? [20, 260] : [60, 600]} />
            <ReferenceLine x={50} stroke="var(--axis)" strokeDasharray="3 3" />
            <ReferenceLine y={50} stroke="var(--axis)" strokeDasharray="3 3" />
            <Tooltip cursor={false} content={({ payload }) => {
              const p = payload?.[0]?.payload;
              return p ? (
                <TipBox>
                  <div className="font-semibold">{p.sym} <span className="font-normal text-muted">{p.name}</span></div>
                  <div className="tnum mt-1 grid grid-cols-2 gap-x-3 text-ink2">
                    <span>Value</span><span className="text-ink">{num(p.x, 1)}</span>
                    <span>Quality</span><span className="text-ink">{num(p.y, 1)}</span>
                    <span>Core score</span><span className="text-ink">{num(p.core, 1)}</span>
                    <span>Market cap</span><span className="text-ink">{compact(p.z, "USD")}</span>
                  </div>
                </TipBox>) : null;
            }} />
            {regions.map((g) => (
              <Scatter key={g} name={REGION_LABEL[g]} data={pts.filter((p) => p.region === g)} fill={REGION_COLOR[g]}
                fillOpacity={0.85} stroke="var(--surface)" strokeWidth={2} cursor="pointer" isAnimationActive={false}
                onClick={(d: { id?: string; payload?: { id: string } }) => router.push(`/instrument/${d.payload?.id ?? d.id}`)}>
                <LabelList dataKey="lbl" position="top" offset={8} stroke="none" style={{ fill: "var(--ink-2)", fontSize: 10, stroke: "none" }} />
              </Scatter>
            ))}
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
        <LegendRow items={regions.map((g) => ({ label: REGION_LABEL[g], color: REGION_COLOR[g] }))} />
        <span className="text-2xs text-muted">Bubble size = market cap · eligible stocks only · top-right = cheap and high quality vs peers</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ component score bars (HTML) */
export function ComponentBars({ comps, onSelect, selected }: {
  comps: Record<string, { score: number | null; weight: number; coverage: number }>;
  onSelect?: (k: string) => void; selected?: string | null;
}) {
  const labels: Record<string, string> = { value: "Value", quality: "Quality", growth: "Growth", resilience: "Resilience", momentum: "Momentum", revisions: "Revisions" };
  return (
    <div className="space-y-2.5">
      {Object.entries(comps).map(([k, c]) => (
        <button key={k} type="button" onClick={() => onSelect?.(k)}
          className={`grid w-full grid-cols-[96px_1fr_44px] items-center gap-3 rounded-md px-1 py-0.5 text-left ${selected === k ? "bg-page" : "hover:bg-page"}`}
          aria-pressed={selected === k} title={`${labels[k] ?? k}: weight ${pct(c.weight, 0)}, input coverage ${pct(c.coverage, 0)}`}>
          <span className="text-sm text-ink2">{labels[k] ?? k} <span className="text-2xs text-muted">{pct(c.weight, 0)}</span></span>
          <span className="relative h-2 rounded-full bg-grid">
            {isNum(c.score) ? <span className="absolute inset-y-0 left-0 rounded-full bg-accent" style={{ width: `${Math.max(2, c.score)}%` }} /> : null}
            <span className="absolute inset-y-[-3px] left-1/2 w-px bg-axis" style={{ background: "var(--axis)" }} aria-hidden />
          </span>
          <span className="tnum text-right text-sm font-semibold text-ink">{isNum(c.score) ? c.score.toFixed(0) : "—"}</span>
        </button>
      ))}
      <p className="pt-1 text-2xs text-muted">Bars are peer percentiles (50 = peer median, marked). Click a component to see its inputs.</p>
    </div>
  );
}

/* ------------------------------------------------------------------ price & drawdown */
export function PriceChart({ bars, currency, mode }: { bars: { d: string; c: number; ac: number }[]; currency: string; mode: "price" | "tr" }) {
  const key = mode === "price" ? "c" : "ac";
  const first = bars[0]?.[key] ?? 1;
  const data = bars.map((b) => ({ d: b.d, v: mode === "price" ? b.c : (b.ac / first) * 100 }));
  const up = data.length > 1 && data[data.length - 1].v >= data[0].v;
  return (
    <div className="h-[260px]">
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="pfill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--s1)" stopOpacity={0.18} />
              <stop offset="100%" stopColor="var(--s1)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="d" {...AXIS} minTickGap={48} tickFormatter={(d: string) => d.slice(0, 7)} />
          <YAxis {...AXIS} width={52} domain={["auto", "auto"]} tickFormatter={(v: number) => (mode === "tr" ? v.toFixed(0) : num(v, v > 100 ? 0 : 2))} />
          <Tooltip cursor={{ stroke: "var(--axis)" }} content={({ payload, label }) => payload?.length ? (
            <TipBox><div className="text-muted">{label}</div>
              <div className="tnum font-semibold">{mode === "tr" ? `${num(payload[0].value as number, 1)} (rebased)` : `${num(payload[0].value as number)} ${currency}`}</div></TipBox>) : null} />
          <Area type="monotone" dataKey="v" stroke="var(--s1)" strokeWidth={2} fill="url(#pfill)" dot={false} isAnimationActive={false}
            name={up ? "rising" : "falling"} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function DrawdownChart({ bars }: { bars: { d: string; dd: number }[] }) {
  return (
    <div className="h-[140px]">
      <ResponsiveContainer>
        <AreaChart data={bars} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="d" {...AXIS} minTickGap={48} tickFormatter={(d: string) => d.slice(0, 7)} />
          <YAxis {...AXIS} width={52} tickFormatter={(v: number) => pct(v, 0)} domain={["dataMin", 0]} />
          <Tooltip cursor={{ stroke: "var(--axis)" }} content={({ payload, label }) => payload?.length ? (
            <TipBox><div className="text-muted">{label}</div><div className="tnum font-semibold">Drawdown {pct(payload[0].value as number)}</div></TipBox>) : null} />
          <Area type="monotone" dataKey="dd" stroke="var(--s8)" strokeWidth={1.5} fill="var(--s8)" fillOpacity={0.15} dot={false} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ------------------------------------------------------------------ comparison */
export function CompareLines({ series, symbols }: { series: Record<string, number | string>[]; symbols: string[] }) {
  return (
    <div>
      <div className="h-[320px]">
        <ResponsiveContainer>
          <LineChart data={series} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="d" {...AXIS} minTickGap={48} tickFormatter={(d: string) => d.slice(0, 7)} />
            <YAxis {...AXIS} width={44} domain={["auto", "auto"]} />
            <ReferenceLine y={100} stroke="var(--axis)" strokeDasharray="3 3" />
            <Tooltip cursor={{ stroke: "var(--axis)" }} content={({ payload, label }) => payload?.length ? (
              <TipBox>
                <div className="mb-1 text-muted">{label}</div>
                {[...payload].sort((a, b) => (b.value as number) - (a.value as number)).map((p) => (
                  <div key={p.dataKey as string} className="tnum flex items-center justify-between gap-4">
                    <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: p.color }} />{p.dataKey as string}</span>
                    <span className="font-semibold">{num(p.value as number, 1)}</span>
                  </div>))}
              </TipBox>) : null} />
            {symbols.map((s, i) => (
              <Line key={s} type="monotone" dataKey={s} stroke={SERIES[i % 8]} strokeWidth={2} dot={false} isAnimationActive={false} />
            ))}
            <Legend content={() => null} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2"><LegendRow items={symbols.map((s, i) => ({ label: s, color: SERIES[i % 8] }))} /></div>
    </div>
  );
}

export function ReturnRiskScatter({ points }: { points: { sym: string; ret: number; vol: number; i: number }[] }) {
  return (
    <div>
    <div className="text-2xs text-muted">↑ Total return 1y</div>
    <div className="h-[280px]">
      <ResponsiveContainer>
        <ScatterChart margin={{ top: 16, right: 24, bottom: 22, left: 0 }}>
          <CartesianGrid />
          <XAxis type="number" dataKey="vol" {...AXIS} tickFormatter={(v: number) => pct(v, 0)}
            label={{ value: "Volatility 1y (annualised) →", position: "insideBottom", offset: -12, fill: "var(--muted)", fontSize: 11 }} />
          <YAxis type="number" dataKey="ret" {...AXIS} width={48} tickFormatter={(v: number) => pct(v, 0)} />
          <ReferenceLine y={0} stroke="var(--axis)" />
          <Tooltip cursor={false} content={({ payload }) => {
            const p = payload?.[0]?.payload;
            return p ? <TipBox><div className="font-semibold">{p.sym}</div><div className="tnum text-ink2">Return {pct(p.ret)} · Vol {pct(p.vol)}</div></TipBox> : null;
          }} />
          <Scatter data={points} isAnimationActive={false}>
            {points.map((p) => <Cell key={p.sym} fill={SERIES[p.i % 8]} stroke="var(--surface)" strokeWidth={2} />)}
            <LabelList dataKey="sym" position="top" offset={8} stroke="none" style={{ fill: "var(--ink-2)", fontSize: 11, stroke: "none" }} />
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </div>
    </div>
  );
}

/* ------------------------------------------------------------------ financial history bars */
export function FinBars({ data, label, currency }: { data: { p: string; v: number | null }[]; label: string; currency: string }) {
  const clean = data.filter((d) => isNum(d.v));
  if (!clean.length) return <div className="flex h-[170px] items-center justify-center text-xs text-muted">{label}: N/A</div>;
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-ink2">{label} <span className="text-muted">({currency})</span></div>
      <div className="h-[150px]">
        <ResponsiveContainer>
          <BarChart data={clean} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} barCategoryGap={6}>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="p" {...AXIS} />
            <YAxis {...AXIS} width={48} tickFormatter={(v: number) => compact(v)} />
            <ReferenceLine y={0} stroke="var(--axis)" />
            <Tooltip cursor={{ fill: "var(--grid)" }} content={({ payload, label: l }) => payload?.length ? (
              <TipBox><div className="text-muted">{l}</div><div className="tnum font-semibold">{compact(payload[0].value as number, currency)}</div></TipBox>) : null} />
            <Bar dataKey="v" radius={[4, 4, 0, 0]} maxBarSize={36} isAnimationActive={false}>
              {clean.map((d) => <Cell key={d.p} fill={(d.v as number) < 0 ? "var(--s8)" : "var(--s1)"} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ diverging color for heatmap cells */
export function divergingColor(v: number, maxAbs = 0.6): string {
  const t = Math.max(-1, Math.min(1, v / maxAbs));
  const pole = t >= 0 ? "var(--div-pos)" : "var(--div-neg)";
  return `color-mix(in srgb, ${pole} ${Math.round(Math.abs(t) * 85)}%, var(--div-mid))`;
}

/* ------------------------------------------------------------------ signed gap area (tracking) */
export function GapChart({ data }: { data: { d: string; v: number }[] }) {
  return (
    <div className="h-[140px]">
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="d" {...AXIS} minTickGap={48} tickFormatter={(d: string) => d.slice(0, 7)} />
          <YAxis {...AXIS} width={44} tickFormatter={(v: number) => v.toFixed(1)} />
          <ReferenceLine y={0} stroke="var(--axis)" />
          <Tooltip cursor={{ stroke: "var(--axis)" }} content={({ payload, label }) => payload?.length ? (
            <TipBox><div className="text-muted">{label}</div><div className="tnum font-semibold">Gap {num(payload[0].value as number, 2)} pts</div></TipBox>) : null} />
          <Area type="monotone" dataKey="v" stroke="var(--s2)" strokeWidth={1.5} fill="var(--s2)" fillOpacity={0.15} dot={false} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ------------------------------------------------------------------ overlap heatmap (sequential, one hue) */
export function OverlapMatrix({ symbols, cells }: { symbols: string[]; cells: { a: string; b: string; overlap: number | null; exact: boolean }[] }) {
  const get = (a: string, b: string) => cells.find((c) => c.a === a && c.b === b);
  return (
    <div className="overflow-x-auto">
      <table className="text-sm tnum">
        <thead><tr><th />{symbols.map((s) => <th key={s} className="p-1.5 text-2xs font-semibold text-muted">{s}</th>)}</tr></thead>
        <tbody>{symbols.map((a) => (
          <tr key={a}><th className="p-1.5 text-right text-2xs font-semibold text-muted">{a}</th>
            {symbols.map((b) => {
              const c = get(a, b);
              const v = c?.overlap;
              const self = a === b;
              return (
                <td key={b} className="min-w-[72px] border-2 border-surface p-2 text-center"
                  style={{ background: self ? "var(--grid)" : isNum(v) ? `color-mix(in srgb, var(--s1) ${Math.round(v * 90)}%, var(--surface))` : "var(--grid)",
                    color: isNum(v) && v > 0.55 && !self ? "#fff" : "var(--ink)" }}
                  title={self ? a : isNum(v) ? `${a} ∩ ${b}: ${pct(v, 1)}${c?.exact ? "" : " (lower bound: top-10 data)"}` : "No holdings data"}>
                  {self ? "—" : isNum(v) ? `${c?.exact ? "" : "≥"}${pct(v, 0)}` : "N/A"}
                </td>);
            })}
          </tr>))}</tbody>
      </table>
    </div>
  );
}
