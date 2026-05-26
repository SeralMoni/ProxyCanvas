from __future__ import annotations

import threading
import re
import time
from typing import Any, Callable

import config


ProviderChangeListener = Callable[[], None]

_BUILTIN_ORDER = ["openai", "cliproxy", "sousaku", "nanobanana2", "apimart"]
_LISTENERS: list[ProviderChangeListener] = []
_LOCK = threading.RLock()


def add_change_listener(listener: ProviderChangeListener) -> None:
    with _LOCK:
        if listener not in _LISTENERS:
            _LISTENERS.append(listener)


def _notify_change() -> None:
    with _LOCK:
        listeners = list(_LISTENERS)
    for listener in listeners:
        try:
            listener()
        except Exception:
            pass


def reload_runtime_config() -> None:
    config.apply_runtime_config()
    _notify_change()


def _provider_source(provider_id: str) -> str:
    raw = config.read_providers_settings().get("providers", {})
    return "config/providers.json" if isinstance(raw, dict) and provider_id in raw else "config.py"


def _sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, str]:
    provider_id, _provider = item
    try:
        return (_BUILTIN_ORDER.index(provider_id), provider_id)
    except ValueError:
        return (len(_BUILTIN_ORDER), provider_id)


def provider_settings() -> dict[str, Any]:
    return config.normalized_providers_settings()


def list_providers(*, include_disabled: bool = True) -> list[dict[str, Any]]:
    data = provider_settings()
    items = sorted(data.get("providers", {}).items(), key=_sort_key)
    providers: list[dict[str, Any]] = []
    for provider_id, provider in items:
        if not include_disabled and not provider.get("enabled", True):
            continue
        providers.append(provider_summary(provider_id, provider))
    return providers


def provider_summary(provider_id: str, provider: dict[str, Any] | None = None) -> dict[str, Any]:
    provider = provider or get_provider(provider_id) or {}
    return {
        "id": provider_id,
        "label": provider.get("label", provider_id),
        "type": provider.get("type", ""),
        "protocol": provider.get("protocol", ""),
        "enabled": provider.get("enabled", True),
        "source": _provider_source(provider_id),
        "baseUrl": provider.get("baseUrl", ""),
        "apiKey": provider.get("apiKey", ""),
        "defaultModel": provider.get("defaultModel", ""),
        "models": provider.get("models", []),
        "capabilities": provider.get("capabilities", []),
        "notes": provider.get("notes", ""),
        "configPath": provider.get("configPath", ""),
        "stream": provider.get("stream", False),
        "timeoutSeconds": provider.get("timeoutSeconds"),
        "badgeColor": provider.get("badgeColor", ""),
        "builtin": provider_id in config.DEFAULT_PROVIDERS_SETTINGS["providers"],
    }


def get_provider(provider_id: str) -> dict[str, Any] | None:
    provider = provider_settings().get("providers", {}).get(str(provider_id or "").strip())
    return dict(provider) if isinstance(provider, dict) else None


def is_enabled(provider_id: str) -> bool:
    provider = get_provider(provider_id)
    return bool(provider and provider.get("enabled", True))


def update_provider(provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(provider_id or "").strip()
    if not provider_id:
        raise KeyError("Provider not found")

    current = provider_settings()
    providers = current.setdefault("providers", {})
    if provider_id not in providers:
        raise KeyError("Provider not found")

    provider = dict(providers[provider_id])
    next_models = payload.get("models") if isinstance(payload.get("models"), list) else None
    next_default_model = str(payload.get("defaultModel") or "").strip()
    if not next_default_model:
        current_default_model = str(provider.get("defaultModel") or "").strip()
        if isinstance(next_models, list) and next_models:
            first_model = _first_model_value(next_models)
            next_model_values = {
                str(item.get("value") or "").strip()
                for item in next_models
                if isinstance(item, dict)
            }
            if not current_default_model or (current_default_model and current_default_model not in next_model_values):
                next_default_model = first_model or current_default_model
        else:
            next_default_model = current_default_model
    allowed_keys = {
        "label",
        "type",
        "protocol",
        "enabled",
        "baseUrl",
        "apiKey",
        "defaultModel",
        "models",
        "capabilities",
        "configPath",
        "notes",
        "stream",
        "timeoutSeconds",
        "badgeColor",
        "endpointPath",
        "responseFormat",
        "imageAction",
    }
    for key in allowed_keys:
        if key in payload:
            provider[key] = payload[key]
    if next_default_model:
        provider["defaultModel"] = next_default_model

    providers[provider_id] = provider
    config.write_providers_settings(current)
    reload_runtime_config()
    return provider_summary(provider_id)


def create_provider(payload: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(payload.get("id") or _slug_from_label(payload.get("label") or payload.get("baseUrl") or "custom-api")).strip().lower()
    if not provider_id:
        raise ValueError("Provider id is required")
    if not provider_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Provider id can only contain letters, numbers, hyphen and underscore")

    current = provider_settings()
    providers = current.setdefault("providers", {})
    if provider_id in providers:
        raise ValueError("Provider already exists")

    models = payload.get("models") if isinstance(payload.get("models"), list) else []
    default_model = str(payload.get("defaultModel") or payload.get("model") or _first_model_value(models) or "gpt-image-2")
    provider = {
        "label": str(payload.get("label") or provider_id),
        "type": str(payload.get("type") or "openai-compatible"),
        "protocol": str(payload.get("protocol") or "images"),
        "enabled": bool(payload.get("enabled", True)),
        "baseUrl": str(payload.get("baseUrl") or ""),
        "apiKey": str(payload.get("apiKey") or ""),
        "defaultModel": default_model,
        "models": models if models else [{
            "value": default_model,
            "label": default_model,
            "defaults": {"ratio": "16:9", "quality": "high", "imageCount": 1},
            "controls": [
                {"key": "ratio", "label": "比例", "type": "select", "options": ["1:1", "4:3", "3:4", "16:9", "9:16"]},
                {"key": "quality", "label": "质量", "type": "select", "options": ["low", "medium", "high"]},
                {"key": "imageCount", "label": "数量", "type": "select", "options": [1, 2, 3, 4]},
            ],
            "features": {"referenceImage": True, "mask": True},
        }],
        "capabilities": payload.get("capabilities") if isinstance(payload.get("capabilities"), list) else ["text-to-image", "reference-image"],
        "notes": str(payload.get("notes") or ""),
        "stream": bool(payload.get("stream", False)),
        "timeoutSeconds": int(payload.get("timeoutSeconds") or 1200),
        "badgeColor": str(payload.get("badgeColor") or "#8ecae6"),
    }
    providers[provider_id] = provider
    config.write_providers_settings(current)
    reload_runtime_config()
    return provider_summary(provider_id)


def _slug_from_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:40] or f"custom-api-{int(time.time())}"


def _first_model_value(models: Any) -> str:
    if not isinstance(models, list):
        return ""
    for item in models:
        if isinstance(item, dict):
            value = str(item.get("value") or "").strip()
            if value:
                return value
        elif isinstance(item, str):
            value = str(item).strip()
            if value:
                return value
    return ""


def delete_provider(provider_id: str) -> None:
    provider_id = str(provider_id or "").strip()
    if not provider_id:
        raise KeyError("Provider not found")
    if provider_id in config.DEFAULT_PROVIDERS_SETTINGS["providers"]:
        raise ValueError("Built-in providers cannot be deleted")

    current = provider_settings()
    providers = current.setdefault("providers", {})
    if provider_id not in providers:
        raise KeyError("Provider not found")
    del providers[provider_id]
    config.write_providers_settings(current)
    reload_runtime_config()


def build_job_adapters(*, app: Any, endpoints: dict[str, Any]) -> dict[str, Any]:
    from services.jobs.providers import APIMartAdapter, FlaskEndpointAdapter, OpenAICompatibleImageAdapter, OpenAITaskAdapter, SousakuAdapter

    adapters: dict[str, Any] = {}
    for provider in list_providers(include_disabled=False):
        provider_id = provider["id"]
        provider_type = provider.get("type")
        if provider_type == "sousaku":
            adapters[provider_id] = SousakuAdapter()
        elif provider_type == "openai-compatible" and provider_id not in {"openai", "cliproxy"}:
            adapters[provider_id] = OpenAICompatibleImageAdapter(provider_id, provider)
        elif provider_id == "cliproxy":
            adapters[provider_id] = FlaskEndpointAdapter(
                name=provider_id,
                app=app,
                endpoint=endpoints["cliproxy"],
                path="/api/generate-cliproxy",
            )
        elif provider_id == "nanobanana2":
            adapters[provider_id] = FlaskEndpointAdapter(
                name=provider_id,
                app=app,
                endpoint=endpoints["nanobanana2"],
                path="/api/generate-nanobanana2",
            )
        elif provider_id == "apimart":
            adapters[provider_id] = APIMartAdapter(
                app=app,
                submit_endpoint=endpoints["apimart_submit"],
                status_endpoint=endpoints["apimart_status"],
            )
        elif provider_id == "openai":
            adapters[provider_id] = OpenAITaskAdapter(
                app=app,
                submit_endpoint=endpoints["openai_submit"],
                status_endpoint=endpoints["openai_status"],
            )
    return adapters
