from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend_v2"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import config  # noqa: E402


DEFAULT_TAG = "下载失败"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Redownload gallery images tagged as 下载失败 into OPENAI_SAVE_DIR."
    )
    parser.add_argument("--tag", default=DEFAULT_TAG, help=f"Tag to scan. Default: {DEFAULT_TAG}")
    parser.add_argument("--db", default=str(config.GALLERY_DB_PATH), help="Gallery SQLite path.")
    parser.add_argument("--jobs-db", default=str(config.JOBS_DB_PATH), help="Jobs SQLite path.")
    parser.add_argument("--save-dir", default=str(config.OPENAI_SAVE_DIR), help="Target save directory.")
    parser.add_argument("--timeout", type=int, default=60, help="Download timeout seconds.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files.")
    parser.add_argument("--dry-run", action="store_true", help="Only print what would be downloaded.")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    jobs_db_path = Path(args.jobs_db).resolve()
    save_dir = Path(args.save_dir).resolve()
    if not db_path.exists():
        raise SystemExit(f"Gallery DB not found: {db_path}")

    save_dir.mkdir(parents=True, exist_ok=True)

    records = _gallery_failed_records(db_path, args.tag, save_dir)
    if jobs_db_path.exists():
        records.extend(_job_failed_records(jobs_db_path, save_dir))
    records = _dedupe_records(records)

    stats = {"total": len(records), "downloaded": 0, "skipped": 0, "failed": 0}
    for record in records:
        image_id = record["id"]
        url = record["url"]
        target = record["target"]

        if not url:
            stats["skipped"] += 1
            print(f"SKIP {image_id}: no original URL")
            continue

        if target.exists() and not args.overwrite:
            stats["skipped"] += 1
            print(f"SKIP {image_id}: exists {target}")
            continue

        print(f"{'DRY ' if args.dry_run else ''}GET  {image_id}: {url}")
        print(f"     -> {target}")
        if args.dry_run:
            continue

        try:
            _download(url, target, timeout=args.timeout)
        except Exception as exc:
            stats["failed"] += 1
            print(f"FAIL {image_id}: {exc}")
            continue

        stats["downloaded"] += 1
        print(f"OK   {image_id}: {target.stat().st_size} bytes")

    print(
        "Done: "
        f"total={stats['total']} downloaded={stats['downloaded']} "
        f"skipped={stats['skipped']} failed={stats['failed']}"
    )
    return 0 if stats["failed"] == 0 else 1


def _gallery_failed_records(db_path: Path, tag: str, save_dir: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT gi.id, gi.relative_path, gi.saved_file_path, gi.local_path,
                   gi.original_url_json, gi.extra_json
            FROM gallery_images gi
            JOIN gallery_image_tags gt ON gt.image_id = gi.id
            WHERE gt.tag = ?
            ORDER BY gi.created_at DESC, gi.id ASC
            """,
            (tag,),
        ).fetchall()

    records: list[dict[str, Any]] = []
    for row in rows:
        image_id = row["id"]
        url = _extract_download_url(row)
        records.append({
            "id": image_id,
            "url": url,
            "target": _target_path(row, save_dir=save_dir, fallback_url=url, image_id=image_id),
        })
    return records


def _job_failed_records(jobs_db_path: Path, save_dir: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(jobs_db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, result_json
            FROM jobs
            WHERE result_json LIKE '%download_failed%'
            ORDER BY created_at DESC
            """
        ).fetchall()

    records: list[dict[str, Any]] = []
    for row in rows:
        try:
            results = json.loads(row["result_json"] or "[]")
        except json.JSONDecodeError:
            continue
        if not isinstance(results, list):
            continue
        for index, image in enumerate(results, start=1):
            if not isinstance(image, dict) or not image.get("download_failed"):
                continue
            url = _find_http_url(image)
            target = _target_path_from_job_result(image, save_dir=save_dir, fallback_url=url, image_id=f"{row['id']}_{index}")
            records.append({
                "id": f"{row['id']}#{index}",
                "url": url,
                "target": target,
            })
    return records


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str]] = set()
    for record in records:
        key = (record.get("url"), str(record.get("target")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _extract_download_url(row: sqlite3.Row) -> str | None:
    for value in (
        _json_loads(row["original_url_json"]),
        _json_loads(row["extra_json"]),
        row["local_path"],
        row["saved_file_path"],
    ):
        url = _find_http_url(value)
        if url:
            return url
    return None


def _find_http_url(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text if text.startswith(("http://", "https://")) else None
    if isinstance(value, dict):
        for key in ("url", "originalUrl", "original_url", "data", "image", "src"):
            url = _find_http_url(value.get(key))
            if url:
                return url
        for item in value.values():
            url = _find_http_url(item)
            if url:
                return url
    if isinstance(value, list):
        for item in value:
            url = _find_http_url(item)
            if url:
                return url
    return None


def _target_path(row: sqlite3.Row, *, save_dir: Path, fallback_url: str | None, image_id: str) -> Path:
    relative_path = _clean_relative_path(row["relative_path"])
    if relative_path:
        return save_dir / relative_path

    serve_path = _path_from_serve_url(row["local_path"])
    relative_path = _clean_relative_path(serve_path)
    if relative_path:
        return save_dir / relative_path

    saved_path = str(row["saved_file_path"] or "").strip()
    if saved_path:
        candidate = Path(saved_path)
        if candidate.is_absolute():
            return save_dir / candidate.name
        relative_path = _clean_relative_path(saved_path)
        if relative_path:
            return save_dir / relative_path

    return save_dir / _filename_from_url(fallback_url, image_id)


def _target_path_from_job_result(image: dict[str, Any], *, save_dir: Path, fallback_url: str | None, image_id: str) -> Path:
    saved_path = image.get("saved_path") or image.get("savedFilePath") or image.get("path") or image.get("local_path")
    if saved_path:
        path = Path(str(saved_path))
        if path.is_absolute():
            return save_dir / path.name
        relative_path = _clean_relative_path(str(saved_path))
        if relative_path:
            return save_dir / relative_path
    return save_dir / _filename_from_url(fallback_url, image_id)


def _download(url: str, target: Path, *, timeout: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f"{target.name}.tmp")
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with tmp_path.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)
    tmp_path.replace(target)


def _json_loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _path_from_serve_url(value: str | None) -> str | None:
    if not value or "/api/serve-image" not in value:
        return None
    parsed = urlparse(value)
    raw_path = parse_qs(parsed.query).get("path", [None])[0]
    return unquote(raw_path) if raw_path else None


def _clean_relative_path(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).replace("\\", "/").strip()
    if not text:
        return None
    path = Path(text)
    first_part = text.split("/", 1)[0]
    if ":" in first_part or path.is_absolute() or text.startswith("../") or "/../" in text or text == "..":
        return None
    return path.as_posix()


def _filename_from_url(url: str | None, image_id: str) -> str:
    suffix = ".png"
    if url:
        parsed = urlparse(url)
        url_name = Path(unquote(parsed.path)).name
        if Path(url_name).suffix:
            return url_name
        content_type = parse_qs(parsed.query).get("content_type", [None])[0]
        suffix = mimetypes.guess_extension(content_type or "") or suffix
    return f"{image_id}{suffix}"


if __name__ == "__main__":
    raise SystemExit(main())
