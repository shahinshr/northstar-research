"""Investment universe: stock index constituents (multi-source) + a curated fund catalogue.

Universe modes (setting `universe`, default "global"):
  starter   17 hand-picked stocks (first draft)
  core      S&P 500 + S&P/TSX Composite + FTSE 100                          (~820 stocks)
  global    core + ASX 200, Hang Seng, SSE 50, NIFTY 50, DAX, CAC 40, SMI, Euro Stoxx 50   (~1,300)
  extended  global + FTSE 250 + CSI 300                                     (~1,800)

Constituent sources, in fallback order: Wikipedia tables -> issuer holdings files (iShares XIC for the
TSX) -> last successfully stored list. Funds are curated into comparable *cohorts* (spec §7: evaluate
passive funds within the same benchmark or closely matched strategy cohort).
"""
from __future__ import annotations

import io
import logging
import re

import httpx
import pandas as pd

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36"}

STARTER_STOCKS = [
    "AAPL", "MSFT", "NVDA", "JNJ", "COST", "JPM",
    "RY.TO", "SHOP.TO", "CNQ.TO", "CNR.TO", "ATD.TO", "CSU.TO",
    "AZN.L", "ULVR.L", "SHEL.L", "REL.L", "VOD.L",
]

# Regional ETF proxies (relative return, attention) + total-return indices (fund tracking)
BENCHMARKS = {"US": ("SPY", "SPDR S&P 500 ETF"), "CA": ("XIU.TO", "iShares S&P/TSX 60 ETF"),
              "UK": ("ISF.L", "iShares Core FTSE 100 ETF"), "AU": ("STW.AX", "SPDR S&P/ASX 200 ETF"),
              "HK": ("2800.HK", "Tracker Fund of Hong Kong"), "CN": ("510050.SS", "China 50 ETF (SSE 50)"),
              "IN": ("NIFTYBEES.NS", "Nippon India ETF Nifty 50"), "EU": ("EXW1.DE", "iShares Core Euro Stoxx 50"),
              "CH": ("CSSMI.SW", "iShares SMI ETF")}
INDICES = {"^SP500TR": ("S&P 500 Total Return", "USD"), "^XNDX": ("Nasdaq-100 Total Return", "USD")}

FINANCIAL_EXCLUSIONS = ("bank", "insurance", "reit", "capital markets", "mortgage", "credit services",
                        "asset management", "financial conglomerates", "shell companies")

# --------------------------------------------------------------------------------------------- funds
# cohort, asset_class (equity|fixed_income|allocation), style (passive|active), index (TR series) and
# hedged flag. Holdings source: "ssga:<ticker>" or "blackrock_ca:<id>/<slug>" for full holdings.
F = lambda sym, cohort, ac="equity", style="passive", index=None, hedged=False, holdings=None: dict(  # noqa: E731
    symbol=sym, cohort=cohort, asset_class=ac, style=style, index=index, hedged=hedged, holdings=holdings)
FUND_CATALOGUE = [
    F("SPY", "US large cap · S&P 500", index="^SP500TR", holdings="ssga:spy"),
    F("VOO", "US large cap · S&P 500", index="^SP500TR"),
    F("IVV", "US large cap · S&P 500", index="^SP500TR"),
    F("VFIAX", "US large cap · S&P 500", index="^SP500TR"),
    F("FXAIX", "US large cap · S&P 500", index="^SP500TR"),
    F("VFV.TO", "US large cap · S&P 500", index="^SP500TR"),
    F("ZSP.TO", "US large cap · S&P 500", index="^SP500TR"),
    F("XUS.TO", "US large cap · S&P 500", index="^SP500TR"),
    F("XSP.TO", "US large cap · S&P 500", index="^SP500TR", hedged=True),
    F("VUSA.L", "US large cap · S&P 500", index="^SP500TR"),
    F("VUAG.L", "US large cap · S&P 500", index="^SP500TR"),
    F("CSP1.L", "US large cap · S&P 500", index="^SP500TR"),
    F("VTI", "US total market"), F("VTSAX", "US total market"),
    F("QQQ", "US large growth · Nasdaq-100", index="^XNDX"),
    F("EQQQ.L", "US large growth · Nasdaq-100", index="^XNDX"),
    F("ZQQ.TO", "US large growth · Nasdaq-100", index="^XNDX", hedged=True),
    F("SCHD", "US dividend"), F("VIG", "US dividend"), F("VYM", "US dividend"),
    F("IWM", "US small cap"),
    F("VEA", "International developed"), F("VXUS", "International developed"),
    F("VWO", "Emerging markets"),
    F("VT", "Global equity"), F("VWRL.L", "Global equity"), F("VWRP.L", "Global equity"),
    F("SWDA.L", "Global equity"), F("XAW.TO", "Global equity"), F("VXC.TO", "Global equity"),
    F("XIC.TO", "Canadian equity · broad", holdings="blackrock_ca:239837/ishares-sptsx-capped-composite-index-etf"),
    F("VCN.TO", "Canadian equity · broad"), F("ZCN.TO", "Canadian equity · broad"),
    F("XIU.TO", "Canadian equity · TSX 60", holdings="blackrock_ca:239832/ishares-sptsx-60-index-etf"),
    F("HXT.TO", "Canadian equity · TSX 60"),
    F("VDY.TO", "Canadian dividend"), F("XEI.TO", "Canadian dividend"),
    F("ZEB.TO", "Canadian banks (sector)"),
    F("XEQT.TO", "Asset allocation · 100% equity", ac="allocation"),
    F("VEQT.TO", "Asset allocation · 100% equity", ac="allocation"),
    F("VGRO.TO", "Asset allocation · 80/20", ac="allocation"), F("XGRO.TO", "Asset allocation · 80/20", ac="allocation"),
    F("VBAL.TO", "Asset allocation · 60/40", ac="allocation"), F("XBAL.TO", "Asset allocation · 60/40", ac="allocation"),
    F("0P0000706A.TO", "Asset allocation · 60/40", ac="allocation", style="active"),
    F("ISF.L", "UK large cap · FTSE 100"), F("VUKE.L", "UK large cap · FTSE 100"),
    F("VMID.L", "UK mid cap · FTSE 250"),
    F("DODGX", "Active US large cap", style="active"), F("AGTHX", "Active US large cap", style="active"),
    F("0P0000RU81.L", "Active global equity", style="active"), F("0P0000RU7W.L", "Active global equity", style="active"),
    # ---- international & emerging (spec expansion: markets with growth potential)
    F("VGK", "Europe equity"), F("IEUR", "Europe equity"), F("EZU", "Europe equity"), F("EXW1.DE", "Europe equity"),
    F("EWJ", "Japan equity"), F("DXJ", "Japan equity", hedged=True),
    F("VAS.AX", "Australia equity"), F("IOZ.AX", "Australia equity"), F("STW.AX", "Australia equity"),
    F("MCHI", "China equity"), F("FXI", "China equity"), F("2800.HK", "China equity"), F("ASHR", "China A-shares"),
    F("KWEB", "China internet (thematic)"),
    F("INDA", "India equity"), F("EPI", "India equity"),
    F("IEMG", "Emerging markets"), F("EEM", "Emerging markets"), F("XEC.TO", "Emerging markets"), F("ZEM.TO", "Emerging markets"),
    F("AAXJ", "Asia ex Japan"), F("EWY", "Korea equity"), F("EWT", "Taiwan equity"), F("EWZ", "Brazil equity"),
    F("BND", "US aggregate bond", ac="fixed_income"), F("AGG", "US aggregate bond", ac="fixed_income"),
    F("VBTLX", "US aggregate bond", ac="fixed_income"),
    F("IEF", "US Treasury 7-10y", ac="fixed_income"), F("TLT", "US Treasury 20y+", ac="fixed_income"),
    F("LQD", "US investment-grade corporate", ac="fixed_income"), F("HYG", "US high yield", ac="fixed_income"),
    F("ZAG.TO", "Canadian aggregate bond", ac="fixed_income"), F("XBB.TO", "Canadian aggregate bond", ac="fixed_income"),
    F("VAB.TO", "Canadian aggregate bond", ac="fixed_income"),
    F("IGLT.L", "UK gilts", ac="fixed_income"), F("VGOV.L", "UK gilts", ac="fixed_income"),
]
FUND_BY_SYMBOL = {f["symbol"]: f for f in FUND_CATALOGUE}


# --------------------------------------------------------------------------------------------- stocks
WIKI_UA = {"User-Agent": "NorthstarResearch/0.2 (private investment research; python-httpx)"}


def _wiki_get(title: str) -> str:
    """Wikipedia asks for a descriptive User-Agent and rate-limits bursts (403/429): retry with back-off."""
    import time
    for attempt in range(4):
        r = httpx.get(f"https://en.wikipedia.org/wiki/{title}", headers=WIKI_UA, timeout=30, follow_redirects=True)
        if r.status_code == 200:
            time.sleep(0.5)  # be polite between index pages
            return r.text
        if r.status_code in (403, 429, 503):
            time.sleep(3 * (attempt + 1))
            continue
        r.raise_for_status()
    r.raise_for_status()
    return r.text


def _wiki_table(title: str, match: str) -> pd.DataFrame:
    return pd.read_html(io.StringIO(_wiki_get(title)), match=match)[0]


def _clean(t: str) -> str:
    return str(t).strip().upper().rstrip(".")


def sp500() -> list[dict]:
    df = _wiki_table("List_of_S%26P_500_companies", "Symbol")
    return [{"symbol": _clean(r["Symbol"]).replace(".", "-"), "index": "S&P 500", "sector_hint": r.get("GICS Sector")}
            for _, r in df.iterrows()]


def tsx_composite() -> list[dict]:
    try:
        df = _wiki_table("S%26P/TSX_Composite_Index", "Ticker")
        col = [c for c in df.columns if str(c).lower().startswith("ticker")][0]
        syms = [_clean(t) for t in df[col]]
    except Exception as e:  # noqa: BLE001 - fall back to the issuer holdings file
        log.warning("Wikipedia TSX failed (%s); using iShares XIC holdings", e)
        r = httpx.get("https://www.blackrock.com/ca/investors/en/products/239837/ishares-sptsx-capped-composite-index-etf/"
                      "1464253357814.ajax?fileType=csv&fileName=XIC_holdings&dataType=fund", headers=UA, timeout=30,
                      follow_redirects=True)
        lines = r.text.splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.startswith("Ticker,"))
        df = pd.read_csv(io.StringIO("\n".join(lines[start:])))
        df = df[df["Asset Class"] == "Equity"]
        syms = [_clean(t) for t in df["Ticker"]]
    return [{"symbol": s.replace(".", "-") + ".TO", "index": "S&P/TSX Composite"} for s in syms if re.match(r"^[A-Z0-9.\-]+$", s)]


def ftse(title: str, label: str) -> list[dict]:
    df = _wiki_table(title, "Ticker")
    col = [c for c in df.columns if str(c).lower().startswith("ticker")][0]
    return [{"symbol": _clean(t).replace(".", "-") + ".L", "index": label} for t in df[col] if isinstance(t, str)]


def _table(title: str, col: str) -> pd.DataFrame:
    return [t for t in pd.read_html(io.StringIO(_wiki_get(title)), match=col) if len(t) >= 15][0]


def _col(df: pd.DataFrame, prefix: str) -> pd.Series:
    return df[[c for c in df.columns if str(c).lower().startswith(prefix.lower())][0]]


def asx200() -> list[dict]:
    return [{"symbol": f"{_clean(t)}.AX", "index": "S&P/ASX 200"} for t in _col(_table("S%26P/ASX_200", "Code"), "Code")]


def hang_seng() -> list[dict]:
    out = []
    for t in _col(_table("Hang_Seng_Index", "Ticker"), "Ticker"):
        m = re.search(r"(\d+)", str(t))
        if m:
            out.append({"symbol": f"{int(m.group(1)):04d}.HK", "index": "Hang Seng"})
    return out


def china_a(title: str, label: str, col: str) -> list[dict]:
    out = []
    for t in _col(_table(title, col), col):
        m = re.search(r"(SSE|SZSE)\W*(\d{6})", str(t))
        if m:
            out.append({"symbol": f"{m.group(2)}.{'SS' if m.group(1) == 'SSE' else 'SZ'}", "index": label})
    return out


def nifty50() -> list[dict]:
    return [{"symbol": f"{_clean(t)}.NS", "index": "NIFTY 50"} for t in _col(_table("NIFTY_50", "Symbol"), "Symbol")]


def suffixed(title: str, label: str, col: str = "Ticker", default_suffix: str = "") -> list[dict]:
    out = []
    for t in _col(_table(title, col), col):
        t = str(t).strip().upper()
        if not re.match(r"^[A-Z0-9.\-]+$", t):
            continue
        out.append({"symbol": t if "." in t else f"{t}{default_suffix}", "index": label})
    return out


GLOBAL_PLAN = [
    ("S&P/ASX 200", asx200), ("Hang Seng", hang_seng),
    ("SSE 50", lambda: china_a("SSE_50_Index", "SSE 50", "Ticker")),
    ("NIFTY 50", nifty50), ("DAX", lambda: suffixed("DAX", "DAX")), ("CAC 40", lambda: suffixed("CAC_40", "CAC 40")),
    ("SMI", lambda: suffixed("Swiss_Market_Index", "SMI", default_suffix=".SW")),
    ("Euro Stoxx 50", lambda: suffixed("Euro_Stoxx_50", "Euro Stoxx 50")),
]


def fetch_constituents(mode: str) -> tuple[list[dict], list[str]]:
    """Returns (members, notes). Each member: {symbol, index}. Duplicates merged."""
    if mode == "starter":
        return [{"symbol": s, "index": "Starter"} for s in STARTER_STOCKS], []
    plan = [("S&P 500", sp500), ("S&P/TSX Composite", tsx_composite),
            ("FTSE 100", lambda: ftse("FTSE_100_Index", "FTSE 100"))]
    if mode in ("global", "extended"):
        plan += GLOBAL_PLAN
    if mode == "extended":
        plan.append(("FTSE 250", lambda: ftse("FTSE_250_Index", "FTSE 250")))
        plan.append(("CSI 300", lambda: china_a("CSI_300_Index", "CSI 300", "Ticker")))
    members: dict[str, dict] = {}
    notes = []
    for label, fn in plan:
        try:
            rows = fn()
            for r in rows:
                members.setdefault(r["symbol"], {"symbol": r["symbol"], "indices": []})["indices"].append(r["index"])
            notes.append(f"{label}: {len(rows)} constituents")
        except Exception as e:  # noqa: BLE001
            notes.append(f"{label}: FAILED ({type(e).__name__}) — previous list kept")
            log.exception("constituents %s failed", label)
    for s in STARTER_STOCKS:  # keep the original draft names
        members.setdefault(s, {"symbol": s, "indices": ["Starter"]})
    return list(members.values()), notes
