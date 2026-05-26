from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SousakuTaskBindingStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or config.JOBS_DB_PATH)
        self._lock = threading.RLock()
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sousaku_task_bindings (
                    task_id TEXT PRIMARY KEY,
                    job_id TEXT,
                    token_hash TEXT NOT NULL,
                    token_masked TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_sousaku_bindings_job ON sousaku_task_bindings(job_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_sousaku_bindings_token ON sousaku_task_bindings(token_hash)")
            connection.commit()

    def bind(self, *, task_id: str, job_id: str | None, token_hash: str, token_masked: str) -> None:
        timestamp = now_iso()
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sousaku_task_bindings (task_id, job_id, token_hash, token_masked, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    job_id = excluded.job_id,
                    token_hash = excluded.token_hash,
                    token_masked = excluded.token_masked,
                    updated_at = excluded.updated_at
                """,
                (task_id, job_id, token_hash, token_masked, timestamp, timestamp),
            )
            connection.commit()

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock, self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sousaku_task_bindings WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return dict(row) if row else None


TASK_BINDINGS = SousakuTaskBindingStore()
