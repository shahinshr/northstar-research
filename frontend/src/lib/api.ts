// Typed client for the FastAPI backend (proxied at /api by next.config.ts).

export type Status = "valid" | "nm" | "na";
export interface Metric { value: number | null; status: Status; basis?: string; reason?: string; unit?: string; formula?: string }
export interface Price { value: number; currency: string; date: string; change_1d: number | null; adjustment?: string; note?: string }
export interface MarketCap { value: number | null; currency: string; usd: number | null; presentation: number | null; presentation_currency: string; basis: string }
export interface Confidence { score: number; label: string; valid_inputs: number; total_inputs: number; note: string; unofficial_source: boolean;
  freshness: { price_sessions_old: number; price_stale: boolean; latest_statement: string | null; statement_stale: boolean; reporting_cadence: string } }
export interface Eligibility { core: boolean; model_family: string; reasons: string[] }

export interface Row {
  id: string; symbol: string; name: string | null; region: string; exchange: string | null; currency: string;
  sector: string | null; industry: string | null; asset_type: string; status: string;
  price: Price | null; market_cap: MarketCap | null;
  core: { score: number | null; rank?: number; status: string; coverage?: number; reasons?: string[]; model?: string; label?: string };
  estimate: { score: number | null; rank?: number; status: string };
  attention: { score: number | null; model?: string; status: string };
  components: Record<string, number | null>;
  metrics: Record<string, { value: number | null; status: Status }>;
  confidence: Confidence | null; eligibility: Eligibility | null; flags: string[]; watch: boolean;
  asset_class?: "stock" | "fund"; discovery?: string[]; indices?: string[]; live?: LiveQuote | null; region_group?: string;
  core_label?: string;
  // funds
  cohort?: string; fund_asset_class?: string; style?: string; family?: string; hedged?: boolean; index?: string | null; fund_model?: string | null;
}

export interface Manifest {
  evaluated: number; ok: number; failed: number; eligible_core: number; excluded: number; scored_core: number;
  peer_min_size: number; peer_note: string; attention_model: string; news_coverage: number;
  fx: { provider: string; as_of: string; USDCAD: number; GBPCAD: number; EURCAD: number };
  cash_rates: Record<string, { source: string; latest: { d: string; v: number } }>;
  benchmarks: Record<string, string>; shortlist_threshold: number; cohort_by_region: Record<string, number>;
  stocks?: number; funds?: number; discovery_lists?: Record<string, number>; peer_levels_used?: Record<string, number>;
  fund_models?: Record<string, number>; indices?: string[];
}

export interface Job {
  job_id: string; kind: string; status: "queued" | "fetching" | "calculating" | "completed" | "partial" | "failed";
  progress: { requested?: number; done?: number; updated?: number; unchanged?: number; failed?: number; skipped?: number; current?: string | null };
  message: string | null; created_at: string; updated_at: string; run_id: string | null; deduplicated?: boolean;
}

export interface Dashboard {
  run: { id: string; created_at: string; manifest: Manifest; model_versions: Record<string, unknown> } | null;
  rows: Row[];
  changes: { entrants: { id: string; symbol: string; score: number }[]; exits: { id: string; symbol: string; score: number | null }[];
    movers: { id: string; symbol: string; from: number; to: number; delta: number }[]; compared_to: { run_id: string; created_at: string } | null } | null;
  active_job: Job | null; health?: { stale_prices: number; failed: number; providers_configured: string[] }; unread_alerts?: number;
  presentation_currency?: string; state: string;
}

export interface ScoreInput { metric: string; planned_weight: number; effective_weight: number; direction: string; raw: number | null;
  status: Status; reason?: string; basis?: string; percentile: number | null; peer_level: string | null; peer_n: number | null }
export interface ModelScore { model: string; label?: string; score: number | null; rank?: number; coverage?: number; status: string; reasons?: string[];
  components?: Record<string, { score: number | null; weight: number; coverage: number; inputs: ScoreInput[] }>;
  inputs?: { metric: string; weight: number; percentile: number }[] }
export interface SourceInfo { provider: string | null; route: string | null; status: string; fetched_at: string | null;
  attempts: { provider?: string; status?: string; error?: string; ms?: number; notes?: string[] }[] | null }
export interface NewsItem { title: string; url: string; source: string | null; published_at: string | number | null; summary: string | null; match?: "headline" | "mention" }
export interface EstimatePeriod { period: string; end_date: string | null; eps_avg: number | null; eps_low: number | null; eps_high: number | null;
  eps_analysts: number | null; eps_year_ago?: number | null; eps_growth?: number | null; revenue_avg?: number | null; revenue_low?: number | null;
  revenue_high?: number | null; revenue_analysts?: number | null; revenue_growth?: number | null;
  eps_trend?: { current: number | null; d7: number | null; d30: number | null; d60: number | null; d90: number | null } }
export interface Anchors { currency: string; revenue: number | null; operating_income: number | null; ebit: number | null; d_and_a: number | null;
  capex: number | null; ocf: number | null; net_income: number | null; income_tax: number | null; pretax_income: number | null;
  total_debt: number | null; cash: number | null; minority_interest: number | null; preferred_equity: number | null;
  diluted_shares: number | null; basis: string; balance_date: string; fx_fin_to_px: number | null;
  fy_margins?: number[]; fy_growth?: number[]; invested_capital?: number | null }

export interface Analysis {
  id: string; symbol: string; name: string; asset_type: string; region: string; exchange: string; sector: string; industry: string;
  country: string; currency: string; quote_currency_raw: string; financial_currency: string | null; description: string | null;
  website: string | null; employees: number | null; status: string; error?: string;
  price: Price; market_cap: MarketCap; metrics: Record<string, Metric>; scores: { core?: ModelScore; estimate?: ModelScore; attention?: ModelScore };
  eligibility: Eligibility; confidence: Confidence; flags: string[]; sources: Record<string, SourceInfo | null>;
  estimates: { periods: EstimatePeriod[]; basis?: string }; targets: { available: boolean; high?: number | null; low?: number | null;
    mean?: number | null; median?: number | null; analysts?: number | null; dispersion?: number; note?: string };
  news: NewsItem[]; anchors: Anchors | null; fx_fin_to_px: number | null; watch: boolean;
  asset_class?: "stock" | "fund"; discovery?: string[]; indices?: string[];
  // funds
  cohort?: string; fund_asset_class?: string; style?: string; family?: string; category?: string; hedged?: boolean; index?: string | null;
  fund_model?: string | null;
  holdings?: { complete: boolean; as_of: string | null; covered_weight: number | null; count: number; top: Holding[] };
  exposure?: { sectors: Record<string, number>; asset_classes: Record<string, number>; bond: { duration?: number; maturity?: number; ratings?: Record<string, number> };
    vendor_valuation: Record<string, number | null> };
  run: { id: string; created_at: string; manifest: Manifest };
}

export interface Holding { symbol: string; name: string | null; weight: number; sector?: string | null; asset_class?: string | null; location?: string | null }
export interface Alert { id: string; kind: string; severity: string; message: string; instrument_id: string | null; created_at: string; read: boolean }
export interface Settings { universe: "starter" | "core" | "global" | "extended"; discovery_time: string; timezone: string;
  presentation_currency: string; intraday_minutes: number }
export interface Overlap { symbols: string[]; cells: { a: string; b: string; overlap: number | null; common: number | null; exact: boolean }[];
  complete: Record<string, boolean>; note: string }
export interface Tracking { available: boolean; reason?: string; index?: string; currency?: string; hedged?: boolean;
  series?: { d: string; fund: number; index: number; gap: number }[] }
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Validation = { report: any | null; generated_at?: string };

export interface AppStatus { run_id: string | null; run_at: string | null; run_kind: string | null; active_job: Job | null;
  markets_open: string[]; intraday_minutes: number }
export interface LiveQuote { price: number; prev_close: number; change: number | null; date: string; provisional: boolean;
  market_open: boolean; note: string; currency?: string; fetched_at?: string | null }
export interface StreamItem extends NewsItem { severity: "critical" | "high" | "normal"; tags: string[]; polarity: "positive" | "negative" | "neutral";
  symbol?: string; instrument_id?: string; watch?: boolean }
export interface Live { quote: LiveQuote | null; news: StreamItem[]; news_source: { provider: string; fetched_at: string; status: string } | null;
  refreshing: boolean; social: { x_live: string; stocktwits: string; note: string } }

export interface Period { period_end: string; fiscal_period: string; filing_date: string | null; fields: Record<string, number> }
export interface Financials { provider: string; route: string; fetched_at: string; status: string; financial_currency: string; annual: Period[]; quarterly: Period[] }
export interface PriceBars { currency: string; provider: string; adjustment: string; bars: { d: string; c: number; ac: number; v: number; dd: number }[] }

export interface Comparison { currency: string; window: string; items: (Row & { scores: Analysis["scores"]; financial_currency: string })[];
  series: Record<string, number | string>[]; period_stats: Record<string, { return_local: number; return_converted: number | null }>;
  dates: { start: string; end: string; observations: number }; note: string }

export interface ProviderCap { id: string; name: string; configured: boolean; requires_key: boolean; signup_url: string; markets: string[]; datasets: string[]; calls_today?: number }
export interface DataHealth { providers: ProviderCap[]; chains: Record<string, string[]>;
  macro: Record<string, { provider: string; status: string; fetched_at: string; attempts: SourceInfo["attempts"] } | null>;
  matrix: { id: string; symbol: string; asset_type: string; datasets: Record<string, { provider: string | null; status: string; fetched_at: string; attempts: SourceInfo["attempts"] } | null> }[];
  universe?: { mode: string | null; notes: string[]; fetched_at: string | null; stocks: number; funds: number } }

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api/v1${path}`, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) }, cache: "no-store" });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail ?? msg; } catch { /* ignore */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json() as Promise<T>;
}

export const api = {
  status: () => req<AppStatus>("/status"),
  live: (id: string) => req<Live>(`/instruments/${id}/live`),
  pulse: () => req<{ items: StreamItem[]; scope: string }>("/pulse"),
  refreshQuotes: () => req<Job>("/quote-refreshes?mode=force", { method: "POST" }),
  dashboard: (asset: "stock" | "fund" = "stock") => req<Dashboard>(`/dashboard?asset=${asset}`),
  holdings: (id: string) => req<{ provider: string; route: string; fetched_at: string; complete: boolean; as_of: string | null; holdings: Holding[] }>(`/instruments/${id}/holdings`),
  tracking: (id: string, years = 3) => req<Tracking>(`/instruments/${id}/tracking?years=${years}`),
  overlap: (ids: string[]) => req<Overlap>("/overlap", { method: "POST", body: JSON.stringify({ instrument_ids: ids }) }),
  alerts: () => req<Alert[]>("/alerts"),
  readAlerts: () => req("/alerts/read-all", { method: "POST" }),
  settings: () => req<Settings>("/settings"),
  saveSettings: (b: Partial<Settings>) => req<Settings>("/settings", { method: "PUT", body: JSON.stringify(b) }),
  validation: () => req<Validation>("/validation"),
  runValidation: () => req<Job>("/validation-runs", { method: "POST" }),
  search: (q: string) => req<{ id: string; symbol: string; name: string | null; asset_type: string; exchange: string | null }[]>(`/instruments?q=${encodeURIComponent(q)}&limit=12`),
  analysis: (id: string) => req<Analysis>(`/instruments/${id}/analysis`),
  prices: (id: string, range: string) => req<PriceBars>(`/instruments/${id}/prices?range=${range}`),
  financials: (id: string) => req<Financials>(`/instruments/${id}/financials`),
  compare: (ids: string[], currency: string, window: string) =>
    req<Comparison>("/comparisons", { method: "POST", body: JSON.stringify({ instrument_ids: ids, currency, window }) }),
  job: (id: string) => req<Job>(`/jobs/${id}`),
  jobs: () => req<Job[]>("/jobs"),
  refresh: (body: { scope: "selection" | "universe"; instrument_ids?: string[]; kind: "refresh_prices" | "refresh_analysis"; mode?: "if_stale" | "force" }) =>
    req<Job>("/refreshes", { method: "POST", body: JSON.stringify(body) }),
  discovery: (mode: "if_stale" | "force" = "if_stale") => req<Job>(`/discovery-runs?mode=${mode}`, { method: "POST" }),
  recalc: () => req<Job>("/recalculate", { method: "POST" }),
  addInstrument: (symbol: string) => req<Job & { instrument_id: string }>("/instruments", { method: "POST", body: JSON.stringify({ symbol }) }),
  removeInstrument: (id: string) => req<{ ok: boolean }>(`/instruments/${id}`, { method: "DELETE" }),
  watch: (id: string, on: boolean) => req<{ ok: boolean }>(`/watchlist/${id}`, { method: on ? "PUT" : "DELETE" }),
  notes: (id: string) => req<{ id: string; body: string; created_at: string }[]>(`/instruments/${id}/notes`),
  addNote: (id: string, body: string) => req(`/instruments/${id}/notes`, { method: "POST", body: JSON.stringify({ body }) }),
  deleteNote: (nid: string) => req(`/notes/${nid}`, { method: "DELETE" }),
  health: () => req<DataHealth>("/data-health"),
  scenarios: (id: string) => req<{ id: string; name: string; version: number; author: string; assumptions: Record<string, unknown>;
    results: Record<string, unknown> | null; created_at: string }[]>(`/instruments/${id}/scenarios`),
  saveScenario: (id: string, body: { name: string; assumptions: Record<string, unknown>; results?: Record<string, unknown> }) =>
    req<{ id: string; version: number }>(`/instruments/${id}/scenarios`, { method: "POST", body: JSON.stringify(body) }),
};
