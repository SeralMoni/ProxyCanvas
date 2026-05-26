from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
from urllib.parse import unquote, urlparse

import requests

import config
from services import reference_library


REF_ID_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")
MAX_URL_LENGTH = 1000
MAX_NAME_LENGTH = 240
MAX_REMOTE_REFERENCE_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class ReferenceImage:
    data: bytes
    content_type: str
    suffix: str
    cache_hit: bool
    ref_id: str | None = None


def normalize_reference_inputs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, str]] = []
    for item in value:
        ref = normalize_reference_input(item)
        if ref:
            normalized.append(ref)
    return normalized


def normalize_reference_input(item: Any) -> dict[str, str] | None:
    if isinstance(item, dict):
        ref: dict[str, str] = {}
        ref_id = _clean_ref_id(item.get("ref_id"))
        url = _clean_url(item.get("url"))
        public_url = _clean_url(item.get("public_url"))
        name = _clean_name(item.get("name"))

        if url:
            ref["url"] = url
        if ref_id:
            ref["ref_id"] = ref_id
        if public_url:
            ref["public_url"] = public_url
        if name:
            ref["name"] = name

        return ref if (ref_id or url or public_url) else None

    url = _clean_url(item)
    return {"url": url} if url else None


def load_reference_image(value: Any, *, timeout: int = 30, proxies: Any = None) -> ReferenceImage:
    """Resolve a reference image input to bytes.

    Source of truth is reference_library. Remote URLs are imported into the
    library on first use instead of being stored in a separate transient cache.
    Data URIs are kept as read-only legacy compatibility and are not persisted.
    """
    ref_id = _ref_id_from_input(value)
    if ref_id:
        asset = reference_library.get_reference(ref_id)
        return ReferenceImage(
            data=asset.path.read_bytes(),
            content_type=asset.content_type,
            suffix=asset.suffix,
            cache_hit=True,
            ref_id=asset.ref_id,
        )

    url = _url_from_input(value)
    if not url:
        raise ValueError("参考图缺少 ref_id 或 URL")

    if url.startswith("data:"):
        data, content_type = _decode_data_uri(url)
        return ReferenceImage(
            data=data,
            content_type=content_type,
            suffix=_suffix_from_content_type(content_type),
            cache_hit=False,
        )

    if url.startswith(("http://", "https://")):
        return import_remote_reference_image(url, timeout=timeout, proxies=proxies)

    path = Path(url)
    if not path.is_absolute():
        path = Path(config.OPENAI_SAVE_DIR) / url
    data = path.read_bytes()
    content_type = _content_type_for_path(path)
    return ReferenceImage(data=data, content_type=content_type, suffix=path.suffix or _suffix_from_content_type(content_type), cache_hit=False)


def import_remote_reference_image(url: str, *, timeout: int = 30, proxies: Any = None, name: str | None = None) -> ReferenceImage:
    response = requests.get(url, timeout=timeout, proxies=config.HTTP_PROXIES if proxies is None else proxies)
    response.raise_for_status()
    data = response.content
    if len(data) > MAX_REMOTE_REFERENCE_BYTES:
        raise ValueError("远程参考图过大")
    content_type = _content_type(response.headers.get("Content-Type", ""))
    filename = name or _filename_from_url(url) or f"remote_{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}{_suffix_from_content_type(content_type)}"
    asset = reference_library.save_reference_file(data, filename=filename, content_type=content_type)
    return ReferenceImage(
        data=asset.path.read_bytes(),
        content_type=asset.content_type,
        suffix=asset.suffix,
        cache_hit=False,
        ref_id=asset.ref_id,
    )


def _clean_ref_id(value: Any) -> str | None:
    text = _clean_string(value)
    if not text or not REF_ID_PATTERN.fullmatch(text):
        return None
    return text.lower()


def _clean_url(value: Any) -> str | None:
    text = _clean_string(value)
    if not text or text.startswith("data:") or len(text) > MAX_URL_LENGTH:
        return None
    return text


def _clean_name(value: Any) -> str | None:
    text = _clean_string(value)
    return text[:MAX_NAME_LENGTH] if text else None


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _ref_id_from_input(value: Any) -> str | None:
    if isinstance(value, dict):
        ref_id = _clean_ref_id(value.get("ref_id"))
        if ref_id:
            return ref_id
        return reference_library.ref_id_from_url(str(value.get("url") or ""))
    text = _clean_string(value)
    ref_id = _clean_ref_id(text)
    return ref_id or reference_library.ref_id_from_url(text)


def _url_from_input(value: Any) -> str:
    if isinstance(value, dict):
        return _clean_string(value.get("url") or value.get("public_url"))
    return _clean_string(value)


def _decode_data_uri(data_uri: str) -> tuple[bytes, str]:
    header, b64_data = data_uri.split(",", 1)
    content_type = header[5:].split(";", 1)[0] or "image/png"
    return base64.b64decode(b64_data), _content_type(content_type)


def _content_type(value: str) -> str:
    normalized = str(value or "").lower().split(";", 1)[0].strip()
    return normalized if normalized.startswith("image/") else "image/png"


def _suffix_from_content_type(content_type: str) -> str:
    if content_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if content_type == "image/webp":
        return ".webp"
    if content_type == "image/gif":
        return ".gif"
    return ".png"


def _content_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return "image/png"


def _filename_from_url(url: str) -> str:
    try:
        name = Path(unquote(urlparse(url).path)).name
    except Exception:
        return ""
    return name[:MAX_NAME_LENGTH] if name else ""
