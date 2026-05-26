from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from PIL import Image, ImageOps

import config
from services.public_uploader import upload_public_image


_LOCK = threading.RLock()


@dataclass(frozen=True)
class ReferenceAsset:
    ref_id: str
    name: str
    path: Path
    content_type: str
    suffix: str
    size: int
    width: int | None
    height: int | None
    public_urls: dict[str, dict[str, Any]]
    created_at: float
    last_used_at: float

    @property
    def local_url(self) -> str:
        return f"/api/reference-images/{quote(self.ref_id, safe='')}"

    @property
    def preview_url(self) -> str:
        return f"/api/reference-images/{quote(self.ref_id, safe='')}/thumbnail"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_id": self.ref_id,
            "name": self.name,
            "local_url": self.local_url,
            "preview_url": self.preview_url,
            "content_type": self.content_type,
            "suffix": self.suffix,
            "size": self.size,
            "width": self.width,
            "height": self.height,
            "public_urls": self.public_urls,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }


def get_reference_library_dir() -> Path:
    return (Path(config.OPENAI_SAVE_DIR) / "reference_library").resolve()


def get_reference_library_db_path() -> Path:
    return get_reference_library_dir() / "refs.sqlite"


def get_assets_dir() -> Path:
    return get_reference_library_dir() / "assets"


def get_thumbnails_dir() -> Path:
    return get_reference_library_dir() / "thumbs"


def save_reference_file(data: bytes, *, filename: str, content_type: str | None = None) -> ReferenceAsset:
    if not data:
        raise ValueError("empty reference image")
    suffix = _suffix_from_content_type(content_type or "") or _suffix_from_filename(filename)
    content_type = _content_type(content_type, suffix)
    ref_id = hashlib.sha256(data).hexdigest()
    now = time.time()
    path = _asset_path(ref_id, suffix)
    width, height = _image_dimensions(data)

    with _LOCK:
        _ensure_schema()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
            tmp_path.write_bytes(data)
            os.replace(tmp_path, path)
        current = _get_record(ref_id)
        public_urls = current.public_urls if current else {}
        created_at = current.created_at if current else now
        with _connect() as connection:
            connection.execute(
                """
                INSERT INTO reference_assets (
                    ref_id, name, path, content_type, suffix, size, width, height,
                    public_urls_json, created_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ref_id) DO UPDATE SET
                    name = excluded.name,
                    path = excluded.path,
                    content_type = excluded.content_type,
                    suffix = excluded.suffix,
                    size = excluded.size,
                    width = excluded.width,
                    height = excluded.height,
                    last_used_at = excluded.last_used_at
                """,
                (
                    ref_id,
                    filename or f"{ref_id}{suffix}",
                    str(path),
                    content_type,
                    suffix,
                    len(data),
                    width,
                    height,
                    json.dumps(public_urls, ensure_ascii=False),
                    created_at,
                    now,
                ),
            )
            connection.commit()
        return get_reference(ref_id, touch=False)


def get_reference(ref_id: str, *, touch: bool = True) -> ReferenceAsset:
    ref_id = normalize_ref_id(ref_id)
    with _LOCK:
        _ensure_schema()
        asset = _get_record(ref_id)
        if not asset:
            raise FileNotFoundError(ref_id)
        if touch:
            now = time.time()
            with _connect() as connection:
                connection.execute("UPDATE reference_assets SET last_used_at = ? WHERE ref_id = ?", (now, ref_id))
                connection.commit()
            asset = _get_record(ref_id) or asset
        return asset


def list_references(*, limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
    with _LOCK:
        _ensure_schema()
        with _connect() as connection:
            if limit is None:
                rows = connection.execute("SELECT * FROM reference_assets ORDER BY last_used_at DESC").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM reference_assets ORDER BY last_used_at DESC LIMIT ? OFFSET ?",
                    (max(1, int(limit)), max(0, int(offset))),
                ).fetchall()
        return [_asset_from_row(row).to_dict() for row in rows if _asset_from_row(row).path.exists()]


def count_references() -> int:
    with _LOCK:
        _ensure_schema()
        with _connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM reference_assets").fetchone()
        return int(row["count"] or 0) if row else 0


def delete_reference(ref_id: str) -> None:
    ref_id = normalize_ref_id(ref_id)
    with _LOCK:
        _ensure_schema()
        asset = _get_record(ref_id)
        if not asset:
            raise FileNotFoundError(ref_id)
        with _connect() as connection:
            connection.execute("DELETE FROM reference_assets WHERE ref_id = ?", (ref_id,))
            connection.commit()
        _delete_file(asset.path)
        for thumbnail in get_thumbnails_dir().glob(f"{ref_id}_*.webp"):
            _delete_file(thumbnail)


def read_reference_bytes(ref_id: str) -> tuple[bytes, str, str, Path]:
    asset = get_reference(ref_id)
    return asset.path.read_bytes(), asset.content_type, asset.suffix, asset.path


def resolve_reference_from_url(url: str) -> ReferenceAsset | None:
    ref_id = ref_id_from_url(url)
    if not ref_id:
        return None
    return get_reference(ref_id)


def ref_id_from_url(url: str) -> str | None:
    text = str(url or "").strip()
    if not text:
        return None
    match = re.search(r"/api/reference-images/([a-fA-F0-9]{64})(?:/thumbnail)?(?:\?|$)", text)
    if match:
        return match.group(1).lower()
    if re.fullmatch(r"[a-fA-F0-9]{64}", text):
        return text.lower()
    return None


def normalize_ref_id(value: str) -> str:
    ref_id = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", ref_id):
        raise ValueError("invalid ref_id")
    return ref_id


def ensure_public_url(ref_item: Any, *, provider: str) -> str:
    public_url_hint = None
    if isinstance(ref_item, dict):
        public_url_hint = ref_item.get("public_url")
        ref_id = ref_item.get("ref_id") or ref_id_from_url(str(ref_item.get("url") or ""))
        if not ref_id and ref_item.get("url"):
            url = str(ref_item["url"])
            if url.startswith(("http://", "https://")):
                return url
        if not ref_id and public_url_hint:
            return str(public_url_hint)
    else:
        ref_id = ref_id_from_url(str(ref_item or ""))
        if not ref_id and str(ref_item or "").startswith(("http://", "https://")):
            return str(ref_item)

    if not ref_id:
        raise ValueError("参考图缺少可公网化的 ref_id")

    asset = get_reference(str(ref_id))
    cached = asset.public_urls.get(provider) or asset.public_urls.get("default")
    cached_url = _fresh_public_url(cached)
    if cached_url:
        return cached_url

    now = time.time()
    result = upload_public_image(
        asset.path.read_bytes(),
        file_name=asset.name or f"{asset.ref_id}{asset.suffix}",
        content_type=asset.content_type,
    )
    public_urls = dict(asset.public_urls)
    public_urls[provider] = {
        "url": result.url,
        "uploader": result.uploader or "telegraph",
        "content_type": result.content_type,
        "size": result.size,
        "compressed": result.compressed,
        "created_at": now,
        "last_verified_at": now,
        "expires_at": now + public_url_ttl_seconds(),
    }
    _update_public_urls(asset.ref_id, public_urls)
    return result.url


def ensure_public_urls(items: Any, *, provider: str) -> list[str]:
    urls = []
    for item in items or []:
        urls.append(ensure_public_url(item, provider=provider))
    return urls


def public_url_ttl_seconds() -> int:
    return max(60, int(getattr(config, "PUBLIC_URL_TTL_SECONDS", 90 * 60) or 90 * 60))


def _fresh_public_url(meta: Any) -> str | None:
    if not isinstance(meta, dict) or not meta.get("url"):
        return None
    now = time.time()
    expires_at = _float_or_none(meta.get("expires_at"))
    if expires_at is not None:
        return str(meta["url"]) if now < expires_at else None

    created_at = _float_or_none(meta.get("created_at") or meta.get("last_verified_at"))
    if created_at is not None and now - created_at < public_url_ttl_seconds():
        return str(meta["url"])
    return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def thumbnail_path(ref_id: str, *, width: int = 512, quality: int = 82) -> Path:
    asset = get_reference(ref_id)
    width = max(128, min(1536, int(width or 512)))
    quality = max(45, min(92, int(quality or 82)))
    thumb = get_thumbnails_dir() / f"{asset.ref_id}_{width}_{quality}.webp"
    if thumb.exists():
        return thumb
    thumb.parent.mkdir(parents=True, exist_ok=True)
    tmp = thumb.with_name(f"{thumb.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with Image.open(asset.path) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((width, width), Image.Resampling.LANCZOS)
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        image.save(tmp, "WEBP", quality=quality, method=4)
    os.replace(tmp, thumb)
    return thumb


def library_usage() -> dict[str, Any]:
    root = get_reference_library_dir()
    assets = get_assets_dir()
    thumbs = get_thumbnails_dir()
    return {
        "path": str(root),
        "files": _count_files(root),
        "bytes": _directory_bytes(root),
        "assets": {
            "path": str(assets),
            "files": _count_files(assets),
            "bytes": _directory_bytes(assets),
        },
        "thumbnails": {
            "path": str(thumbs),
            "files": _count_files(thumbs),
            "bytes": _directory_bytes(thumbs),
        },
    }


def _ensure_schema() -> None:
    get_reference_library_dir().mkdir(parents=True, exist_ok=True)
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reference_assets (
                ref_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                content_type TEXT NOT NULL,
                suffix TEXT NOT NULL,
                size INTEGER NOT NULL,
                width INTEGER,
                height INTEGER,
                public_urls_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                last_used_at REAL NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_reference_assets_last_used ON reference_assets(last_used_at)")
        connection.commit()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(get_reference_library_db_path(), timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _get_record(ref_id: str) -> ReferenceAsset | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM reference_assets WHERE ref_id = ?", (ref_id,)).fetchone()
    return _asset_from_row(row) if row else None


def _asset_from_row(row: sqlite3.Row) -> ReferenceAsset:
    public_urls = {}
    try:
        parsed = json.loads(row["public_urls_json"] or "{}")
        public_urls = parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        public_urls = {}
    return ReferenceAsset(
        ref_id=str(row["ref_id"]),
        name=str(row["name"]),
        path=Path(str(row["path"])).resolve(),
        content_type=str(row["content_type"]),
        suffix=str(row["suffix"]),
        size=int(row["size"] or 0),
        width=int(row["width"]) if row["width"] is not None else None,
        height=int(row["height"]) if row["height"] is not None else None,
        public_urls=public_urls,
        created_at=float(row["created_at"] or 0),
        last_used_at=float(row["last_used_at"] or 0),
    )


def _update_public_urls(ref_id: str, public_urls: dict[str, Any]) -> None:
    with _LOCK:
        _ensure_schema()
        with _connect() as connection:
            connection.execute(
                "UPDATE reference_assets SET public_urls_json = ?, last_used_at = ? WHERE ref_id = ?",
                (json.dumps(public_urls, ensure_ascii=False), time.time(), ref_id),
            )
            connection.commit()


def _asset_path(ref_id: str, suffix: str) -> Path:
    return get_assets_dir() / ref_id[:2] / ref_id[2:4] / f"{ref_id}{suffix or '.png'}"


def _image_dimensions(data: bytes) -> tuple[int | None, int | None]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:
        return None, None


def _content_type(value: str | None, suffix: str) -> str:
    normalized = str(value or "").lower().split(";", 1)[0].strip()
    if normalized.startswith("image/"):
        return normalized
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return "image/png"


def _suffix_from_content_type(content_type: str) -> str:
    normalized = str(content_type or "").lower().split(";", 1)[0].strip()
    if normalized in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if normalized == "image/webp":
        return ".webp"
    if normalized == "image/gif":
        return ".gif"
    if normalized == "image/png":
        return ".png"
    return ""


def _suffix_from_filename(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".jpeg":
        return ".jpg"
    return suffix if suffix in {".png", ".jpg", ".webp", ".gif"} else ".png"


def _delete_file(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def _directory_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total
