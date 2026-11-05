"""Application settings. API keys are read from environment / backend/.env (never from the browser)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # ---- Provider credentials (placeholders; empty = provider skipped) ----
    fmp_api_key: str = ""
    eodhd_api_key: str = ""
    alphavantage_api_key: str = ""
    finnhub_api_key: str = ""
    twelvedata_api_key: str = ""
    sec_user_agent: str = "InvestmentDashboard/0.1 (personal research; contact: you@example.com)"

    # ---- App ----
    database_url: str = f"sqlite:///{BACKEND_DIR / 'data' / 'app.db'}"
    presentation_currency: str = "CAD"
    peer_min_size: int = 30
    cors_origins: str = "http://localhost:3000"

    # ---- Fallback chains (spec §11: capabilities evaluated per dataset, not per provider) ----
    chain_profile: str = "yahoo,fmp,eodhd,finnhub"
    chain_prices: str = "yahoo,fmp,eodhd,twelvedata,alphavantage"
    chain_financials: str = "yahoo,fmp,eodhd,alphavantage,sec_edgar"
    chain_estimates: str = "yahoo,fmp,eodhd"
    chain_price_targets: str = "yahoo,fmp,eodhd"
    chain_news: str = "finnhub,fmp,yahoo_rss,yahoo,eodhd,google_news,gdelt"
    chain_fx: str = "boc,frankfurter,yahoo"
    chain_fund_profile: str = "yahoo,fmp,eodhd"
    chain_holdings: str = "issuer,fmp,eodhd,yahoo"
    universe: str = "global"  # starter | core | global | extended (overridable in Settings)
    ingest_workers: int = 4

    def chain(self, dataset: str) -> list[str]:
        raw = getattr(self, f"chain_{dataset}")
        return [p.strip() for p in raw.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.database_url.startswith("sqlite:///./"):
        s.database_url = f"sqlite:///{BACKEND_DIR / s.database_url.removeprefix('sqlite:///./')}"
    return s
