from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import config
from services import gallery_store
from services.reference_library import library_usage


def storage_usage() -> dict[str, Any]:
    save_dir = Path(config.OPENAI_SAVE_DIR).resolve()
    thumbnail_dir = save_dir / "thumbnails"

    return {
        "saveDir": str(save_dir),
        "gallery": _gallery_usage(save_dir),
        "thumbnailCache": {
            "path": str(thumbnail_dir),
            **_directory_usage(thumbnail_dir),
            "maxBytes": int(float(config.GALLERY_THUMBNAIL_CACHE_MAX_GB or 0) * 1024 * 1024 * 1024),
        },
        "referenceLibrary": library_usage(),
    }


def clear_cache(cache_name: str) -> dict[str, Any]:
    if cache_name == "thumbnails":
        target = Path(config.OPENAI_SAVE_DIR).resolve() / "thumbnails"
        before = _directory_usage(target)
        _clear_directory(target)
        return {"cleared": "thumbnails", **before, "remainingBytes": _directory_usage(target)["bytes"]}

    raise ValueError("Unsupported cache name")


def _gallery_usage(save_dir: Path) -> dict[str, Any]:
    records = gallery_store.gallery_file_records()
    unique_paths: dict[str, Path] = {}
    missing = 0

    for record in records:
        path_value = record.get("path")
        if not path_value:
            missing += 1
            continue
        path = Path(str(path_value)).resolve()
        if not path.is_file():
            missing += 1
            continue
        unique_paths[str(path)] = path

    imports_dir = (save_dir / "imports").resolve()
    total_bytes = 0
    import_count = 0
    import_bytes = 0
    for path in unique_paths.values():
        try:
            size = path.stat().st_size
        except OSError:
            missing += 1
            continue
        total_bytes += size
        if _is_relative_to(path, imports_dir):
            import_count += 1
            import_bytes += size

    return {
        "records": len(records),
        "files": len(unique_paths),
        "bytes": total_bytes,
        "missing": missing,
        "imports": {
            "path": str(imports_dir),
            "files": import_count,
            "bytes": import_bytes,
        },
    }


def _directory_usage(path: Path) -> dict[str, int]:
    if not path.exists():
        return {"files": 0, "bytes": 0}

    files = 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                files += 1
                total += item.stat().st_size
        except OSError:
            continue
    return {"files": files, "bytes": total}


def _clear_directory(path: Path) -> None:
    if not path.exists():
        return
    for item in path.iterdir():
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        except OSError:
            continue


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
