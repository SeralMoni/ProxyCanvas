from __future__ import annotations

import io
import time
from dataclasses import dataclass
from typing import Any

import requests
from PIL import Image

import config


MAX_PUBLIC_UPLOAD_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class PublicUploadResult:
    url: str
    content_type: str
    size: int
    compressed: bool = False
    uploader: str = ""


def upload_public_image(
    data: bytes,
    *,
    file_name: str,
    content_type: str,
    timeout: int = 60,
    attempts: int = 3,
) -> PublicUploadResult:
    upload_data, upload_name, upload_type, compressed = _prepare_public_image(data, file_name, content_type)
    last_error: Exception | None = None
    upload_url = f"{config.TELEGRAPH_URL.rstrip('/')}/upload"
    attempts = max(1, int(attempts or 1))

    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(
                upload_url,
                files={"file": (upload_name, upload_data, upload_type)},
                timeout=timeout,
                proxies=config.HTTP_PROXIES,
            )
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code} {response.text[:200]}")

            payload: Any = response.json()
            if isinstance(payload, list) and payload and isinstance(payload[0], dict):
                item = payload[0]
                if item.get("src"):
                    return PublicUploadResult(
                        url=_public_url(config.TELEGRAPH_URL, str(item["src"])),
                        content_type=upload_type,
                        size=len(upload_data),
                        compressed=compressed,
                        uploader=config.TELEGRAPH_URL,
                    )
                if item.get("error"):
                    raise RuntimeError(str(item["error"]))
            raise RuntimeError("返回格式无效")
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(1.5 * attempt, 4.0))

    raise RuntimeError(f"图床上传失败: {last_error}")


def _public_url(base_url: str, src: str) -> str:
    if src.startswith(("http://", "https://")):
        return src
    return f"{base_url.rstrip('/')}/{src.lstrip('/')}"


def _prepare_public_image(data: bytes, file_name: str, content_type: str) -> tuple[bytes, str, str, bool]:
    if len(data) <= MAX_PUBLIC_UPLOAD_BYTES:
        return data, file_name or "image.png", content_type or "image/png", False

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.mode in {"RGBA", "LA", "P"}:
                image = image.convert("RGBA")
            elif image.mode != "RGB":
                image = image.convert("RGB")

            quality = 95
            output = data
            while quality >= 50:
                buffer = io.BytesIO()
                image.save(buffer, format="WEBP", quality=quality)
                output = buffer.getvalue()
                if len(output) <= MAX_PUBLIC_UPLOAD_BYTES:
                    break
                quality -= 5
            stem = (file_name or "image").rsplit(".", 1)[0]
            return output, f"{stem}.webp", "image/webp", True
    except Exception:
        return data, file_name or "image.png", content_type or "image/png", False
