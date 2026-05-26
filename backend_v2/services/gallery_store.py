from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import config
from services.image_files import absolute_path_for_relative_path, path_to_relative_path, resolve_allowed_path, serve_url_for_path


GALLERY_DB = Path(config.GALLERY_DB_PATH)
_gallery_lock = threading.RLock()
_initialized = False

KNOWN_IMAGE_KEYS = {
    "id",
    "status",
    "error",
    "localPath",
    "savedFilePath",
    "relativePath",
    "thumbnail",
    "width",
    "height",
    "prompt",
    "apiType",
    "params",
    "createdAt",
    "originalUrl",
    "isFavorite",
    "tags",
}


def _empty_gallery() -> dict[str, Any]:
    return {"images": [], "tags": []}


def load_gallery(*, limit: int | None = None, offset: int = 0) -> dict[str, Any]:
    _ensure_ready()
    with _gallery_lock, _connect() as connection:
        query = """
            SELECT *
            FROM gallery_images
            ORDER BY
                created_at DESC,
                CASE
                    WHEN json_valid(extra_json)
                    THEN COALESCE(CAST(json_extract(extra_json, '$.resultIndex') AS INTEGER), 999999)
                    ELSE 999999
                END ASC,
                id ASC
            """
        params: list[Any] = []
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, max(0, offset)])
        image_rows = connection.execute(
            query,
            params,
        ).fetchall()
        image_ids = [row["id"] for row in image_rows]
        if image_ids:
            placeholders = ",".join("?" for _ in image_ids)
            tag_rows = connection.execute(
                f"""
                SELECT image_id, tag
                FROM gallery_image_tags
                WHERE image_id IN ({placeholders})
                ORDER BY image_id ASC, tag ASC
                """,
                image_ids,
            ).fetchall()
        else:
            tag_rows = []

    tags_by_image: dict[str, list[str]] = {}
    all_tags: set[str] = set()
    for row in tag_rows:
        tag = row["tag"]
        tags_by_image.setdefault(row["image_id"], []).append(tag)
        all_tags.add(tag)

    return {
        "images": [_row_to_image(row, tags_by_image.get(row["id"], [])) for row in image_rows],
        "tags": sorted(all_tags),
    }


def save_gallery(data: dict[str, Any]) -> None:
    """Replace the SQLite gallery with the provided data.

    Kept for compatibility with older callers; normal writes should use
    upsert_image, add_images, delete_image, or replace_tags.
    """
    images = data.get("images", []) if isinstance(data, dict) else []
    if not isinstance(images, list):
        images = []

    _ensure_ready()
    with _gallery_lock, _connect() as connection:
        connection.execute("DELETE FROM gallery_image_tags")
        connection.execute("DELETE FROM gallery_images")
        _insert_images(connection, [image for image in images if isinstance(image, dict)])
        connection.commit()


def upsert_image(image: dict[str, Any]) -> bool:
    """Insert or update an image. Returns True when inserted, False when updated."""
    if not isinstance(image, dict) or not image.get("id"):
        raise ValueError("gallery image id is required")

    _ensure_ready()
    image_id = str(image["id"])
    with _gallery_lock, _connect() as connection:
        existing = connection.execute("SELECT 1 FROM gallery_images WHERE id = ?", (image_id,)).fetchone()
        connection.execute("DELETE FROM gallery_deleted_images WHERE id = ?", (image_id,))
        _insert_images(connection, [image], skip_deleted=False)
        connection.commit()
        return existing is None


def delete_image(image_id: str) -> tuple[bool, str | None]:
    """Delete a gallery record. Returns (deleted, saved_file_path)."""
    _ensure_ready()
    with _gallery_lock, _connect() as connection:
        _mark_deleted_locked(connection, [image_id])
        row = connection.execute(
            "SELECT relative_path, saved_file_path, local_path FROM gallery_images WHERE id = ?",
            (image_id,),
        ).fetchone()
        if not row:
            connection.commit()
            return False, None

        connection.execute("DELETE FROM gallery_image_tags WHERE image_id = ?", (image_id,))
        connection.execute("DELETE FROM gallery_images WHERE id = ?", (image_id,))
        connection.commit()
        return True, _row_file_path(row)


def get_images_by_ids(image_ids: list[str]) -> list[dict[str, Any]]:
    ids = _clean_id_list(image_ids)
    if not ids:
        return []

    _ensure_ready()
    placeholders = ",".join("?" for _ in ids)
    with _gallery_lock, _connect() as connection:
        image_rows = connection.execute(
            f"""
            SELECT *
            FROM gallery_images
            WHERE id IN ({placeholders})
            """,
            ids,
        ).fetchall()
        tag_rows = connection.execute(
            f"""
            SELECT image_id, tag
            FROM gallery_image_tags
            WHERE image_id IN ({placeholders})
            ORDER BY image_id ASC, tag ASC
            """,
            ids,
        ).fetchall()

    tags_by_image: dict[str, list[str]] = {}
    for row in tag_rows:
        tags_by_image.setdefault(row["image_id"], []).append(row["tag"])

    images_by_id = {row["id"]: _row_to_image(row, tags_by_image.get(row["id"], [])) for row in image_rows}
    return [images_by_id[image_id] for image_id in ids if image_id in images_by_id]


def delete_images(image_ids: list[str]) -> list[tuple[str, str | None]]:
    ids = _clean_id_list(image_ids)
    if not ids:
        return []

    deleted: list[tuple[str, str | None]] = []
    _ensure_ready()
    with _gallery_lock, _connect() as connection:
        _mark_deleted_locked(connection, ids)
        for image_id in ids:
            row = connection.execute(
                "SELECT relative_path, saved_file_path, local_path FROM gallery_images WHERE id = ?",
                (image_id,),
            ).fetchone()
            if not row:
                continue
            deleted.append((image_id, _row_file_path(row)))
            connection.execute("DELETE FROM gallery_image_tags WHERE image_id = ?", (image_id,))
            connection.execute("DELETE FROM gallery_images WHERE id = ?", (image_id,))
        connection.commit()
    return deleted


def update_image_tags(image_ids: list[str], *, add: list[str] | None = None, remove: list[str] | None = None) -> int:
    ids = _clean_id_list(image_ids)
    add_tags = _clean_tag_list(add or [])
    remove_tags = _clean_tag_list(remove or [])
    if not ids or (not add_tags and not remove_tags):
        return 0

    _ensure_ready()
    touched = 0
    timestamp = _now_iso()
    with _gallery_lock, _connect() as connection:
        for image_id in ids:
            exists = connection.execute("SELECT 1 FROM gallery_images WHERE id = ?", (image_id,)).fetchone()
            if not exists:
                continue
            for tag in add_tags:
                connection.execute(
                    "INSERT OR IGNORE INTO gallery_image_tags (image_id, tag) VALUES (?, ?)",
                    (image_id, tag),
                )
            for tag in remove_tags:
                connection.execute(
                    "DELETE FROM gallery_image_tags WHERE image_id = ? AND tag = ?",
                    (image_id, tag),
                )
            connection.execute("UPDATE gallery_images SET updated_at = ? WHERE id = ?", (timestamp, image_id))
            touched += 1
        connection.commit()
    return touched


def set_images_favorite(image_ids: list[str], favorite: bool) -> int:
    ids = _clean_id_list(image_ids)
    if not ids:
        return 0

    _ensure_ready()
    timestamp = _now_iso()
    with _gallery_lock, _connect() as connection:
        touched = 0
        for image_id in ids:
            cursor = connection.execute(
                "UPDATE gallery_images SET is_favorite = ?, updated_at = ? WHERE id = ?",
                (1 if favorite else 0, timestamp, image_id),
            )
            touched += int(cursor.rowcount or 0)
        connection.commit()
    return touched


def replace_tags(tags: list[str]) -> None:
    """Compatibility hook for the old JSON-level tag list.

    Tags are now derived from per-image tag rows, matching what the frontend
    actually uses. The requested list is intentionally not allowed to create
    tags that no image owns.
    """
    _ensure_ready()


def add_images(images: list[dict[str, Any]]) -> None:
    if not images:
        return

    _ensure_ready()
    with _gallery_lock, _connect() as connection:
        _insert_images(connection, [image for image in images if isinstance(image, dict) and image.get("id")])
        connection.commit()


def add_job_images(images: list[dict[str, Any]]) -> int:
    """Add worker-published images without reviving deleted slots.

    Existing user metadata such as favorite state and tags wins over the
    worker's default values, because a final provider poll can arrive after the
    user has already organized partial results.
    """
    if not images:
        return 0

    _ensure_ready()
    with _gallery_lock, _connect() as connection:
        inserted = _insert_images(
            connection,
            [image for image in images if isinstance(image, dict) and image.get("id")],
            preserve_user_fields=True,
            skip_deleted=True,
        )
        connection.commit()
        return inserted


def is_image_deleted(image_id: str) -> bool:
    _ensure_ready()
    with _gallery_lock, _connect() as connection:
        return _is_deleted_locked(connection, image_id)


def gallery_file_records() -> list[dict[str, str | None]]:
    """Return current gallery file references for storage accounting."""
    _ensure_ready()
    with _gallery_lock, _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, relative_path, saved_file_path, local_path
            FROM gallery_images
            """
        ).fetchall()

    return [
        {
            "id": row["id"],
            "path": _row_file_path(row),
            "relativePath": row["relative_path"],
        }
        for row in rows
    ]


def is_file_still_referenced(file_path: str | None) -> bool:
    """Return True when another gallery row still points at this file."""
    target = _normal_file_key(file_path)
    if not target:
        return False

    _ensure_ready()
    with _gallery_lock, _connect() as connection:
        rows = connection.execute(
            """
            SELECT relative_path, saved_file_path, local_path
            FROM gallery_images
            """
        ).fetchall()

    return any(_normal_file_key(_row_file_path(row)) == target for row in rows)


def _ensure_ready() -> None:
    global _initialized
    if _initialized:
        return

    with _gallery_lock:
        if _initialized:
            return
        GALLERY_DB.parent.mkdir(parents=True, exist_ok=True)
        with _connect() as connection:
            _init_db(connection)
            if _is_gallery_empty(connection):
                _migrate_json_gallery(connection)
            connection.commit()
        _initialized = True


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(GALLERY_DB, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def _init_db(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS gallery_images (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            error_json TEXT,
            local_path TEXT NOT NULL,
            saved_file_path TEXT,
            relative_path TEXT,
            thumbnail TEXT,
            width INTEGER,
            height INTEGER,
            prompt TEXT NOT NULL,
            api_type TEXT NOT NULL,
            params_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            original_url_json TEXT,
            is_favorite INTEGER NOT NULL DEFAULT 0,
            extra_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS gallery_image_tags (
            image_id TEXT NOT NULL,
            tag TEXT NOT NULL,
            PRIMARY KEY (image_id, tag),
            FOREIGN KEY (image_id) REFERENCES gallery_images(id) ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS gallery_deleted_images (
            id TEXT PRIMARY KEY,
            deleted_at TEXT NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_gallery_images_created ON gallery_images(created_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_gallery_images_api_type ON gallery_images(api_type)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_gallery_images_favorite ON gallery_images(is_favorite)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_gallery_tags_tag ON gallery_image_tags(tag)")
    _ensure_gallery_column(connection, "relative_path", "TEXT")


def _ensure_gallery_column(connection: sqlite3.Connection, name: str, definition: str) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(gallery_images)").fetchall()
    }
    if name not in columns:
        connection.execute(f"ALTER TABLE gallery_images ADD COLUMN {name} {definition}")


def _mark_deleted_locked(connection: sqlite3.Connection, image_ids: list[str]) -> None:
    timestamp = _now_iso()
    for image_id in _clean_id_list(image_ids):
        connection.execute(
            """
            INSERT INTO gallery_deleted_images (id, deleted_at)
            VALUES (?, ?)
            ON CONFLICT(id) DO UPDATE SET deleted_at = excluded.deleted_at
            """,
            (image_id, timestamp),
        )


def _is_deleted_locked(connection: sqlite3.Connection, image_id: str) -> bool:
    if not image_id:
        return False
    row = connection.execute("SELECT 1 FROM gallery_deleted_images WHERE id = ?", (str(image_id),)).fetchone()
    return row is not None


def _existing_user_fields_locked(connection: sqlite3.Connection, image_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT is_favorite FROM gallery_images WHERE id = ?",
        (image_id,),
    ).fetchone()
    if not row:
        return None
    tag_rows = connection.execute(
        "SELECT tag FROM gallery_image_tags WHERE image_id = ? ORDER BY tag ASC",
        (image_id,),
    ).fetchall()
    return {
        "isFavorite": bool(row["is_favorite"]),
        "tags": [tag_row["tag"] for tag_row in tag_rows],
    }


def _is_gallery_empty(connection: sqlite3.Connection) -> bool:
    count = connection.execute("SELECT COUNT(*) FROM gallery_images").fetchone()[0]
    return int(count or 0) == 0


def _migrate_json_gallery(connection: sqlite3.Connection) -> None:
    data = _load_json_gallery()
    images = data.get("images", []) if isinstance(data, dict) else []
    if not isinstance(images, list) or not images:
        return
    _insert_images(connection, [image for image in images if isinstance(image, dict) and image.get("id")])


def _load_json_gallery() -> dict[str, Any]:
    gallery_file = Path(config.OPENAI_SAVE_DIR) / "gallery.json"
    if not gallery_file.exists():
        return _empty_gallery()
    try:
        with gallery_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return _empty_gallery()
    return data if isinstance(data, dict) else _empty_gallery()


def _insert_images(
    connection: sqlite3.Connection,
    images: list[dict[str, Any]],
    *,
    preserve_user_fields: bool = False,
    skip_deleted: bool = True,
) -> int:
    timestamp = _now_iso()
    inserted = 0
    for image in images:
        normalized = _normalize_image(image)
        image_id = normalized["id"]
        if skip_deleted and _is_deleted_locked(connection, image_id):
            continue
        if preserve_user_fields:
            user_fields = _existing_user_fields_locked(connection, image_id)
            if user_fields:
                normalized["isFavorite"] = user_fields["isFavorite"]
                if user_fields["tags"]:
                    normalized["tags"] = user_fields["tags"]
        connection.execute(
            """
            INSERT INTO gallery_images (
                id, status, error_json, local_path, saved_file_path, relative_path, thumbnail,
                width, height, prompt, api_type, params_json, created_at,
                original_url_json, is_favorite, extra_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                error_json = excluded.error_json,
                local_path = excluded.local_path,
                saved_file_path = excluded.saved_file_path,
                relative_path = excluded.relative_path,
                thumbnail = excluded.thumbnail,
                width = excluded.width,
                height = excluded.height,
                prompt = excluded.prompt,
                api_type = excluded.api_type,
                params_json = excluded.params_json,
                created_at = excluded.created_at,
                original_url_json = excluded.original_url_json,
                is_favorite = excluded.is_favorite,
                extra_json = excluded.extra_json,
                updated_at = excluded.updated_at
            """,
            (
                image_id,
                normalized["status"],
                _json_dumps(normalized.get("error")),
                normalized["localPath"],
                normalized.get("savedFilePath"),
                normalized.get("relativePath"),
                normalized.get("thumbnail"),
                normalized.get("width"),
                normalized.get("height"),
                normalized["prompt"],
                normalized["apiType"],
                _json_dumps(normalized["params"]),
                normalized["createdAt"],
                _json_dumps(normalized.get("originalUrl")),
                1 if normalized["isFavorite"] else 0,
                _json_dumps(normalized["extra"]),
                timestamp,
            ),
        )
        connection.execute("DELETE FROM gallery_image_tags WHERE image_id = ?", (image_id,))
        for tag in normalized["tags"]:
            connection.execute(
                "INSERT OR IGNORE INTO gallery_image_tags (image_id, tag) VALUES (?, ?)",
                (image_id, tag),
            )
        inserted += 1
    return inserted


def _normalize_image(image: dict[str, Any]) -> dict[str, Any]:
    extra = {key: value for key, value in image.items() if key not in KNOWN_IMAGE_KEYS}
    relative_path = _relative_path_from_image(image)
    local_path = str(image.get("localPath") or "")
    saved_file_path = _optional_str(image.get("savedFilePath"))
    thumbnail = _optional_str(image.get("thumbnail"))

    if relative_path:
        resolved_path = resolve_allowed_path(relative_path=relative_path)
        if resolved_path:
            local_path = serve_url_for_path(resolved_path)
            if not thumbnail or _path_from_serve_url(thumbnail):
                thumbnail = local_path
        saved_file_path = None
    else:
        relative_path = None

    return {
        "id": str(image["id"]),
        "status": str(image.get("status") or "success"),
        "error": image.get("error"),
        "localPath": local_path,
        "savedFilePath": saved_file_path,
        "relativePath": relative_path,
        "thumbnail": thumbnail,
        "width": _optional_int(image.get("width")),
        "height": _optional_int(image.get("height")),
        "prompt": str(image.get("prompt") or ""),
        "apiType": str(image.get("apiType") or "other"),
        "params": image.get("params") if isinstance(image.get("params"), dict) else {},
        "createdAt": str(image.get("createdAt") or _now_iso()),
        "originalUrl": image.get("originalUrl"),
        "isFavorite": bool(image.get("isFavorite")),
        "tags": _clean_tag_list(image.get("tags", [])),
        "extra": extra,
    }


def _row_to_image(row: sqlite3.Row, tags: list[str]) -> dict[str, Any]:
    extra = _json_loads(row["extra_json"], {})
    image = extra if isinstance(extra, dict) else {}
    relative_path = row["relative_path"]
    file_path = _row_file_path(row)
    local_path = row["local_path"]
    if relative_path:
        local_path = f"/api/serve-image?path={quote(relative_path, safe='')}"
    elif file_path:
        local_path = f"/api/serve-image?path={quote(file_path, safe='')}"
    thumbnail = row["thumbnail"] or local_path
    if (relative_path or file_path) and _is_serve_image_url(thumbnail):
        thumbnail = local_path
    image.update(
        {
            "id": row["id"],
            "status": row["status"],
            "localPath": local_path,
            "prompt": row["prompt"],
            "apiType": row["api_type"],
            "params": _json_loads(row["params_json"], {}),
            "createdAt": row["created_at"],
            "isFavorite": bool(row["is_favorite"]),
            "tags": _clean_tag_list(tags),
        }
    )
    _set_if_present(image, "relativePath", relative_path)
    _set_if_present(image, "error", _json_loads(row["error_json"], None))
    if not relative_path:
        _set_if_present(image, "savedFilePath", file_path)
    _set_if_present(image, "thumbnail", thumbnail)
    _set_if_present(image, "width", row["width"])
    _set_if_present(image, "height", row["height"])
    _set_if_present(image, "originalUrl", _json_loads(row["original_url_json"], None))
    return image


def _path_from_serve_url(value: str) -> str | None:
    if "/api/serve-image" not in value:
        return None
    parsed = urlparse(value)
    params = parse_qs(parsed.query)
    raw_path = params.get("path", [None])[0]
    if not raw_path:
        return None
    path = resolve_allowed_path(raw_path)
    return str(path) if path else unquote(raw_path)


def _is_serve_image_url(value: str | None) -> bool:
    return bool(value and "/api/serve-image" in value)


def _relative_path_from_image(image: dict[str, Any]) -> str | None:
    explicit_relative = _optional_str(image.get("relativePath"))
    if explicit_relative:
        path = resolve_allowed_path(relative_path=explicit_relative)
        if path:
            return explicit_relative

    candidates = [
        image.get("savedFilePath"),
        _path_from_serve_url(str(image.get("localPath") or "")),
        _path_from_serve_url(str(image.get("thumbnail") or "")),
    ]
    for candidate in candidates:
        relative_path = path_to_relative_path(candidate)
        if relative_path:
            return relative_path
    return None


def _row_file_path(row: sqlite3.Row) -> str | None:
    if row["relative_path"]:
        path = absolute_path_for_relative_path(row["relative_path"])
        if path:
            return str(path)
    return row["saved_file_path"] or _path_from_serve_url(row["local_path"] or "")


def _normal_file_key(file_path: str | None) -> str | None:
    if not file_path:
        return None
    try:
        return str(Path(file_path).resolve()).casefold()
    except (OSError, RuntimeError, ValueError):
        return str(file_path).casefold()


def _clean_tag_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        tag = _clean_tag(item)
        if tag and tag not in seen:
            cleaned.append(tag)
            seen.add(tag)
    return cleaned


def _clean_tag(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _clean_id_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        image_id = str(item).strip() if item is not None else ""
        if image_id and image_id not in seen:
            cleaned.append(image_id)
            seen.add(image_id)
    return cleaned


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _set_if_present(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
