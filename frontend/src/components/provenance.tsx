"use client";

import { isNum } from "@/lib/format";

export function StatusTag({ s }: { s: string }) {
  const color = s === "valid" || s === "ok" ? "var(--good)" : s === "partial" ? "var(--warn)" : "var(--bad)";
  return <span className="inline-flex items-center gap-1.5 text-xs text-ink2"><span className="h-2 w-2 rounded-full" style={{ background: color }} aria-hidden />{s}</span>;
}

export function Trail({ attempts }: { attempts: { provider?: string; status?: string; error?: string; ms?: number; notes?: string[] }[] }) {
  return (
    <ol className="flex flex-wrap gap-1">
      {attempts.map((t, i) => t.notes ? (
        <li key={i} className="w-full text-2xs text-muted">Note: {t.notes.join("; ")}</li>
      ) : (
        <li key={i} title={t.error ?? ""} className="chip cursor-help py-0.5 text-2xs"
          style={{ borderColor: t.status === "ok" ? "var(--good)" : t.status === "partial" ? "var(--warn)" : t.status === "NOT_CONFIGURED" || t.status === "UNSUPPORTED" ? "var(--line)" : "var(--serious)" }}>
          {i + 1}. {t.provider}: {t.status === "ok" ? "✓" : t.status?.toLowerCase().replace("_", " ")}{isNum(t.ms) ? ` · ${t.ms}ms` : ""}
        </li>))}
    </ol>
  );
}

