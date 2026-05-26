from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_STATUSES = {
    "queued",
    "submitting",
    "running",
    "saving",
    "succeeded",
    "failed",
    "cancelled",
    "timeout",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _redact_inline_images(value: Any) -> Any:
    if isinstance(value, list):
        return [_redact_inline_images(item) for item in value]
    if not isinstance(value, dict):
        return value
    item = dict(value)
    url = item.get("url")
    if isinstance(url, str) and url.startswith("data:"):
        item["url"] = f"{url[:32]}...<inline image omitted>"
    return item


def sanitize_job_params(params: dict[str, Any]) -> dict[str, Any]:
    """Keep runtime params small; reference images are stored in input_images_json."""
    cleaned = dict(params)
    for key in ("image_urls", "imageUrls", "input_images", "inputImages"):
        cleaned.pop(key, None)
    return cleaned


class JobStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._lock, self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL,
                    prompt TEXT,
                    params_json TEXT NOT NULL,
                    input_images_json TEXT NOT NULL,
                    external_task_id TEXT,
                    progress INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_updated ON jobs(status, updated_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at, id)")
            connection.execute("DROP TABLE IF EXISTS job_events")
            connection.commit()

    def create_job(
        self,
        *,
        provider: str,
        prompt: str,
        params: dict[str, Any],
        input_images: list[dict[str, Any]] | None = None,
        max_attempts: int = 1,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        timestamp = now_iso()
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, provider, status, prompt, params_json, input_images_json,
                    progress, result_json, attempts, max_attempts,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    provider,
                    "queued",
                    prompt,
                    json_dumps(sanitize_job_params(params)),
                    json_dumps(input_images or []),
                    0,
                    "[]",
                    0,
                    max(1, int(max_attempts or 1)),
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
        return self.get_job(job_id) or {"id": job_id, "status": "queued"}

    def claim_next_job(self, provider_limits: dict[str, int] | None = None) -> dict[str, Any] | None:
        provider_limits = provider_limits or {}
        with self._lock, self.connect() as connection:
            queued = connection.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 20"
            ).fetchall()
            for row in queued:
                provider = str(row["provider"])
                limit = int(provider_limits.get(provider, provider_limits.get("*", 1)))
                running = connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE provider = ? AND status IN ('submitting', 'running', 'saving')",
                    (provider,),
                ).fetchone()[0]
                if running >= limit:
                    continue
                timestamp = now_iso()
                updated = connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'submitting', attempts = attempts + 1, started_at = COALESCE(started_at, ?), updated_at = ?
                    WHERE id = ? AND status = 'queued'
                    """,
                    (timestamp, timestamp, row["id"]),
                )
                if updated.rowcount:
                    connection.commit()
                    return self.get_job(row["id"], redact_inline_images=False)
            return None

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        if "status" in fields and fields["status"] not in VALID_STATUSES:
            raise ValueError(f"invalid job status: {fields['status']}")
        encoded = {key: self._encode_value(value) for key, value in fields.items()}
        assignments = ", ".join(f"{key} = ?" for key in encoded)
        values = list(encoded.values()) + [job_id]
        with self._lock, self.connect() as connection:
            connection.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", values)
            connection.commit()

    def finish_job(self, job_id: str, status: str, *, result: list[dict[str, Any]] | None = None, error: str = "") -> None:
        if result is None:
            current = self.get_job(job_id)
            result = (current or {}).get("result") or []
        fields: dict[str, Any] = {
            "status": status,
            "progress": 100 if status == "succeeded" else 0,
            "result_json": result,
            "error": error,
            "finished_at": now_iso(),
        }
        if status in {"succeeded", "failed", "cancelled", "timeout"}:
            fields["input_images_json"] = self._redacted_input_images(job_id)
        self.update_job(job_id, **fields)

    def get_job(
        self,
        job_id: str,
        *,
        redact_inline_images: bool = True,
    ) -> dict[str, Any] | None:
        with self._lock, self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return None
            return self._row_to_dict(row, redact_inline_images=redact_inline_images)

    def list_jobs(self, *, status: str | None = None, active: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        query = """
            SELECT
                id, provider, status, prompt, external_task_id, progress, error,
                attempts, max_attempts, created_at, updated_at, started_at, finished_at,
                json_extract(params_json, '$.model') AS model,
                json_extract(params_json, '$.size') AS size,
                json_extract(params_json, '$.ratio') AS ratio,
                json_extract(params_json, '$.n') AS n,
                json_extract(params_json, '$.number') AS number,
                json_extract(params_json, '$.imageCount') AS image_count,
                json_array_length(input_images_json) AS input_image_count,
                result_json
            FROM jobs
        """
        params: list[Any] = []
        if active:
            query += " WHERE status IN ('queued', 'submitting', 'running', 'saving')"
        elif status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(500, int(limit or 100))))
        with self._lock, self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
            return [self._row_to_summary(row) for row in rows]

    def cancel_job(self, job_id: str) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        if not job:
            return None
        if job["status"] in {"succeeded", "failed", "cancelled", "timeout"}:
            return job
        self.finish_job(job_id, "cancelled", error="cancelled by user")
        return self.get_job(job_id)

    def retry_job(self, job_id: str) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        if not job:
            return None
        timestamp = now_iso()
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', error = '', progress = 0, external_task_id = NULL, finished_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (timestamp, job_id),
            )
            connection.commit()
        return self.get_job(job_id)

    def delete_job(self, job_id: str) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        if not job:
            return None
        with self._lock, self.connect() as connection:
            connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            connection.commit()
        return job

    def delete_jobs(self, *, include_active: bool = True) -> int:
        params: list[Any] = []
        where = ""
        if not include_active:
            where = " WHERE status NOT IN (?, ?, ?, ?)"
            params.extend(["queued", "submitting", "running", "saving"])

        with self._lock, self.connect() as connection:
            rows = connection.execute(f"SELECT id FROM jobs{where}", params).fetchall()
            job_ids = [row["id"] for row in rows]
            if not job_ids:
                return 0

            placeholders = ", ".join("?" for _ in job_ids)
            connection.execute(f"DELETE FROM jobs WHERE id IN ({placeholders})", job_ids)
            connection.commit()
            return len(job_ids)

    def mark_interrupted_jobs(self, *, error: str = "backend restarted before job finished") -> int:
        timestamp = now_iso()
        with self._lock, self.connect() as connection:
            rows = connection.execute(
                "SELECT id, input_images_json FROM jobs WHERE status IN ('submitting', 'running', 'saving')"
            ).fetchall()
            if not rows:
                return 0
            for row in rows:
                redacted_input_images = _redact_inline_images(json_loads(row["input_images_json"], []))
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'failed', error = ?, input_images_json = ?, finished_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (error, json_dumps(redacted_input_images), timestamp, timestamp, row["id"]),
                )
            connection.commit()
        return len(rows)

    def _encode_value(self, value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json_dumps(value)
        return value

    def _row_to_dict(self, row: sqlite3.Row, *, redact_inline_images: bool = True) -> dict[str, Any]:
        item = dict(row)
        item["params"] = json_loads(item.pop("params_json", ""), {})
        input_images = json_loads(item.pop("input_images_json", ""), [])
        item["input_images"] = _redact_inline_images(input_images) if redact_inline_images else input_images
        item["result"] = json_loads(item.pop("result_json", ""), [])
        item["job_id"] = item["id"]
        return item

    def _row_to_summary(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        params = {
            "model": item.pop("model", None),
            "size": item.pop("size", None),
            "ratio": item.pop("ratio", None),
            "n": item.pop("n", None),
            "number": item.pop("number", None),
            "imageCount": item.pop("image_count", None),
        }
        item["params"] = {key: value for key, value in params.items() if value not in (None, "")}
        input_image_count = int(item.pop("input_image_count", 0) or 0)
        item["input_images"] = [{} for _ in range(input_image_count)]
        item["result"] = json_loads(item.pop("result_json", ""), [])
        item["job_id"] = item["id"]
        return item

    def _redacted_input_images(self, job_id: str) -> list[dict[str, Any]]:
        with self._lock, self.connect() as connection:
            row = connection.execute("SELECT input_images_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return []
        return _redact_inline_images(json_loads(row["input_images_json"], []))
