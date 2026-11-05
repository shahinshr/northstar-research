"""Persistence. SQLite for the draft; set DATABASE_URL to a postgresql+psycopg URL to move to Postgres.

Design (spec §13, simplified for the draft):
* `dataset_snapshots` is append-only: every fetch is a new versioned record with provider, route,
  fetch time, content hash and the full fallback trail. Latest-state reads pick the newest valid row.
* `analysis_runs` + `analysis_results` are immutable published snapshots; the dashboard reads the
  latest *completed* run, so a failed run never half-publishes (spec §19).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        create_engine, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # canonical = Yahoo style (RY.TO, VOD.L)
    name: Mapped[str | None] = mapped_column(String(255))
    asset_type: Mapped[str] = mapped_column(String(32), default="stock")
    exchange: Mapped[str | None] = mapped_column(String(32))
    region: Mapped[str | None] = mapped_column(String(8))  # US / CA / UK
    currency: Mapped[str | None] = mapped_column(String(8))  # trading currency as quoted (may be GBp)
    sector: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(128))
    country: Mapped[str | None] = mapped_column(String(64))
    isin: Mapped[str | None] = mapped_column(String(16))
    tags: Mapped[dict | None] = mapped_column(JSON)  # index membership, fund cohort/benchmark, source lists
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DatasetSnapshot(Base):
    __tablename__ = "dataset_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    subject: Mapped[str] = mapped_column(String(64), index=True)  # instrument id, or macro key like "fx"
    dataset: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str | None] = mapped_column(String(32))
    route: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))  # valid / partial / failed
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict | None] = mapped_column(JSON)
    attempts: Mapped[list | None] = mapped_column(JSON)  # fallback trail [{provider,status,error,ms}]
    __table_args__ = (Index("ix_ds_subject_dataset_time", "subject", "dataset", "fetched_at"),)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running / completed / failed
    model_versions: Mapped[dict | None] = mapped_column(JSON)
    manifest: Mapped[dict | None] = mapped_column(JSON)  # counts, coverage, cohort info, fx used


class AnalysisResult(Base):
    __tablename__ = "analysis_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), index=True)
    core_score: Mapped[float | None] = mapped_column(Float)
    asset_class: Mapped[str | None] = mapped_column(String(16))  # stock | fund
    row: Mapped[dict | None] = mapped_column(JSON)  # slim dashboard row (avoids parsing full payloads)
    payload: Mapped[dict] = mapped_column(JSON)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32))
    scope_key: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    # queued / fetching / calculating / completed / partial / failed
    progress: Mapped[dict | None] = mapped_column(JSON)
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    run_id: Mapped[str | None] = mapped_column(String(36))


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    instrument_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(32))  # new_candidate | score_drop | new_earnings | watch_stale
    severity: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    read: Mapped[bool] = mapped_column(Boolean, default=False)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | None] = mapped_column(JSON)


class Scenario(Base):
    """Versioned DCF assumption sets (spec §8). Never alters provider facts."""
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    instrument_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)
    author: Mapped[str] = mapped_column(String(64), default="owner")
    assumptions: Mapped[dict] = mapped_column(JSON)
    results: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


settings = get_settings()
_connect_args = {"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    if settings.database_url.startswith("sqlite"):
        with engine.connect() as c:
            c.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """Additive column migrations for existing SQLite/Postgres databases (no data loss)."""
    from sqlalchemy import inspect
    insp = inspect(engine)
    adds = {"instruments": [("tags", "JSON")],
            "analysis_results": [("asset_class", "VARCHAR(16)"), ("row", "JSON")]}
    with engine.begin() as c:
        for table, cols in adds.items():
            have = {x["name"] for x in insp.get_columns(table)}
            for name, typ in cols:
                if name not in have:
                    c.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN "{name}" {typ}')


def get_setting(s: Session, key: str, default=None):
    row = s.get(Setting, key)
    return row.value if row is not None and row.value is not None else default


def put_setting(s: Session, key: str, value) -> None:
    row = s.get(Setting, key)
    if row is None:
        s.add(Setting(key=key, value=value))
    else:
        row.value = value
    s.commit()


def latest_snapshot(s: Session, subject: str, dataset: str, valid_only: bool = True) -> DatasetSnapshot | None:
    q = select(DatasetSnapshot).where(DatasetSnapshot.subject == subject, DatasetSnapshot.dataset == dataset)
    if valid_only:
        q = q.where(DatasetSnapshot.status.in_(["valid", "partial"]))
    return s.scalars(q.order_by(DatasetSnapshot.fetched_at.desc()).limit(1)).first()


def latest_completed_run(s: Session, offset: int = 0) -> AnalysisRun | None:
    q = (select(AnalysisRun).where(AnalysisRun.status == "completed")
         .order_by(AnalysisRun.created_at.desc()).offset(offset).limit(1))
    return s.scalars(q).first()
