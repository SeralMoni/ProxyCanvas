from __future__ import annotations

"""Translate unified job params into provider-specific legacy payloads.

This module intentionally stays thin: it resolves the active model schema, then
delegates the actual field mapping to small provider translators. That keeps
provider differences isolated and makes the compatibility layer easier to
reason about when new providers are added.
"""

from typing import Any

from services.jobs.payloads.apimart import translate as translate_apimart
from services.jobs.payloads.cliproxy import translate as translate_cliproxy
from services.jobs.payloads.common import (
    flatten_payload,
    has_control,
    image_count,
    lookup_model_config,
    model_value,
    payload_config,
    request_model_config,
    size_for_model,
    strip_schema_fields,
)
from services.jobs.payloads.nanobanana2 import translate as translate_nanobanana2
from services.jobs.payloads.openai import translate as translate_openai
from services.jobs.payloads.sousaku import translate as translate_sousaku


def translate_provider_payload(provider: str, payload: dict[str, Any]) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()
    source = flatten_payload(payload)
    request_config = request_model_config(source)
    model = model_value(provider, source, request_config)
    model_config = lookup_model_config(provider, model) or request_config
    ratio = str(source.get("ratio") or source.get("size") or "")
    resolution = str(source.get("resolution") or "")
    quality = source.get("quality")
    n = image_count(source, model_config)

    if provider == "sousaku":
        return translate_sousaku(source, model=model, model_config=model_config, ratio=ratio, resolution=resolution, quality=quality, n=n)
    if provider == "nanobanana2":
        return translate_nanobanana2(source, model=model, model_config=model_config, ratio=ratio, resolution=resolution, quality=quality, n=n)
    if provider == "cliproxy":
        return translate_cliproxy(source, model=model, model_config=model_config, ratio=ratio, resolution=resolution, quality=quality, n=n)
    if provider == "openai":
        return translate_openai(source, model=model, model_config=model_config, ratio=ratio, resolution=resolution, quality=quality, n=n)
    if provider == "apimart":
        return translate_apimart(source, model=model, model_config=model_config, ratio=ratio, resolution=resolution, quality=quality, n=n)

    translated = {
        "prompt": source.get("prompt") or "",
    }
    for key in ("image_urls", "mask_data", "feather", "background", "moderation", "output_format"):
        if source.get(key) is not None:
            translated[key] = source[key]
    if model:
        translated["model"] = model
    _apply_payload_mapping(translated, source, model_config)
    if has_control(model_config, "imageCount") and n:
        translated[_payload_target_for(model_config, "imageCount", "n")] = n
    if has_control(model_config, "ratio"):
        size_target = _payload_target_for(model_config, "ratio", "size")
        translated[size_target] = size_for_model(
            model_config,
            ratio,
            resolution,
            fallback=ratio or str(source.get(size_target) or source.get("size") or "16:9"),
        )
    if has_control(model_config, "resolution") and resolution:
        translated[_payload_target_for(model_config, "resolution", "resolution")] = resolution
    if has_control(model_config, "quality") and quality:
        translated[_payload_target_for(model_config, "quality", "quality")] = quality
    if model == "gpt-image-2-official" and has_control(model_config, "quality"):
        translated[_payload_target_for(model_config, "quality", "quality")] = quality or "high"
        translated["moderation"] = source.get("moderation") or "low"
    return strip_schema_fields(translated)


def _apply_payload_mapping(translated: dict[str, Any], source: dict[str, Any], model_config: dict[str, Any]) -> None:
    mapping = payload_config(model_config)
    if not isinstance(mapping, dict):
        return
    for target, source_key in mapping.items():
        if target == "pixelSizeMap":
            continue
        source_name = str(source_key or "").strip()
        if not source_name:
            continue
        if source_name in source and source[source_name] is not None:
            translated[str(target)] = source[source_name]


def _payload_target_for(model_config: dict[str, Any], key: str, fallback: str) -> str:
    mapping = payload_config(model_config)
    if isinstance(mapping, dict):
        for target, source_key in mapping.items():
            if str(source_key or "").strip() == key:
                return str(target)
    return fallback
