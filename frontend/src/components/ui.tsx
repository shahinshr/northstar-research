"use client";

import { AlertTriangle, Info } from "lucide-react";
import type { Metric } from "@/lib/api";
import { fmtMetric, isNum, type Fmt } from "@/lib/format";
import { METRICS } from "@/lib/metrics";

/** Score 0-100 as a thin bar + number. Null renders an explicit reason, never zero. */
export function ScoreBar({ score, reason, width = 72, label }: { score: number | null | undefined; reason?: string; width?: number; label?: string }) {
  if (!isNum(score)) {
    return <span className="text-xs text-muted" title={reason}>{reason ? "Not scored" : "—"}</span>;
  }
  return (
    <span className="inline-flex items-center gap-2" title={label ? `${label}: ${score.toFixed(1)} / 100 (peer percentile composite)` : undefined}>
      <span className="relative h-1.5 rounded-full bg-grid" style={{ width }} aria-hidden>
        <span className="absolute inset-y-0 left-0 rounded-full bg-accent" style={{ width: `${Math.max(2, score)}%` }} />
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
    <div className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${tone === "warn" ? "border-serious text-ink2" : "border-line text-ink2"}`}
      style={tone === "warn" ? { background: "color-mix(in srgb, var(--serious) 8%, transparent)" } : { background: "var(--page)" }}>
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

export function Stat({ label, value, sub, title }: { label: string; value: React.ReactNode; sub?: React.ReactNode; title?: string }) {
  return (
    <div className="card px-4 py-3" title={title}>
      <div className="label">{label}</div>
      <div className="mt-1 text-xl font-semibold text-ink">{value}</div>
      {sub ? <div className="mt-0.5 text-xs text-muted">{sub}</div> : null}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-lg bg-grid ${className}`} />;
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="card flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <div className="text-base font-semibold text-ink">{title}</div>
      {children ? <div className="max-w-lg text-sm text-ink2">{children}</div> : null}
    </div>
  );
}

export function RegionDot({ region }: { region: string }) {
  const c = region === "US" ? "var(--s1)" : region === "CA" ? "var(--s2)" : "var(--s3)";
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink2">
      <span className="inline-block h-2 w-2 rounded-full" style={{ background: c }} aria-hidden />{region}
    </span>
  );
}
