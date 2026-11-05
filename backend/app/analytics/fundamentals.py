"""Stock fundamental metrics (spec §5 table, §14 normalisation).

Conventions (formula_version suffix _v1):
* TTM = sum of the latest 4 consecutive discrete quarters (span 250-300 days) per field; otherwise the
  latest fiscal year is used and labelled "FY". Balance items are point-in-time, never summed.
* Statement values are converted from reporting currency to the (normalised) trading currency at the
  latest reference FX before being divided by market values.
* Every metric is a dict {value, status, basis, reason?} with status in valid | nm | na.
  nm = not meaningful (e.g. negative denominator); na = input missing.
"""
from __future__ import annotations

from datetime import date
from typing import Any


def M(value: float | None, basis: str = "", status: str | None = None, reason: str | None = None,
      unit: str = "ratio", formula: str = "") -> dict[str, Any]:
    st = status or ("valid" if value is not None else "na")
    d = {"value": value, "status": st, "basis": basis, "unit": unit}
    if reason:
        d["reason"] = reason
    if formula:
        d["formula"] = formula
    return d


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


class Statements:
    def __init__(self, fin: dict):
        self.currency = fin.get("financial_currency")
        self.annual = sorted(fin.get("annual") or [], key=lambda p: p["period_end"], reverse=True)
        self.quarterly = sorted(fin.get("quarterly") or [], key=lambda p: p["period_end"], reverse=True)
        self.derived_eps = False
        # Vendors sometimes omit diluted EPS for a period: derive NI / diluted weighted shares (labelled)
        for p in self.annual + self.quarterly:
            f = p["fields"]
            if "eps_diluted" not in f and f.get("net_income") is not None and f.get("diluted_shares"):
                p["fields"] = {**f, "eps_diluted": f["net_income"] / f["diluted_shares"]}
                self.derived_eps = True

    # ---------- flows
    def _ttm_from(self, quarters: list[dict], field: str) -> tuple[float | None, str]:
        if len(quarters) < 4:
            return None, ""
        q = quarters[:4]
        span = (_d(q[0]["period_end"]) - _d(q[3]["period_end"])).days
        if not 250 <= span <= 300 or any(field not in x["fields"] for x in q):
            return None, ""
        return sum(x["fields"][field] for x in q), f"TTM to {q[0]['period_end']}"

    def ttm(self, field: str, offset_years: int = 0) -> tuple[float | None, str]:
        """offset_years=0 -> latest TTM; 1 -> TTM one year earlier. Falls back to FY."""
        qs = self.quarterly[4 * offset_years:]
        newest_q = self.quarterly[0]["period_end"] if self.quarterly else ""
        newest_a = self.annual[0]["period_end"] if self.annual else ""
        if newest_q >= newest_a:
            v, basis = self._ttm_from(qs, field)
            if v is not None:
                return v, basis
        if len(self.annual) > offset_years and field in self.annual[offset_years]["fields"]:
            p = self.annual[offset_years]
            return p["fields"][field], f"FY {p['period_end']}"
        return None, ""

    def growth_pair(self, field: str) -> tuple[float | None, float | None, str]:
        """Latest vs one-year-earlier on the SAME basis (TTM vs TTM, else FY vs FY)."""
        cur, b0 = self.ttm(field, 0)
        prev, b1 = self.ttm(field, 1)
        if cur is not None and prev is not None and b0[:3] == b1[:3]:
            return cur, prev, f"{b0} vs {b1}"
        if len(self.annual) >= 2 and all(field in self.annual[i]["fields"] for i in (0, 1)):
            a0, a1 = self.annual[0], self.annual[1]
            return a0["fields"][field], a1["fields"][field], f"FY {a0['period_end']} vs FY {a1['period_end']}"
        return None, None, ""

    # ---------- balance
    def _all_points(self) -> list[dict]:
        return sorted(self.quarterly + self.annual, key=lambda p: p["period_end"], reverse=True)

    def point(self, field: str) -> tuple[float | None, str]:
        for p in self._all_points():
            if field in p["fields"]:
                return p["fields"][field], p["period_end"]
        return None, ""

    def point_year_ago(self, field: str, ref: str) -> float | None:
        if not ref:
            return None
        target = _d(ref).toordinal() - 365
        best = None
        for p in self._all_points():
            if field in p["fields"]:
                gap = abs(_d(p["period_end"]).toordinal() - target)
                if gap <= 45 and (best is None or gap < best[0]):
                    best = (gap, p["fields"][field])
        return best[1] if best else None

    def latest_period_end(self) -> str | None:
        pts = self._all_points()
        return pts[0]["period_end"] if pts else None


def _ratio(n: float | None, d: float | None) -> float | None:
    if n is None or d is None or d == 0:
        return None
    return n / d


def compute_stock_fundamentals(fin: dict, price: float | None, market_cap: float | None,
                               fx_fin_to_px: float | None) -> dict[str, dict]:
    st = Statements(fin)
    m: dict[str, dict] = {}
    fx = fx_fin_to_px

    def conv(v):
        return None if v is None or fx is None else v * fx

    rev, rev_b = st.ttm("revenue")
    ni, ni_b = st.ttm("net_income")
    ocf, ocf_b = st.ttm("operating_cash_flow")
    capex, _ = st.ttm("capex")
    ebitda, ebitda_b = st.ttm("ebitda")
    op_inc, op_b = st.ttm("operating_income")
    ebit, ebit_b = st.ttm("ebit")
    ebit = ebit if ebit is not None else op_inc
    ebit_b = ebit_b or op_b
    intexp, _ = st.ttm("interest_expense")
    tax, _ = st.ttm("income_tax")
    pretax, _ = st.ttm("pretax_income")
    sbc, _ = st.ttm("sbc")
    divs, _ = st.ttm("dividends_paid")
    gp, _ = st.ttm("gross_profit")
    eps, eps_b = st.ttm("eps_diluted")
    debt, bs_date = st.point("total_debt")
    cash_st, _ = st.point("cash_and_st_investments")
    cash, _ = st.point("cash")
    cash_st = cash_st if cash_st is not None else cash
    equity, eq_date = st.point("total_equity")
    minority, _ = st.point("minority_interest")
    pref, _ = st.point("preferred_equity")
    shares_dil, sh_date = st.point("diluted_shares")
    if eps is None and ni is not None and shares_dil:
        eps, eps_b = ni / shares_dil, f"{ni_b} (net income / diluted shares)"
    elif st.derived_eps:
        eps_b += " (EPS partly derived: net income / diluted shares)"

    fx_note = "" if fx in (None, 1.0) else f"; statements converted {st.currency}→trading ccy @ {fx:.4f}"
    if fx is None:
        for k in ("fcf_yield", "earnings_yield", "pe", "ev_ebitda", "ebitda_to_ev"):
            m[k] = M(None, reason=f"No FX rate for reporting currency {st.currency}")

    # ---------- value
    if fx is not None:
        fcf = (ocf - (capex or 0)) if ocf is not None else None
        if fcf is not None and market_cap:
            m["fcf_yield"] = M(conv(fcf) / market_cap, f"{ocf_b} OCF − capex / market cap{fx_note}",
                               formula="fcf_yield_v1")
        else:
            m["fcf_yield"] = M(None, reason="Operating cash flow or market value missing")
        m["fcf_ttm"] = M(fcf, ocf_b, unit=f"{st.currency}")
        eps_px = conv(eps)
        if eps_px is not None and price:
            ey = eps_px / price
            m["earnings_yield"] = M(ey, f"{eps_b} diluted EPS / price{fx_note}", formula="earnings_yield_v1")
            m["pe"] = M(1 / ey, eps_b, unit="x") if ey > 0 else M(None, status="nm", reason="Negative earnings: P/E not meaningful")
        else:
            m["earnings_yield"] = M(None, reason="Diluted EPS missing")
            m["pe"] = M(None, reason="Diluted EPS missing")
        if market_cap is not None and debt is not None:
            ev = market_cap + conv(debt) + conv(minority or 0) + conv(pref or 0) - conv(cash_st or 0)
            m["enterprise_value"] = M(ev, f"mcap + debt + minority + preferred − cash & ST inv. (BS {bs_date})", unit="ccy")
            if ebitda is not None and ebitda > 0 and ev > 0:
                m["ev_ebitda"] = M(ev / conv(ebitda), ebitda_b, unit="x")
                m["ebitda_to_ev"] = M(conv(ebitda) / ev, ebitda_b, formula="ebitda_ev_v1")
            else:
                why = "EBITDA ≤ 0" if ebitda is not None else "EBITDA missing"
                m["ev_ebitda"] = M(None, status="nm" if ebitda is not None else "na", reason=why)
                m["ebitda_to_ev"] = M(None, status="nm" if ebitda is not None else "na", reason=why)
        else:
            m["enterprise_value"] = M(None, reason="Debt or market value missing")
            m["ev_ebitda"] = M(None, reason="Debt or market value missing")
            m["ebitda_to_ev"] = M(None, reason="Debt or market value missing")
        if divs is not None and market_cap:
            m["shareholder_yield_div"] = M(conv(divs) / market_cap, "cash dividends paid / market cap")

    # ---------- quality
    if ebit is not None and equity is not None:
        t_rate = _ratio(tax, pretax) if (pretax and pretax > 0) else None
        assumed = t_rate is None or not 0 <= t_rate <= 0.5
        t_rate = 0.25 if assumed else t_rate
        ic_now = (debt or 0) + equity + (minority or 0) - (cash or 0)
        debt_1 = st.point_year_ago("total_debt", bs_date)
        eq_1 = st.point_year_ago("total_equity", eq_date)
        cash_1 = st.point_year_ago("cash", bs_date)
        min_1 = st.point_year_ago("minority_interest", bs_date)
        avg_note = "point-in-time IC"
        ic = ic_now
        if eq_1 is not None:
            ic_1 = (debt_1 or 0) + eq_1 + (min_1 or 0) - (cash_1 or 0)
            ic, avg_note = (ic_now + ic_1) / 2, "average invested capital (now, −1y)"
        tax_note = f"tax rate {'assumed 25%' if assumed else f'{t_rate:.1%} effective'}"
        assets, _ = st.point("total_assets")
        if ic <= 0:
            m["roic"] = M(None, status="nm", reason="Invested capital ≤ 0")
        elif assets and ic < 0.10 * assets:
            m["roic"] = M(None, status="nm", reason=f"Invested capital is only {ic / assets:.0%} of total assets (large net cash) — "
                                                     "ROIC not meaningful; see operating margin and cash conversion")
        else:
            m["roic"] = M(ebit * (1 - t_rate) / ic, f"{ebit_b} EBIT×(1−t) / {avg_note}; {tax_note}", formula="roic_v1")
    else:
        m["roic"] = M(None, reason="EBIT or equity missing")
    m["operating_margin"] = (M(op_inc / rev, op_b) if op_inc is not None and rev and rev > 0
                             else M(None, reason="Operating income or revenue missing"))
    m["gross_margin"] = M(gp / rev, rev_b) if gp is not None and rev and rev > 0 else M(None, reason="Gross profit missing")
    m["net_margin"] = M(ni / rev, ni_b) if ni is not None and rev and rev > 0 else M(None, reason="Net income missing")
    if ocf is not None and ni is not None:
        m["cash_conversion"] = (M(ocf / ni, f"{ocf_b} OCF / net income") if ni > 0
                                else M(None, status="nm", reason="Net income ≤ 0"))
    else:
        m["cash_conversion"] = M(None, reason="OCF or net income missing")

    # ---------- growth (negative bases are N/M, spec §10)
    for key, field in (("revenue_growth", "revenue"), ("eps_growth", "eps_diluted"), ("fcf_growth", "operating_cash_flow")):
        cur, prev, basis = st.growth_pair(field)
        if field == "eps_diluted" and cur is None:
            cur, prev, basis = st.growth_pair("net_income")
            basis = basis + " (net income proxy)" if basis else basis
        if cur is None or prev is None:
            m[key] = M(None, reason="Comparable prior period missing")
        elif prev <= 0:
            m[key] = M(None, status="nm", basis=basis, reason="Prior-period base ≤ 0: growth not meaningful")
        else:
            m[key] = M(cur / prev - 1, basis)
    if len(st.annual) >= 4 and all("revenue" in st.annual[i]["fields"] for i in (0, 3)):
        a0, a3 = st.annual[0]["fields"]["revenue"], st.annual[3]["fields"]["revenue"]
        yrs = (_d(st.annual[0]["period_end"]) - _d(st.annual[3]["period_end"])).days / 365.25
        m["revenue_cagr_3y"] = (M((a0 / a3) ** (1 / yrs) - 1, f"FY {st.annual[3]['period_end'][:4]}→{st.annual[0]['period_end'][:4]}")
                                if a0 > 0 and a3 > 0 else M(None, status="nm", reason="Non-positive endpoint"))
    else:
        m["revenue_cagr_3y"] = M(None, reason="Needs 4 fiscal years")

    # ---------- resilience
    if ebitda is not None and debt is not None:
        nd = debt - (cash_st or 0)
        m["net_debt"] = M(nd, f"BS {bs_date}", unit=f"{st.currency}")
        m["net_debt_ebitda"] = (M(nd / ebitda, f"BS {bs_date} / {ebitda_b}", unit="x") if ebitda > 0
                                else M(None, status="nm", reason="EBITDA ≤ 0: suppressed"))
    else:
        m["net_debt_ebitda"] = M(None, reason="Debt or EBITDA missing")
    if ebit is not None and intexp:
        m["interest_coverage"] = (M(ebit / intexp, f"{ebit_b} EBIT / interest", unit="x") if ebit > 0
                                  else M(None, status="nm", reason="EBIT ≤ 0"))
    else:
        m["interest_coverage"] = M(None, reason="No interest expense reported (not treated as infinite coverage)")

    # ---------- dilution, SBC, dividends
    sh_1 = st.point_year_ago("diluted_shares", sh_date)
    m["share_dilution"] = (M(shares_dil / sh_1 - 1, f"diluted shares {sh_date} vs −1y") if shares_dil and sh_1
                           else M(None, reason="Share history missing"))
    m["sbc_to_revenue"] = M(sbc / rev, rev_b) if sbc is not None and rev and rev > 0 else M(None, reason="SBC missing")
    fcf_v = (ocf - (capex or 0)) if ocf is not None else None
    if divs:
        m["payout_fcf"] = M(divs / fcf_v, "dividends / FCF") if fcf_v and fcf_v > 0 else M(None, status="nm", reason="FCF ≤ 0")
        m["payout_earnings"] = M(divs / ni, "dividends / net income") if ni and ni > 0 else M(None, status="nm", reason="Net income ≤ 0")
    # ---------- raw anchors for DCF workspace (reporting currency)
    m["_anchors"] = {"currency": st.currency, "revenue": rev, "operating_income": op_inc, "ebit": ebit,
                     "d_and_a": st.ttm("d_and_a")[0], "capex": capex, "ocf": ocf, "net_income": ni,
                     "income_tax": tax, "pretax_income": pretax, "total_debt": debt, "cash": cash_st,
                     "minority_interest": minority, "preferred_equity": pref, "diluted_shares": shares_dil,
                     "basis": rev_b, "balance_date": bs_date, "fx_fin_to_px": fx,
                     "invested_capital": ((debt or 0) + equity + (minority or 0) - (cash or 0)) if equity is not None else None,
                     # fiscal-year history for normalising DCF assumptions (cyclical peaks/troughs)
                     "fy_margins": [p["fields"]["operating_income"] / p["fields"]["revenue"] for p in st.annual[:5]
                                    if p["fields"].get("revenue") and p["fields"].get("operating_income") is not None],
                     "fy_growth": [st.annual[i]["fields"]["revenue"] / st.annual[i + 1]["fields"]["revenue"] - 1
                                   for i in range(min(4, len(st.annual) - 1))
                                   if st.annual[i]["fields"].get("revenue") and (st.annual[i + 1]["fields"].get("revenue") or 0) > 0]}
    m["_profitable"] = ni is not None and ni > 0
    m["_latest_period"] = st.latest_period_end()
    return m
