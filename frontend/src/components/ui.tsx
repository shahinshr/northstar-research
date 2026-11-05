"use client";

import { AlertTriangle, Info } from "lucide-react";
import type { LiveQuote, Metric } from "@/lib/api";
import { fmtMetric, isNum, num, pct, type Fmt } from "@/lib/format";
import { METRICS } from "@/lib/metrics";

/** Score 0-100 as a thin bar + number. Null renders an explicit reason, never zero. */
export function ScoreBar({ score, reason, width = 72, label }: { score: number | null | undefined; reason?: string; width?: number; label?: string }) {
  if (!isNum(score)) {
    return <span className="text-xs text-muted" title={reason}>{reason ? "Not scored" : "—"}</span>;
  }
  return (
    <span className="inline-flex items-center gap-2" title={label ? `${label}: ${score.toFixed(1)} / 100 (peer percentile composite)` : undefined}>
      <span className="relative h-1.5 rounded-full bg-grid" style={{ width }} aria-hidden>
        <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${Math.max(2, score)}%`, background: "linear-gradient(90deg, var(--accent), var(--accent-2))" }} />
      </span>
      <span className="tnum text-sm font-semibold text-ink">{score.toFixed(1)}</span>
    </span>
  );
}

/** A metric value that explains itself on hover: basis when valid, reason when N/A / N/M. */
export function MetricValue({ k, m, fmt, className = "" }: { k: string; m: Pick<Metric, "value" | "status"> & Partial<Metric> | undefined; fmt?: Fmt; className?: string }) {
  const meta = METRICS[k];
  const text = fmtMetric(m, fmt ?? meta?.fmt ?? "num");
  const valid = m?.status === "valid" && isNum(m.value);
  const signed = ((fmt ?? meta?.fmt) === "pctSigned" || (fmt ?? meta?.fmt) === "pct2Signed") && valid;
  const tone = !valid ? "text-muted" : signed ? ((m!.value as number) >= 0 ? "text-up" : "text-down") : "text-ink";
  const tip = [meta?.label, valid ? m?.basis : m?.reason ?? (m?.status === "nm" ? "Not meaningful" : "Not available")].filter(Boolean).join(" — ");
  return <span className={`tnum ${tone} ${className}`} title={tip}>{text}</span>;
}

export function Flags({ flags }: { flags: string[] }) {
  if (!flags?.length) return null;
  return (
    <ul className="space-y-1">
      {flags.map((f) => (
        <li key={f} className="flex items-start gap-2 text-xs text-ink2">
          <AlertTriangle size={13} className="mt-0.5 shrink-0 text-serious" aria-hidden /> <span>{f}</span>
        </li>
      ))}
    </ul>
  );
}

export function Note({ children, tone = "info" }: { children: React.ReactNode; tone?: "info" | "warn" }) {
  return (
    <div className={`flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-xs leading-relaxed ${tone === "warn" ? "text-ink2" : "border-line text-ink2"}`}
      style={tone === "warn" ? { background: "color-mix(in srgb, var(--serious) 8%, var(--surface))", borderColor: "color-mix(in srgb, var(--serious) 45%, var(--line))" }
        : { background: "color-mix(in srgb, var(--accent) 5%, var(--surface))" }}>
      {tone === "warn" ? <AlertTriangle size={14} className="mt-0.5 shrink-0 text-serious" aria-hidden /> : <Info size={14} className="mt-0.5 shrink-0 text-accent" aria-hidden />}
      <div>{children}</div>
    </div>
  );
}

export function Confidence({ c }: { c: { score: number; label: string; note?: string; valid_inputs?: number; total_inputs?: number } | null | undefined }) {
  if (!c) return <span className="text-muted">—</span>;
  const color = c.label === "High" ? "var(--good)" : c.label === "Medium" ? "var(--warn)" : "var(--serious)";
  return (
    <span className="inline-flex items-center gap-1.5 text-xs" title={`${c.valid_inputs}/${c.total_inputs} key inputs valid. ${c.note ?? ""}`}>
      <span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} aria-hidden />
      <span className="text-ink2">{c.label}</span>
    </span>
  );
}

export function Stat({ label, value, sub, title, icon }: { label: string; value: React.ReactNode; sub?: React.ReactNode; title?: string; icon?: React.ReactNode }) {
  return (
    <div className="card card-hover relative overflow-hidden px-4 py-3.5" title={title}>
      <div className="flex items-center gap-2">
        {icon ? <span className="grid h-6 w-6 place-items-center rounded-lg text-accent" style={{ background: "color-mix(in srgb, var(--accent) 12%, var(--surface))" }}>{icon}</span> : null}
        <div className="label">{label}</div>
      </div>
      <div className="mt-1.5 text-[22px] font-semibold leading-tight tracking-tight text-ink">{value}</div>
      {sub ? <div className="mt-1 text-xs text-muted">{sub}</div> : null}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`shimmer rounded-xl ${className}`} />;
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="card flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <div className="text-base font-semibold text-ink">{title}</div>
      {children ? <div className="max-w-lg text-sm text-ink2">{children}</div> : null}
    </div>
  );
}

const GROUP_COLOR: Record<string, string> = { Americas: "var(--s1)", Europe: "var(--s3)", "Asia-Pacific": "var(--s2)" };
const REGION_GROUP: Record<string, string> = { US: "Americas", CA: "Americas", BR: "Americas", MX: "Americas", UK: "Europe", EU: "Europe", CH: "Europe", ZA: "Europe" };
export const regionGroup = (r: string) => REGION_GROUP[r] ?? "Asia-Pacific";
export const groupColor = (g: string) => GROUP_COLOR[g] ?? "var(--s2)";

export function RegionDot({ region }: { region: string }) {
  const g = regionGroup(region);
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink2" title={g}>
      <span className="inline-block h-2 w-2 rounded-full" style={{ background: groupColor(g) }} aria-hidden />{region}
    </span>
  );
}

/** Official close, or a provisional intraday quote (delayed; never used in scores) with a live dot. */
export function LivePrice({ official, currency, live, size = "sm" }: { official: number | null | undefined; currency: string; live?: LiveQuote | null; size?: "sm" | "lg" }) {
  const isLive = !!live?.provisional;
  const v = isLive ? live!.price : official;
  const ch = isLive ? live!.change : undefined;
  if (size === "lg") {
    return (
      <span className="inline-flex items-baseline gap-2">
        {isLive ? <span className="live-dot self-center" title={live!.note} aria-label="Live intraday quote" /> : null}
        <span className="tnum text-3xl font-semibold tracking-tight text-ink">{num(v)}</span>
        <span className="text-base font-normal text-muted">{currency}</span>
      </span>);
  }
  return (
    <span className="tnum inline-flex items-center gap-1" title={isLive ? live!.note : "Last completed session close"}>
      {isLive ? <span className="live-dot" style={{ width: 6, height: 6 }} aria-hidden /> : null}
      {num(v)} <span className="text-2xs text-muted">{currency}</span>
      {isNum(ch) ? <span className={`text-2xs ${ch >= 0 ? "text-up" : "text-down"}`}>{pct(ch, 1, true)}</span> : null}
    </span>
  );
}
