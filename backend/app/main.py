"""FastAPI entry point.  Run:  .venv/bin/uvicorn app.main:app --reload --port 8000"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from .api.routes import router
from .config import get_settings
from .db import SessionLocal, init_db, latest_completed_run
from .services import jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    jobs.recover_interrupted_jobs()
    jobs.seed_universe()
    with SessionLocal() as s:
        has_run = latest_completed_run(s) is not None
    if not has_run:
        jobs.submit("discovery")  # first-run backfill
    if os.getenv("DISABLE_SCHEDULER") != "1":
        jobs.DailyScheduler(os.getenv("DISCOVERY_TIME", "04:00"), os.getenv("DISCOVERY_TZ", "America/Toronto")).start()
        jobs.IntradayScheduler().start()
    yield


app = FastAPI(title="Investment Research API", version="0.1.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins.split(","),
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.get("/v1/health")
def health():
    return {"ok": True}
