"""Reference FX conversion from a CAD-based table (CAD per 1 unit of CCY)."""
from __future__ import annotations

import pandas as pd


class FXTable:
    def __init__(self, payload: dict | None, source: str | None = None):
        self.source = source
        self.series: dict[str, pd.Series] = {"CAD": pd.Series(dtype=float)}
        for ccy, pts in ((payload or {}).get("series") or {}).items():
            if pts:
                s = pd.Series({pd.Timestamp(p["d"]): p["v"] for p in pts}).sort_index()
                self.series[ccy] = s[~s.index.duplicated(keep="last")]

    def has(self, ccy: str | None) -> bool:
        return ccy == "CAD" or (ccy in self.series and not self.series[ccy].empty)

    def _cad_per(self, ccy: str) -> float | None:
        if ccy == "CAD":
            return 1.0
        s = self.series.get(ccy)
        return float(s.iloc[-1]) if s is not None and not s.empty else None

    def rate(self, frm: str | None, to: str | None) -> float | None:
        """Latest spot: units of `to` per 1 unit of `frm`."""
        if not frm or not to:
            return None
        if frm == to:
            return 1.0
        a, b = self._cad_per(frm), self._cad_per(to)
        return a / b if a and b else None

    def as_of(self, ccy: str = "USD") -> str | None:
        s = self.series.get(ccy)
        return s.index[-1].date().isoformat() if s is not None and not s.empty else None

    def series_rate(self, frm: str, to: str, index: pd.DatetimeIndex) -> pd.Series | None:
        """Daily conversion series aligned to `index` (forward-filled reference fixings)."""
        if frm == to:
            return pd.Series(1.0, index=index)

        def cad(ccy):
            if ccy == "CAD":
                return pd.Series(1.0, index=index)
            s = self.series.get(ccy)
            if s is None or s.empty:
                return None
            return s.reindex(s.index.union(index)).ffill().reindex(index)

        a, b = cad(frm), cad(to)
        if a is None or b is None:
            return None
        return a / b
