"""Canonical (provider-independent) payload shapes + shared normalisation helpers.

profile        {symbol,name,exchange,quote_currency,financial_currency,sector,industry,country,
                quote_type,shares_outstanding,market_cap,description,website,isin}
prices         {quote_currency, adjustment, bars:[{d,o,h,l,c,ac,v}], dividends:[{d,amount}], splits:[{d,ratio}]}
               c = raw close, ac = split+dividend adjusted close (total-return proxy)
financials     {financial_currency, annual:[Period], quarterly:[Period]}
               Period = {period_end, fiscal_period, filing_date, fields:{FIELD: number}}
estimates      {periods:[{period,end_date,eps_avg,eps_low,eps_high,eps_analysts,revenue_avg,
                revenue_analysts,eps_trend:{current,d7,d30,d60,d90}}]}
price_targets  {quote_currency,high,low,mean,median,analysts}
news           {items:[{title,url,source,published_at,summary}]}

Quote currency is stored exactly as the vendor quotes it (e.g. "GBp"); `normalise_quote_currency`
converts minor units before any valuation (spec §14: "Normalise UK GBX quotes to GBP").
"""
from __future__ import annotations

import math
from typing import Any

# Canonical financial-statement fields. Flows are summed for TTM; balance items are point-in-time.
FLOW_FIELDS = [
    "revenue", "gross_profit", "operating_income", "ebit", "ebitda", "net_income", "eps_diluted",
    "interest_expense", "pretax_income", "income_tax", "d_and_a", "operating_cash_flow", "capex",
    "sbc", "dividends_paid",
]
POINT_FIELDS = [
    "diluted_shares", "total_debt", "cash", "cash_and_st_investments", "total_equity",
    "minority_interest", "preferred_equity", "total_assets", "lease_liabilities",
]
ALL_FIELDS = FLOW_FIELDS + POINT_FIELDS

# minor-unit currency codes -> (major code, multiplier)
MINOR_UNITS = {"GBp": ("GBP", 0.01), "GBX": ("GBP", 0.01), "ZAc": ("ZAR", 0.01), "ILA": ("ILS", 0.01)}


def normalise_quote_currency(code: str | None) -> tuple[str | None, float]:
    if not code:
        return None, 1.0
    if code in MINOR_UNITS:
        return MINOR_UNITS[code]
    return code.upper(), 1.0


def num(x: Any) -> float | None:
    """Coerce vendor numerics; None/NaN/'None'/'' -> None (never 0)."""
    if x is None:
        return None
    if isinstance(x, str):
        x = x.strip().replace(",", "")
        if x in ("", "None", "null", "NaN", "-", "N/A"):
            return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def clean_fields(d: dict[str, Any]) -> dict[str, float]:
    out = {}
    for k, v in d.items():
        n = num(v)
        if n is not None:
            out[k] = n
    # capex and dividends are stored as positive outflows regardless of vendor sign convention
    for k in ("capex", "dividends_paid", "interest_expense"):
        if k in out:
            out[k] = abs(out[k])
    return out


def region_of(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith(".TO") or s.endswith(".V") or s.endswith(".NE") or s.endswith(".CN"):
        return "CA"
    if s.endswith(".L") or s.endswith(".IL"):
        return "UK"
    return "US"


def split_symbol(symbol: str) -> tuple[str, str]:
    """'RY.TO' -> ('RY','TO'); 'AAPL' -> ('AAPL','US'); 'BRK-B' -> ('BRK-B','US')."""
    if "." in symbol:
        base, suffix = symbol.rsplit(".", 1)
        return base, suffix.upper()
    return symbol, "US"
