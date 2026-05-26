from __future__ import annotations

import base64
import io
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from PIL import Image

import config
from services.image_files import storage_base
from services.jobs.providers.base import ProviderAdapter, ProviderError
from services.jobs.store import JobStore
from services.reference_inputs import load_reference_image


class OpenAICompatibleImageAdapter(ProviderAdapter):
    """Run configurable OpenAI-style image providers.

    Supported protocols:
    - images: /v1/images/generations and /v1/images/edits
    - chat-completions: /v1/chat/completions returning markdown/data-uri images
    - responses: /v1/responses with the hosted image_generation tool
    """

    def __init__(self, provider_id: str, provider_config: dict[str, Any]):
        self.name = provider_id
        self.config = dict(provider_config or {})

    def run(self, job: dict[str, Any], store: JobStore) -> list[dict[str, Any]]:
        payload = self.normalize_payload(job)
        protocol = _protocol(self.config)
        store.update_job(job["id"], status="running", progress=5)

        if protocol == "chat-completions":
            raw_images = self._run_chat_completions(payload)
        elif protocol == "responses":
            raw_images = self._run_responses(payload)
        else:
            raw_images = self._run_images(payload)

        results = [self._save_image(item, index) for index, item in enumerate(raw_images, start=1)]
        results = [item for item in results if item]
        if not results:
            raise ProviderError(f"{self.name} finished but produced 0 images")
        store.update_job(job["id"], status="saving", progress=95, result_json=results)
        return results

    def _run_images(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        refs = payload.get("image_urls") or []
        url = self._url("/images/edits" if refs or payload.get("mask_data") else "/images/generations")
        n = _int(payload.get("n"), 1)

        if refs or payload.get("mask_data"):
            data = self._common_image_fields(payload)
            data["n"] = str(n)
            files = self._reference_files(refs)
            if payload.get("mask_data"):
                files.append(("mask", ("mask.png", _decode_data_uri(str(payload["mask_data"])), "image/png")))
            response = requests.post(url, headers=self._auth_headers(), data=data, files=files, timeout=self._timeout())
        else:
            body = self._common_image_fields(payload)
            body["n"] = n
            body["response_format"] = str(self.config.get("responseFormat") or "b64_json")
            response = requests.post(url, headers=self._json_headers(), json=body, timeout=self._timeout())

        return _extract_image_items(self._json_response(response))

    def _run_chat_completions(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        stream = bool(self.config.get("stream"))
        body: dict[str, Any] = {
            "model": payload.get("model") or self.config.get("defaultModel"),
            "messages": [{
                "role": "user",
                "content": self._chat_content(payload),
            }],
            "stream": stream,
        }
        for key in ("size", "quality", "n"):
            if payload.get(key) is not None:
                body[key] = payload[key]

        response = requests.post(
            self._url("/chat/completions"),
            headers={**self._json_headers(), **({"Accept": "text/event-stream"} if stream else {})},
            json=body,
            timeout=self._timeout(),
            stream=stream,
        )
        if stream:
            content = self._read_chat_stream(response)
        else:
            data = self._json_response(response)
            content = _chat_message_content(data)
        return _extract_images_from_text(content)

    def _run_responses(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        tool: dict[str, Any] = {"type": "image_generation"}
        for key in ("size", "quality"):
            if payload.get(key):
                tool[key] = payload[key]
        if self.config.get("imageAction"):
            tool["action"] = self.config["imageAction"]

        body = {
            "model": payload.get("model") or self.config.get("defaultModel"),
            "input": [{
                "role": "user",
                "content": self._responses_content(payload),
            }],
            "tools": [tool],
        }
        data = self._json_response(requests.post(self._url("/responses"), headers=self._json_headers(), json=body, timeout=self._timeout()))
        return _extract_responses_images(data)

    def _common_image_fields(self, payload: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "model": payload.get("model") or self.config.get("defaultModel"),
            "prompt": payload.get("prompt") or "",
        }
        for key in ("size", "quality", "background", "moderation", "output_format"):
            if payload.get(key):
                fields[key] = payload[key]
        return fields

    def _chat_content(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        content = [{"type": "text", "text": payload.get("prompt") or ""}]
        for url in _image_urls(payload):
            content.append({"type": "image_url", "image_url": {"url": self._image_url_for_json(url)}})
        return content

    def _responses_content(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        content = [{"type": "input_text", "text": payload.get("prompt") or ""}]
        for url in _image_urls(payload):
            content.append({"type": "input_image", "image_url": self._image_url_for_json(url)})
        return content

    def _reference_files(self, refs: Any) -> list[tuple[str, tuple[str, bytes, str]]]:
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for index, url in enumerate(_image_urls({"image_urls": refs}), start=1):
            data, content_type, suffix = self._image_bytes(url)
            files.append(("image", (f"image_{index}{suffix}", data, content_type)))
        return files

    def _image_url_for_json(self, url: str) -> str:
        if url.startswith("data:") or url.startswith("http://") or url.startswith("https://"):
            return url
        data, content_type, _suffix = self._image_bytes(url)
        return f"data:{content_type};base64,{base64.b64encode(data).decode('utf-8')}"

    def _image_bytes(self, url: str) -> tuple[bytes, str, str]:
        if url.startswith("data:"):
            header, b64 = url.split(",", 1)
            content_type = header[5:].split(";", 1)[0] or "image/png"
            return base64.b64decode(b64), content_type, _suffix_for_content_type(content_type)
        if url.startswith("http://") or url.startswith("https://"):
            image = load_reference_image(url, timeout=30, proxies=config.HTTP_PROXIES)
            return image.data, image.content_type, image.suffix
        if "/api/reference-images/" in url or re.fullmatch(r"[a-fA-F0-9]{64}", url or ""):
            image = load_reference_image(url, timeout=30, proxies=config.HTTP_PROXIES)
            return image.data, image.content_type, image.suffix
        path = Path(url)
        if not path.is_absolute():
            path = storage_base() / url
        data = path.read_bytes()
        content_type = _content_type_for_path(path)
        return data, content_type, path.suffix or _suffix_for_content_type(content_type)

    def _save_image(self, item: dict[str, Any], index: int) -> dict[str, Any] | None:
        image_bytes: bytes | None = None
        source_url = str(item.get("url") or "")
        b64 = item.get("b64_json") or item.get("base64") or item.get("result")
        if isinstance(b64, str) and b64:
            if b64.startswith("data:"):
                image_bytes = _decode_data_uri(b64)
            else:
                image_bytes = base64.b64decode(re.sub(r"\s+", "", b64))
        elif source_url:
            response = requests.get(source_url, timeout=self._timeout(), proxies=config.HTTP_PROXIES)
            response.raise_for_status()
            image_bytes = response.content
        if not image_bytes:
            return None

        root_dir = storage_base()
        root_dir.mkdir(parents=True, exist_ok=True)
        prefix = _safe_filename_prefix(self.config.get("label") or self.name)
        filename = f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{index:02d}_{secrets.token_hex(2)}.png"
        path = root_dir / filename
        path.write_bytes(image_bytes)

        width = height = None
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                width, height = image.size
        except Exception:
            pass

        return {
            "provider": self.name,
            "index": index,
            "saved_path": str(path),
            "filename": filename,
            "width": width,
            "height": height,
            "url": source_url or None,
            "revised_prompt": item.get("revised_prompt"),
        }
    def _url(self, path: str) -> str:
        base = str(self.config.get("baseUrl") or "").rstrip("/")
        if not base:
            raise ProviderError(f"{self.name} baseUrl is empty")
        endpoint = str(self.config.get("endpointPath") or "").strip()
        if endpoint:
            return base + "/" + endpoint.lstrip("/")
        if base.endswith(("/chat/completions", "/responses", "/images/generations", "/images/edits")):
            return base
        return base + path

    def _auth_headers(self) -> dict[str, str]:
        api_key = str(self.config.get("apiKey") or "")
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def _json_headers(self) -> dict[str, str]:
        return {**self._auth_headers(), "Content-Type": "application/json"}

    def _timeout(self) -> int:
        return max(30, _int(self.config.get("timeoutSeconds"), 1200))

    def _json_response(self, response: requests.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise ProviderError(f"HTTP {response.status_code}: {response.text[:500]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(f"Invalid JSON response: {response.text[:300]}") from exc
        return data if isinstance(data, dict) else {"data": data}

    def _read_chat_stream(self, response: requests.Response) -> str:
        if response.status_code >= 400:
            raise ProviderError(f"HTTP {response.status_code}: {response.text[:500]}")
        chunks: list[str] = []
        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace")
            if not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                break
            try:
                event = json.loads(data_str)
            except ValueError:
                continue
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    chunks.append(str(delta["content"]))
        return "".join(chunks)


def _safe_filename_prefix(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "provider"


def _protocol(provider: dict[str, Any]) -> str:
    value = str(provider.get("protocol") or "images").strip().lower()
    aliases = {
        "image": "images",
        "images-api": "images",
        "chat": "chat-completions",
        "chat-completions-image": "chat-completions",
        "responses-api": "responses",
    }
    return aliases.get(value, value if value in {"images", "chat-completions", "responses"} else "images")


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _image_urls(payload: dict[str, Any]) -> list[str]:
    urls = []
    for item in payload.get("image_urls") or []:
        url = item.get("url") if isinstance(item, dict) else item
        if url:
            urls.append(str(url))
    return urls


def _extract_image_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("data", "images", "result", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return [item if isinstance(item, dict) else {"url": item} for item in value]
        if isinstance(value, str):
            return _extract_images_from_text(value)
    return []


def _chat_message_content(data: dict[str, Any]) -> str:
    chunks: list[str] = []
    for choice in data.get("choices") or []:
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    chunks.append(str(item["text"]))
    return "\n".join(chunks)


def _extract_images_from_text(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for match in re.findall(r"!\[[^\]]*]\((data:image/[^)]+)\)", text or ""):
        items.append({"b64_json": match})
    for match in re.findall(r"!\[[^\]]*]\((https?://[^)]+)\)", text or ""):
        items.append({"url": match.strip()})
    if not items:
        for match in re.findall(r"(https?://\S+\.(?:png|jpg|jpeg|webp)(?:\?\S*)?)", text or "", flags=re.I):
            items.append({"url": match.strip()})
    if not items:
        stripped = (text or "").strip()
        if stripped.startswith("data:image/"):
            items.append({"b64_json": stripped})
        elif len(stripped) > 1000 and re.fullmatch(r"[A-Za-z0-9+/=\s]+", stripped):
            items.append({"b64_json": stripped})
    return items


def _extract_responses_images(data: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for output in data.get("output") or []:
        if not isinstance(output, dict):
            continue
        if output.get("type") == "image_generation_call" and output.get("result"):
            items.append({"b64_json": output.get("result"), "revised_prompt": output.get("revised_prompt")})
        for content in output.get("content") or []:
            if isinstance(content, dict):
                if content.get("type") in {"output_image", "image"} and content.get("b64_json"):
                    items.append({"b64_json": content.get("b64_json")})
                if content.get("text"):
                    items.extend(_extract_images_from_text(str(content["text"])))
    return items


def _decode_data_uri(data_uri: str) -> bytes:
    return base64.b64decode(data_uri.split(",", 1)[1] if "," in data_uri else data_uri)


def _suffix_for_content_type(content_type: str) -> str:
    if "jpeg" in content_type:
        return ".jpg"
    if "webp" in content_type:
        return ".webp"
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
