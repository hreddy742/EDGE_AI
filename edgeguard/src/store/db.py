from collections.abc import Generator
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

Base = declarative_base()

_engine = None
_SessionLocal = None


def init_db(db_url: str) -> None:
    global _engine, _SessionLocal

    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    _engine = create_engine(db_url, connect_args=connect_args, future=True)
    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False, future=True)

    from src.store import models  # noqa: F401

    Base.metadata.create_all(bind=_engine)
    _ensure_legacy_columns(db_url)


def is_sqlite_corruption_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "database disk image is malformed" in message


def _sqlite_db_path(db_url: str) -> Path | None:
    if not db_url.startswith("sqlite:///"):
        return None
    raw_path = db_url.removeprefix("sqlite:///")
    if raw_path in {"", ":memory:"}:
        return None
    return Path(raw_path).expanduser().resolve()


def recover_sqlite_database(db_url: str) -> Path | None:
    db_path = _sqlite_db_path(db_url)
    if db_path is None:
        return None

    global _engine
    if _engine is not None:
        _engine.dispose()

    backup_path: Path | None = None
    if db_path.exists():
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        backup_path = db_path.with_suffix(db_path.suffix + f".corrupt.{timestamp}.bak")
        db_path.rename(backup_path)

    init_db(db_url)
    return backup_path


def _ensure_legacy_columns(db_url: str) -> None:
    if _engine is None:
        return
    if not db_url.startswith("sqlite"):
        return

    with _engine.begin() as conn:
        try:
            rows = conn.execute(text("PRAGMA table_info(events)")).fetchall()
        except Exception:
            return
        existing = {row[1] for row in rows}

        column_sql = {
            "track_id": "ALTER TABLE events ADD COLUMN track_id INTEGER DEFAULT 0",
            "ts_start": "ALTER TABLE events ADD COLUMN ts_start DATETIME",
            "ts_trigger": "ALTER TABLE events ADD COLUMN ts_trigger DATETIME",
            "risk_score_at_trigger": "ALTER TABLE events ADD COLUMN risk_score_at_trigger FLOAT DEFAULT 0.0",
            "short_explanation": "ALTER TABLE events ADD COLUMN short_explanation VARCHAR(512) DEFAULT ''",
            "confidence": "ALTER TABLE events ADD COLUMN confidence FLOAT",
        }
        for col, sql in column_sql.items():
            if col not in existing:
                conn.execute(text(sql))

        try:
            clip_rows = conn.execute(text("PRAGMA table_info(clips)")).fetchall()
        except Exception:
            clip_rows = []
        clip_existing = {row[1] for row in clip_rows}
        clip_sql = {
            "processing_status": "ALTER TABLE clips ADD COLUMN processing_status VARCHAR(32) DEFAULT 'PENDING'",
            "retention_until": "ALTER TABLE clips ADD COLUMN retention_until DATETIME",
        }
        for col, sql in clip_sql.items():
            if col not in clip_existing:
                conn.execute(text(sql))


def get_session_local():
    if _SessionLocal is None:
        raise RuntimeError("Database is not initialized. Call init_db first.")
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    session_local = get_session_local()
    db = session_local()
    try:
        yield db
    finally:
        db.close()
