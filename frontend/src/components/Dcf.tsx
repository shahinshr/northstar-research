"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Analysis } from "@/lib/api";
import { ago, compact, isNum, num, pct } from "@/lib/format";
import { divergingColor } from "./charts";
import { Note } from "./ui";

/**
 * FCFF scenario workspace (spec §8). FCFF = EBIT(1−t) + D&A − capex − ΔNWC, discounted at WACC;
 * equity = EV − debt + cash − minority − preferred. Historical API values anchor year 0; every
 * forward input is an explicit user assumption. Computed client-side; nothing touches provider facts.
 */
type Assump = { growth: number; margin: number; tax: number; s2c: number; wacc: number; tg: number; roicT: number };
type Scen = "bear" | "base" | "bull";
const YEARS = 5;

/** Sales-to-capital formulation (Damodaran): reinvestment = ΔRevenue / (sales-to-capital), equal to
 *  capex − D&A + ΔNWC. Terminal reinvestment rate = g / terminal ROIC keeps growth and returns consistent. */
function project(a: Assump, rev0: number, margin0: number) {
  const rows = [];
  let rev = rev0, pv = 0;
  for (let t = 1; t <= YEARS; t++) {
    const prev = rev;
    rev = rev * (1 + a.growth);
    const m = margin0 + (a.margin - margin0) * (t / YEARS); // linear convergence to target margin
    const ebit = rev * m;
    const reinvest = (rev - prev) / a.s2c;
    const fcff = ebit * (1 - a.tax) - reinvest;
    const df = 1 / (1 + a.wacc) ** t;
    pv += fcff * df;
    rows.push({ t, rev, m, ebit, fcff, df, pvf: fcff * df });
  }
  const last = rows[rows.length - 1];
  const nopatN1 = last.ebit * (1 + a.tg) * (1 - a.tax);
  const tv = (nopatN1 * (1 - a.tg / a.roicT)) / (a.wacc - a.tg);
  const pvTv = tv * last.df;
  return { rows, pvExplicit: pv, pvTv, ev: pv + pvTv };
}

export default function Dcf({ a }: { a: Analysis }) {
  const x = a.anchors;
  const applicable = a.eligibility?.model_family !== "financials";
  const rev0 = x?.revenue ?? 0;
  const margin0 = x && x.revenue ? (x.operating_income ?? x.ebit ?? 0) / x.revenue : 0;
  const effTax = x && x.pretax_income && x.pretax_income > 0 && x.income_tax != null ? x.income_tax / x.pretax_income : 0.25;
  const g0 = a.metrics.revenue_growth?.status === "valid" ? (a.metrics.revenue_growth.value as number) : 0.04;
  const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
  const baseG = clamp(g0, -0.05, 0.2);
  const s2c0 = x && x.revenue && x.invested_capital && x.invested_capital > 0 ? clamp(x.revenue / x.invested_capital, 0.5, 5) : 1.5;
  const tax = clamp(effTax, 0.1, 0.35);

  // Normalise to the company's own fiscal-year history so a cyclical peak is not extrapolated (spec §8)
  const med = (xs: number[]) => { const v = [...xs].sort((p, q) => p - q); return v.length ? (v.length % 2 ? v[(v.length - 1) / 2] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2) : null; };
  const histM = (x?.fy_margins ?? []).filter((v) => Number.isFinite(v));
  const histG = (x?.fy_growth ?? []).filter((v) => Number.isFinite(v));
  const normM = histM.length >= 3 ? (med(histM) as number) : margin0;
  const minM = histM.length >= 3 ? Math.min(...histM) : margin0 - 0.03;
  const normG = clamp(histG.length >= 2 ? (med(histG) as number) : baseG, -0.05, 0.15);
  const peak = histM.length >= 3 && margin0 - normM > 0.05;
  // Terminal ROIC seeded from the company's own ROIC: high-return franchises keep an edge, but capped (competition)
  const roicNow = a.metrics.roic?.status === "valid" ? (a.metrics.roic.value as number) : 0.12;
  const roicBase = clamp(roicNow, 0.10, 0.25);
  const [s, setS] = useState<Record<Scen, Assump>>(() => ({
    bear: { growth: Math.min(normG, baseG) - 0.04, margin: Math.min(minM, normM - 0.02), tax, s2c: s2c0 * 0.8, wacc: 0.095, tg: 0.02, roicT: 0.10 },
    base: { growth: normG, margin: normM, tax, s2c: s2c0, wacc: 0.085, tg: 0.025, roicT: roicBase },
    bull: { growth: Math.max(normG, baseG) + 0.02, margin: Math.max(margin0, normM + 0.02), tax, s2c: s2c0 * 1.2, wacc: 0.0775, tg: 0.03, roicT: clamp(roicNow, 0.12, 0.40) },
  }));
  const [active, setActive] = useState<Scen>("base");
  const qc = useQueryClient();
  const saved = useQuery({ queryKey: ["scenarios", a.id], queryFn: () => api.scenarios(a.id) });
  const [scName, setScName] = useState("My valuation");

  const claims = (x?.total_debt ?? 0) - (x?.cash ?? 0) + (x?.minority_interest ?? 0) + (x?.preferred_equity ?? 0);
  const fx = x?.fx_fin_to_px ?? null;
  const shares = x?.diluted_shares ?? null;
  const price = a.price.value;

  const results = useMemo(() => Object.fromEntries((["bear", "base", "bull"] as Scen[]).map((k) => {
    const v = s[k];
    if (v.tg >= v.wacc) return [k, null];
    const r = project(v, rev0, margin0);
    const eq = r.ev - claims;
    const ps = shares && fx && eq > 0 ? (eq / shares) * fx : null;  // never show a negative share price
    return [k, { ...r, equity: eq, perShare: ps, upside: ps ? ps / price - 1 : null, tvShare: r.ev > 0 ? r.pvTv / r.ev : null }];
  })), [s, rev0, margin0, claims, shares, fx, price]) as Record<Scen, (ReturnType<typeof project> & { equity: number; perShare: number | null; upside: number | null; tvShare: number | null }) | null>;

  const save = useMutation({
    mutationFn: () => api.saveScenario(a.id, { name: scName, assumptions: { ...s, anchors: { revenue: rev0, margin0, claims, shares, fx } },
      results: Object.fromEntries((["bear", "base", "bull"] as Scen[]).map((k) => [k, results[k] ? { perShare: results[k]!.perShare, upside: results[k]!.upside, tvShare: results[k]!.tvShare } : null])) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scenarios", a.id] }), onError: (e: Error) => alert(e.message),
  });

  if (!applicable) return <Note>DCF on operating cash flows is not meaningful for banks and insurers (deposits and float are not operating debt). A dividend-discount / excess-return model is planned for financials.</Note>;
  if (!x || !rev0 || !shares) return <Note tone="warn">Not enough historical anchors (revenue, operating income, diluted shares) to seed a DCF.</Note>;

  const cur = s[active];
  const set = (k: keyof Assump, val: number) => setS({ ...s, [active]: { ...cur, [k]: val } });
  const field = (k: keyof Assump, label: string, step = 0.005, help?: string, asMultiple = false) => asMultiple ? (
    <label className="flex items-center justify-between gap-3 text-sm" title={help}>
      <span className="text-ink2">{label}</span>
      <span className="flex items-center gap-1">
        <input type="number" step={0.1} min={0.2} className="input w-20 text-right tnum" value={+cur[k].toFixed(2)}
          onChange={(e) => set(k, Math.max(0.2, Number(e.target.value)))} aria-label={label} />
        <span className="text-xs text-muted">×</span>
      </span>
    </label>
  ) : (
    <label className="flex items-center justify-between gap-3 text-sm" title={help}>
      <span className="text-ink2">{label}</span>
      <span className="flex items-center gap-1">
        <input type="number" step={step * 100} className="input w-20 text-right tnum" value={+(cur[k] * 100).toFixed(2)}
          onChange={(e) => set(k, Number(e.target.value) / 100)} aria-label={label} />
        <span className="text-xs text-muted">%</span>
      </span>
    </label>
  );

  // sensitivity: WACC × terminal growth, base scenario
  const waccs = [-0.01, -0.005, 0, 0.005, 0.01].map((d) => s.base.wacc + d);
  const tgs = [-0.01, -0.005, 0, 0.005, 0.01].map((d) => s.base.tg + d);
  const sens = waccs.map((w) => tgs.map((g) => {
    if (g >= w) return null;
    const r = project({ ...s.base, wacc: w, tg: g }, rev0, margin0);
    return r.ev - claims > 0 ? ((r.ev - claims) / shares) * (fx ?? 1) : null;
  }));

  return (
    <div className="space-y-4">
      {peak ? <Note tone="warn">Current operating margin ({pct(margin0)}) is {pct(margin0 - normM, 0)} above its {histM.length}-year median ({pct(normM)}) —
        possibly a cyclical peak. The base case uses the median; the bull case keeps today&apos;s margin.</Note> : null}
      <Note>
        Historical anchors ({x.basis}, {x.currency}) seed year 0. Every forward input below is <b>your assumption</b>, not provider data.
        Per-share values convert {x.currency}→{a.currency} at {num(fx, 4)}. Saved scenarios are versioned (name, version, author) and never change provider data.
      </Note>
      <div className="grid gap-4 lg:grid-cols-[340px_1fr]">
        <div className="card space-y-2.5 p-4">
          <div className="flex gap-1" role="tablist" aria-label="Scenario">
            {(["bear", "base", "bull"] as Scen[]).map((k) => (
              <button key={k} role="tab" aria-selected={active === k} className={`chip flex-1 justify-center capitalize ${active === k ? "chip-on" : ""}`}
                onClick={() => setActive(k)}>{k}</button>))}
          </div>
          {field("growth", "Revenue growth (yrs 1–5)", 0.005, `Latest: ${pct(g0)} · FY median: ${histG.length ? pct(med(histG)) : "n/a"}`)}
          {field("margin", "Target operating margin (yr 5)", 0.005, `Current: ${pct(margin0)} · ${histM.length}-yr median: ${histM.length ? pct(normM) : "n/a"}`)}
          {field("tax", "Tax rate on EBIT")}
          {field("s2c", "Sales-to-capital", 0.1, `Revenue per unit of invested capital; current ${s2c0.toFixed(2)}×. Reinvestment = Δrevenue ÷ this`, true)}
          {field("wacc", "Discount rate (WACC)", 0.0025)}
          {field("tg", "Terminal growth", 0.0025)}
          {field("roicT", "Terminal ROIC", 0.005, `Return on new capital in perpetuity (current ROIC ${pct(roicNow)}); reinvestment rate = g / ROIC`)}
          {cur.tg >= cur.wacc ? <p className="text-xs text-down">Terminal growth must be below the discount rate.</p> : null}
          <div className="border-t border-line pt-2 text-xs text-muted">
            Net claims (debt − cash + minority + pref.): {compact(claims, x.currency)} · diluted shares {compact(shares)}
          </div>
          <div className="space-y-2 border-t border-line pt-3">
            <div className="flex gap-2">
              <input className="input flex-1" value={scName} onChange={(e) => setScName(e.target.value)} aria-label="Scenario name" />
              <button type="button" className="btn-primary" disabled={save.isPending || !scName.trim()} onClick={() => save.mutate()}>Save</button>
            </div>
            {(saved.data ?? []).slice(0, 5).map((v) => (
              <button key={v.id} type="button" className="flex w-full items-center justify-between rounded-md px-1 py-0.5 text-left text-xs text-ink2 hover:bg-page"
                title="Load these assumptions" onClick={() => { const as = v.assumptions as unknown as Record<Scen, Assump>; setS({ bear: as.bear, base: as.base, bull: as.bull }); setScName(v.name); }}>
                <span>{v.name} <span className="text-muted">v{v.version} · {v.author}</span></span><span className="text-muted">{ago(v.created_at)}</span>
              </button>))}
          </div>
        </div>

        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-3">
            {(["bear", "base", "bull"] as Scen[]).map((k) => {
              const r = results[k];
              return (
                <div key={k} className={`card px-4 py-3 ${active === k ? "ring-2 ring-accent" : ""}`}>
                  <div className="label capitalize">{k} case</div>
                  <div className="mt-1 text-xl font-semibold text-ink tnum">{r && isNum(r.perShare) ? `${num(r.perShare)} ${a.currency}` : "—"}</div>
                  <div className={`text-sm tnum ${r && isNum(r.upside) ? (r.upside >= 0 ? "text-up" : "text-down") : "text-muted"}`}>
                    {r && isNum(r.upside) ? `${pct(r.upside, 1, true)} vs ${num(price)}` : r ? "no positive equity value" : "invalid inputs (g ≥ WACC)"}</div>
                  <div className="mt-1 text-2xs text-muted">{r ? (r.perShare === null ? "Equity value ≤ 0 under these assumptions" : `Terminal value = ${pct(r.tvShare, 0)} of EV`) : ""}</div>
                </div>);
            })}
          </div>

          {results[active] ? (
            <div className="card overflow-x-auto p-4">
              <div className="mb-2 text-sm font-semibold text-ink">{active[0].toUpperCase() + active.slice(1)} case projection ({x.currency})</div>
              <table className="w-full text-sm tnum">
                <thead><tr className="text-2xs uppercase tracking-wide text-muted">
                  <th className="py-1 text-left font-semibold">Year</th><th className="text-right font-semibold">Revenue</th><th className="text-right font-semibold">Op. margin</th>
                  <th className="text-right font-semibold">EBIT</th><th className="text-right font-semibold" title="EBIT×(1−t) − Δrevenue ÷ sales-to-capital">FCFF</th><th className="text-right font-semibold">PV FCFF</th></tr></thead>
                <tbody>
                  <tr className="border-t border-line text-muted"><td className="py-1">0 (hist.)</td><td className="text-right">{compact(rev0)}</td><td className="text-right">{pct(margin0)}</td><td colSpan={3} /></tr>
                  {results[active]!.rows.map((r) => (
                    <tr key={r.t} className="border-t border-line"><td className="py-1">{r.t}</td><td className="text-right">{compact(r.rev)}</td>
                      <td className="text-right">{pct(r.m)}</td><td className="text-right">{compact(r.ebit)}</td><td className="text-right">{compact(r.fcff)}</td>
                      <td className="text-right">{compact(r.pvf)}</td></tr>))}
                  <tr className="border-t border-line"><td className="py-1" colSpan={5}>PV of terminal value</td><td className="text-right">{compact(results[active]!.pvTv)}</td></tr>
                  <tr className="border-t border-line font-semibold"><td className="py-1" colSpan={5}>Enterprise value → equity value</td>
                    <td className="text-right">{compact(results[active]!.ev)} → {compact(results[active]!.equity)}</td></tr>
                </tbody>
              </table>
            </div>) : null}

          <div className="card p-4">
            <div className="text-sm font-semibold text-ink">Sensitivity — base case value per share ({a.currency})</div>
            <p className="mb-2 text-xs text-muted">Rows: discount rate · columns: terminal growth · colour: implied up/downside vs {num(price)} (blue = above price, red = below)</p>
            <div className="overflow-x-auto">
              <table className="text-sm tnum">
                <thead><tr><th className="p-1.5 text-2xs text-muted">WACC \ g</th>{tgs.map((g) => <th key={g} className="p-1.5 text-2xs font-semibold text-muted">{pct(g, 2)}</th>)}</tr></thead>
                <tbody>
                  {waccs.map((w, i) => (
                    <tr key={w}><th className="p-1.5 text-right text-2xs font-semibold text-muted">{pct(w, 2)}</th>
                      {sens[i].map((v, j) => (
                        <td key={j} className={`min-w-[78px] border-2 border-surface p-1.5 text-center ${i === 2 && j === 2 ? "font-bold" : ""}`}
                          style={{ background: v ? divergingColor(v / price - 1) : "var(--grid)", color: v && Math.abs(v / price - 1) > 0.35 ? "#fff" : "var(--ink)" }}
                          title={v ? `${num(v)} ${a.currency} (${pct(v / price - 1, 1, true)})` : "g ≥ WACC: undefined"}>
                          {v ? num(v, v > 100 ? 0 : 2) : "—"}</td>))}
                    </tr>))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
