"""Price, return and risk analytics (spec §7 'Calculation conventions').

* Wealth index = provider split+dividend adjusted close (total-return proxy; adjustment labelled).
* Period return = end/start − 1; multi-year returns annualised over actual elapsed years.
* Volatility = sample stdev of daily simple returns × √252; needs 252 valid observations.
* Sharpe = mean(excess)/stdev(excess) × √252 with a currency-matched daily cash rate; suppressed otherwise.
* Only *completed* sessions are used: today's bar is dropped until the exchange close + 30 min.
* Missing observations are never filled with zero returns.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .fundamentals import M

EXCHANGE_CLOSE = {"US": ("America/New_York", time(16, 30)), "CA": ("America/Toronto", time(16, 30)),
                  "UK": ("Europe/London", time(17, 0))}
TD = 252


def bars_frame(prices: dict, region: str, scale: float) -> tuple[pd.DataFrame, list[str]]:
    flags: list[str] = []
    bars = prices.get("bars") or []
    df = pd.DataFrame(bars)  # accepts list-of-dicts or column-wise dict-of-lists
    if df.empty:
        return df, ["no price bars"]
    df["d"] = pd.to_datetime(df["d"])
    df = df.drop_duplicates("d", keep="last").set_index("d").sort_index()
    for col in ("o", "h", "l", "c", "ac"):
        if col in df:
            df[col] = df[col].astype(float) * scale
    df["v"] = df["v"].astype(float)
    tz, close_t = EXCHANGE_CLOSE.get(region, EXCHANGE_CLOSE["US"])
    now_local = datetime.now(ZoneInfo(tz))
    last = df.index[-1].date()
    if last == now_local.date() and now_local.time() < close_t:
        df = df.iloc[:-1]
        flags.append("Dropped today's partial session (exchange not yet closed)")
    r = df["ac"].pct_change()
    jumps = r[(r.abs() > 0.5)]
    if len(jumps):
        flags.append(f"{len(jumps)} daily move(s) > 50% — check corporate actions ({jumps.index[-1].date()})")
    zero_vol = int((df["v"].tail(60) == 0).sum())
    if zero_vol > 5:
        flags.append(f"{zero_vol} zero-volume sessions in last 60")
    return df, flags


def _ret_since(s: pd.Series, days: int) -> tuple[float | None, str]:
    if s.empty:
        return None, ""
    end_d = s.index[-1]
    start_target = end_d - timedelta(days=days)
    if s.index[0] > start_target + timedelta(days=5):
        return None, ""  # no padded pre-inception history
    start = s[:start_target]
    if start.empty:
        return None, ""
    return float(s.iloc[-1] / start.iloc[-1] - 1), f"{start.index[-1].date()} → {end_d.date()}"


def total_returns(ac: pd.Series) -> dict[str, dict]:
    out = {}
    for key, days, years in (("return_1m", 30, None), ("return_3m", 91, None), ("return_6m", 182, None),
                             ("return_1y", 365, None), ("return_3y_ann", 365 * 3, 3), ("return_5y_ann", 365 * 5, 5)):
        v, basis = _ret_since(ac, days)
        if v is None:
            out[key] = M(None, reason="Insufficient history")
            continue
        if years:
            start_d, end_d = basis.split(" → ")
            yrs = (pd.Timestamp(end_d) - pd.Timestamp(start_d)).days / 365.25
            v = (1 + v) ** (1 / yrs) - 1
        out[key] = M(v, f"total return {basis}" + (" annualised" if years else ""))
    ytd = ac[ac.index >= pd.Timestamp(ac.index[-1].year, 1, 1)]
    prev = ac[ac.index < pd.Timestamp(ac.index[-1].year, 1, 1)]
    out["return_ytd"] = M(float(ac.iloc[-1] / prev.iloc[-1] - 1), "YTD total return") if len(ytd) and len(prev) else M(None)
    return out


def risk_metrics(ac: pd.Series, cash_points: list[dict] | None, cash_label: str | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    rets = ac.pct_change().dropna()
    last_y = rets.tail(TD)
    if len(rets) >= TD:
        out["volatility_1y"] = M(float(last_y.std(ddof=1) * np.sqrt(TD)), "sample σ daily × √252, last 252 sessions")
    else:
        out["volatility_1y"] = M(None, reason=f"Needs 252 observations (have {len(rets)})")
    for key, n in (("max_drawdown_1y", TD), ("max_drawdown_3y", TD * 3), ("max_drawdown_5y", TD * 5)):
        w = ac.tail(n + 1)
        if len(ac) > n:  # full window only — no padded pre-inception history
            dd = w / w.cummax() - 1
            out[key] = M(float(dd.min()), f"peak-to-trough, {w.index[0].date()} → {w.index[-1].date()}")
        else:
            out[key] = M(None, reason="Insufficient history")
    # momentum 12-1: return from t-252 to t-21 sessions
    if len(ac) > TD:
        out["momentum_12_1"] = M(float(ac.iloc[-22] / ac.iloc[-TD - 1] - 1), "12-month total return excluding latest month",
                                 formula="momentum_12_1_v1")
    else:
        out["momentum_12_1"] = M(None, reason="Needs 13 months of history")
    # Sharpe
    if cash_points and len(last_y) >= TD:
        cs = pd.Series({pd.Timestamp(p["d"]): p["v"] for p in cash_points}).sort_index()
        cs = cs.reindex(cs.index.union(last_y.index)).ffill().reindex(last_y.index)
        if cs.isna().mean() < 0.05:
            rf = (1 + cs.bfill() / 100) ** (1 / TD) - 1
            ex = last_y - rf
            out["sharpe_1y"] = M(float(ex.mean() / ex.std(ddof=1) * np.sqrt(TD)),
                                 f"daily excess vs {cash_label}, annualised", formula="sharpe_v1")
        else:
            out["sharpe_1y"] = M(None, reason="Cash-rate series does not cover the window")
    else:
        out["sharpe_1y"] = M(None, reason="No currency-matched cash-rate series" if not cash_points else "Needs 252 observations")
    return out


def relative_metrics(ac: pd.Series, bench_ac: pd.Series | None, bench_label: str) -> dict[str, dict]:
    out = {}
    if bench_ac is None or bench_ac.empty:
        return {"relative_return_6m": M(None, reason="No benchmark"), "relative_return_1y": M(None, reason="No benchmark"),
                "beta_1y": M(None, reason="No benchmark")}
    idx = ac.index.intersection(bench_ac.index)  # intersection of dates only (spec §7)
    a, b = ac.reindex(idx), bench_ac.reindex(idx)
    for key, days in (("relative_return_6m", 182), ("relative_return_1y", 365)):
        ra, basis = _ret_since(a, days)
        rb, _ = _ret_since(b, days)
        out[key] = M(ra - rb, f"vs {bench_label} (ETF proxy), {basis}") if ra is not None and rb is not None else M(None, reason="Insufficient history")
    ra, rb = a.pct_change().dropna().tail(TD), b.pct_change().dropna().tail(TD)
    if len(ra) >= 200:
        out["beta_1y"] = M(float(np.cov(ra, rb, ddof=1)[0, 1] / rb.var(ddof=1)), f"vs {bench_label}, daily, 1y", unit="x")
    else:
        out["beta_1y"] = M(None, reason="Insufficient overlapping history")
    return out


def liquidity_and_volume(df: pd.DataFrame, fx_to_usd: float | None) -> dict[str, dict]:
    out = {}
    tv = (df["c"] * df["v"]).tail(60)
    if len(tv) >= 40 and fx_to_usd:
        out["median_traded_value_usd_60d"] = M(float(tv.median() * fx_to_usd), "60-session median close × volume", unit="USD")
    else:
        out["median_traded_value_usd_60d"] = M(None, reason="Insufficient volume history or FX")
    v = df["v"]
    if len(v) >= 21 and v.iloc[-21:-1].mean() > 0:
        out["abnormal_volume"] = M(float(v.iloc[-1] / v.iloc[-21:-1].mean()),
                                   f"session {df.index[-1].date()} vs prior 20-session mean", unit="x")
    else:
        out["abnormal_volume"] = M(None, reason="Insufficient volume history")
    hi, lo = df["c"].tail(TD).max(), df["c"].tail(TD).min()
    out["high_52w"], out["low_52w"] = M(float(hi), unit="price"), M(float(lo), unit="price")
    out["pct_from_52w_high"] = M(float(df["c"].iloc[-1] / hi - 1), "close vs 52-week high")
    return out


def dividend_yield(prices: dict, scale: float, price: float, last_date: pd.Timestamp) -> dict:
    cutoff = (last_date - timedelta(days=365)).date().isoformat()
    total = sum(d["amount"] for d in prices.get("dividends") or [] if d["d"] >= cutoff) * scale
    if total <= 0:
        return M(0.0, "no cash dividends in trailing 12 months")
    return M(total / price, "trailing 12m cash dividends / price")
