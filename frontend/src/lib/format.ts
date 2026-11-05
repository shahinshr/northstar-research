import type { Metric, Status } from "./api";

export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function pct(v: number | null | undefined, dp = 1, sign = false): string {
  if (!isNum(v)) return "—";
  const s = (v * 100).toFixed(dp);
  return `${sign && v > 0 ? "+" : ""}${s}%`;
}

export function num(v: number | null | undefined, dp = 2): string {
  if (!isNum(v)) return "—";
  return v.toLocaleString("en-CA", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

export function mult(v: number | null | undefined, dp = 1): string {
  return isNum(v) ? `${v.toFixed(dp)}×` : "—";
}

export function compact(v: number | null | undefined, ccy?: string): string {
  if (!isNum(v)) return "—";
  const a = Math.abs(v);
  const [d, s] = a >= 1e12 ? [1e12, "T"] : a >= 1e9 ? [1e9, "B"] : a >= 1e6 ? [1e6, "M"] : a >= 1e3 ? [1e3, "K"] : [1, ""];
  return `${ccy ? ccy + " " : ""}${(v / d).toFixed(a / d >= 100 ? 0 : 1)}${s}`;
}

export function money(v: number | null | undefined, ccy: string, dp = 2): string {
  return isNum(v) ? `${num(v, dp)} ${ccy}` : "—";
}

export function dateFmt(s: string | null | undefined, withTime = false): string {
  if (!s) return "—";
  const d = new Date(s.length === 10 ? `${s}T12:00:00` : s.endsWith("Z") || s.includes("+") ? s : `${s}Z`);
  if (isNaN(d.getTime())) return s;
  return d.toLocaleString("en-CA", { year: "numeric", month: "short", day: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}) });
}

export function ago(s: string | null | undefined): string {
  if (!s) return "never";
  const d = new Date(s.endsWith("Z") || s.includes("+") ? s : `${s}Z`);
  const m = Math.round((Date.now() - d.getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  if (m < 60 * 24) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} d ago`;
}

/** Placeholder text for an unavailable metric: N/M (not meaningful) vs N/A (missing/inapplicable). */
export function unavailable(status: Status | undefined): string {
  return status === "nm" ? "N/M" : "N/A";
}

export type Fmt = "pct" | "pct2" | "pctSigned" | "pct2Signed" | "mult" | "num" | "ratio" | "usd" | "int" | "years";
export function fmtMetric(m: Pick<Metric, "value" | "status"> | undefined, f: Fmt): string {
  if (!m || m.status !== "valid" || !isNum(m.value)) return unavailable(m?.status);
  switch (f) {
    case "pct": return pct(m.value);
    case "pct2": return pct(m.value, 2);
    case "pct2Signed": return pct(m.value, 2, true);
    case "usd": return compact(m.value, "USD");
    case "int": return Math.round(m.value).toLocaleString("en-CA");
    case "years": return `${m.value.toFixed(1)} yrs`;
    case "pctSigned": return pct(m.value, 1, true);
    case "mult": return mult(m.value);
    case "ratio": return num(m.value, 2);
    default: return num(m.value, 2);
  }
}

export function scoreBand(s: number | null | undefined): { label: string; tone: string } {
  if (!isNum(s)) return { label: "—", tone: "text-muted" };
  if (s >= 70) return { label: "Top tier", tone: "text-up" };
  if (s >= 55) return { label: "Above peers", tone: "text-accent-ink" };
  if (s >= 45) return { label: "In line", tone: "text-ink2" };
  return { label: "Below peers", tone: "text-down" };
}
