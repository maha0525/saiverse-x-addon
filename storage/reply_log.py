"""X reply de-duplication log (addon-private SQLite).

Stored at ``~/.saiverse/user_data/addon_data/saiverse-x-addon/x_reply_log.db``.

X enforces strict reply rules ("one reply per source tweet" etc.) and the
spec changes occasionally. Keeping this log on the addon side means future
schema tweaks ride along with addon updates rather than core migrations.

API contract:
- :func:`try_reserve_reply` inserts a row and returns its primary key,
  or ``None`` if the source tweet has already been replied to (UNIQUE
  constraint on ``tweet_id``).
- :func:`commit_reply` writes the posted reply's tweet ID into the row.
- :func:`cancel_reply` removes the row (caller could not post — e.g.
  user rejected, confirmation timed out, X API failed).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

LOGGER = logging.getLogger(__name__)

ADDON_NAME = "saiverse-x-addon"
_DB_FILENAME = "x_reply_log.db"

Base = declarative_base()


class ReplyLog(Base):
    """One row per source tweet that we have attempted to reply to."""

    __tablename__ = "x_reply_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tweet_id = Column(String(64), nullable=False)
    persona_id = Column(String(255), nullable=False)
    reply_tweet_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    confirmed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("tweet_id", name="uq_x_reply_log_tweet_id"),
    )


_engine = None
_SessionLocal = None
_init_lock = threading.Lock()


def _ensure_engine():
    """Lazily build the SQLAlchemy engine and create tables on first use."""
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine, _SessionLocal

    with _init_lock:
        if _engine is not None:
            return _engine, _SessionLocal

        from saiverse.addon_paths import get_addon_data_dir

        storage_dir = get_addon_data_dir(ADDON_NAME)
        db_path = storage_dir / _DB_FILENAME
        url = f"sqlite:///{db_path}"

        _engine = create_engine(url, future=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(_engine)
        LOGGER.info("[x_reply_log] Initialized SQLite at %s", db_path)

    return _engine, _SessionLocal


def try_reserve_reply(tweet_id: str, persona_id: str) -> Optional[int]:
    """Atomically reserve the right to reply to ``tweet_id``.

    Returns the new row's primary key on success, or ``None`` if a reply
    has already been recorded for this source tweet.
    """
    _, Session = _ensure_engine()
    if not tweet_id:
        return None

    with Session() as session:
        row = ReplyLog(tweet_id=tweet_id, persona_id=persona_id or "unknown")
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            LOGGER.info(
                "[x_reply_log] tweet_id=%s already has a reply record — skipping",
                tweet_id,
            )
            return None
        return int(row.id)


def commit_reply(reservation_id: int, reply_tweet_id: str) -> None:
    """Mark a reservation as posted, recording the resulting reply tweet ID."""
    _, Session = _ensure_engine()
    with Session() as session:
        row = session.get(ReplyLog, reservation_id)
        if row is None:
            LOGGER.warning(
                "[x_reply_log] commit_reply: reservation %s not found", reservation_id
            )
            return
        row.reply_tweet_id = reply_tweet_id
        row.confirmed_at = datetime.utcnow()
        session.commit()


def cancel_reply(reservation_id: int) -> None:
    """Discard a reservation that failed to post (delete the row)."""
    _, Session = _ensure_engine()
    with Session() as session:
        row = session.get(ReplyLog, reservation_id)
        if row is None:
            return
        session.delete(row)
        session.commit()
        LOGGER.info(
            "[x_reply_log] Cancelled reservation %s (tweet_id=%s)",
            reservation_id, row.tweet_id,
        )
