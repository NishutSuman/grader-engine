"""Engine + session factory. Single SQLite file at repo root: gradepilot.db
(override with GRADEPILOT_DB env var)."""
from __future__ import annotations

import os
import re
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

REPO = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("GRADEPILOT_DB", REPO / "gradepilot.db"))
DATA_DIR = Path(os.environ.get("GRADEPILOT_DATA", REPO / "data"))

_engine = create_engine(f"sqlite:///{DB_PATH}", future=True)
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, class_=Session)


def init_db() -> None:
    """Create tables if missing (idempotent). SQLite WAL for concurrent reads."""
    with _engine.connect() as c:
        c.exec_driver_sql("PRAGMA journal_mode=WAL")
        c.exec_driver_sql("PRAGMA foreign_keys=ON")
        c.exec_driver_sql("PRAGMA busy_timeout=10000")  # wait out concurrent job/web writers
    Base.metadata.create_all(_engine)


def get_session() -> Session:
    return SessionLocal()


def get_db():
    """FastAPI dependency: one session per request, always closed."""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def eval_data_dir(slug: str) -> Path:
    """On-disk artifact dir for an eval (downloads, cards).

    `slug` reaches here from user input (the "new eval" form) and is used
    directly in a filesystem path — without this check a slug like
    '../../../etc' would escape DATA_DIR entirely (path traversal / arbitrary
    file write via every endpoint that writes into an eval's data dir)."""
    if not _SLUG_RE.match(slug):
        raise ValueError(f"invalid eval slug: {slug!r}")
    d = DATA_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_filename(name: str) -> str:
    """Strip anything that isn't a plain filename character — for student/
    eval codes used to build on-disk file paths (card PDFs, ticket JSON),
    so a code containing '/' or '..' can never escape the intended dir."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return cleaned or "_"
