from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

import config
from services import provider_registry
from services.storage_usage import clear_cache, storage_usage


settings_bp = Blueprint("settings", __name__)
APP_SETTINGS_PATH = Path(config.APP_SETTINGS_PATH)
PROVIDERS_SETTINGS_PATH = Path(config.PROVIDERS_SETTINGS_PATH)
def _path_value(path: str | os.PathLike[str], source: str = "config.py") -> dict[str, str]:
    raw = str(path)
    try:
        resolved = str(Path(raw).resolve())
    except Exception:
        resolved = raw
    return {"value": raw, "resolved": resolved, "source": source}


def _display_config_path(path: str | os.PathLike[str]) -> str:
    try:
        return Path(path).resolve().relative_to(Path(config.PROJECT_ROOT).resolve()).as_posix()
    except Exception:
        return str(path)


def _read_app_settings() -> dict[str, Any]:
    return config.read_app_settings()


def _deep_merge(defaults: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalized_app_settings(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    return config.normalized_app_settings(raw)


def _write_app_settings(settings: dict[str, Any]) -> None:
    config.write_app_settings(settings)


def _apply_runtime_settings(settings: dict[str, Any]) -> None:
    provider_registry.reload_runtime_config()


def _source_for(raw_settings: dict[str, Any], *path: str) -> str:
    value: Any = raw_settings
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return "config.py"
        value = value[key]
    return "config/app_settings.json"


def _setting(value: Any, raw_settings: dict[str, Any], *path: str) -> dict[str, Any]:
    return {"value": value, "source": _source_for(raw_settings, *path)}


@settings_bp.route("/api/settings", methods=["GET"])
def get_settings():
    raw_app_settings = _read_app_settings()
    app_settings = _normalized_app_settings(raw_app_settings)
    return jsonify({
        "success": True,
        "data": {
            "ui": {
                "prompt": {
                    "autoClear": _setting(app_settings["ui"]["prompt"]["autoClear"], raw_app_settings, "ui", "prompt", "autoClear"),
                },
                "gallery": {
                    "columns": _setting(app_settings["ui"]["gallery"]["columns"], raw_app_settings, "ui", "gallery", "columns"),
                    "displayMode": _setting(app_settings["ui"]["gallery"]["displayMode"], raw_app_settings, "ui", "gallery", "displayMode"),
                    "pageSize": _setting(app_settings["ui"]["gallery"]["pageSize"], raw_app_settings, "ui", "gallery", "pageSize"),
                    "deleteLocalFile": _setting(app_settings["ui"]["gallery"]["deleteLocalFile"], raw_app_settings, "ui", "gallery", "deleteLocalFile"),
                    "deleteImportedOriginal": _setting(app_settings["ui"]["gallery"]["deleteImportedOriginal"], raw_app_settings, "ui", "gallery", "deleteImportedOriginal"),
                    "selectionColor": _setting(app_settings["ui"]["gallery"]["selectionColor"], raw_app_settings, "ui", "gallery", "selectionColor"),
                    "selectionBoxColor": _setting(app_settings["ui"]["gallery"]["selectionBoxColor"], raw_app_settings, "ui", "gallery", "selectionBoxColor"),
                    "tagColor": _setting(app_settings["ui"]["gallery"]["tagColor"], raw_app_settings, "ui", "gallery", "tagColor"),
                },
            },
            "paths": {
                "projectRoot": _path_value(config.PROJECT_ROOT),
                "saveDir": _path_value(
                    app_settings["storage"]["saveDir"],
                    _source_for(raw_app_settings, "storage", "saveDir"),
                ),
                "jobsDb": _path_value(config.JOBS_DB_PATH),
                "galleryDb": _path_value(config.GALLERY_DB_PATH),
            },
            "server": {
                "backendPort": _setting(app_settings["server"]["backendPort"], raw_app_settings, "server", "backendPort"),
                "frontendPort": _setting(app_settings["server"]["frontendPort"], raw_app_settings, "server", "frontendPort"),
                "useReloader": _setting(app_settings["server"]["useReloader"], raw_app_settings, "server", "useReloader"),
            },
            "gallery": {
                "thumbnailWidth": _setting(app_settings["gallery"]["thumbnailWidth"], raw_app_settings, "gallery", "thumbnailWidth"),
                "thumbnailQuality": _setting(app_settings["gallery"]["thumbnailQuality"], raw_app_settings, "gallery", "thumbnailQuality"),
                "thumbnailCacheMaxGb": _setting(app_settings["gallery"]["thumbnailCacheMaxGb"], raw_app_settings, "gallery", "thumbnailCacheMaxGb"),
            },
            "jobs": {
                "workerEnabled": _setting(app_settings["jobs"]["workerEnabled"], raw_app_settings, "jobs", "workerEnabled"),
                "maxWorkers": _setting(app_settings["jobs"]["maxWorkers"], raw_app_settings, "jobs", "maxWorkers"),
                "pollIntervalSeconds": _setting(app_settings["jobs"]["pollIntervalSeconds"], raw_app_settings, "jobs", "pollIntervalSeconds"),
                "defaultTimeoutSeconds": _setting(app_settings["jobs"]["defaultTimeoutSeconds"], raw_app_settings, "jobs", "defaultTimeoutSeconds"),
                "sousakuStaleTaskSeconds": _setting(app_settings["jobs"]["sousakuStaleTaskSeconds"], raw_app_settings, "jobs", "sousakuStaleTaskSeconds"),
                "providerLimits": _setting(app_settings["jobs"]["providerLimits"], raw_app_settings, "jobs", "providerLimits"),
            },
            "network": {
                "httpProxies": _setting(app_settings["network"]["httpProxies"], raw_app_settings, "network", "httpProxies"),
                "publicUrlTtlSeconds": _setting(app_settings["network"]["publicUrlTtlSeconds"], raw_app_settings, "network", "publicUrlTtlSeconds"),
            },
            "logging": {
                "level": _setting(app_settings["logging"]["level"], raw_app_settings, "logging", "level"),
                "color": _setting(app_settings["logging"]["color"], raw_app_settings, "logging", "color"),
                "sousakuProgressPanel": _setting(app_settings["logging"]["sousakuProgressPanel"], raw_app_settings, "logging", "sousakuProgressPanel"),
            },
            "configFiles": {
                "appSettings": {"path": "config/app_settings.json", "exists": APP_SETTINGS_PATH.exists()},
                "providers": {"path": "config/providers.json", "exists": PROVIDERS_SETTINGS_PATH.exists()},
                "sousaku": {"path": _display_config_path(config.SOUSAKU_CONFIG_PATH), "exists": Path(config.SOUSAKU_CONFIG_PATH).exists()},
            },
        },
    })


@settings_bp.route("/api/settings", methods=["PATCH"])
def update_settings():
    payload = request.get_json(silent=True) or {}
    current = _normalized_app_settings()
    incoming = payload if isinstance(payload, dict) else {}
    merged = _normalized_app_settings(_deep_merge(current, incoming))
    _write_app_settings(merged)
    _apply_runtime_settings(merged)
    return jsonify({"success": True, "data": merged})


@settings_bp.route("/api/settings/reset", methods=["POST"])
def reset_settings():
    defaults = _normalized_app_settings({})
    _write_app_settings(defaults)
    _apply_runtime_settings(defaults)
    return jsonify({"success": True, "data": defaults})


@settings_bp.route("/api/storage/usage", methods=["GET"])
def get_storage_usage():
    try:
        return jsonify({"success": True, "data": storage_usage()})
    except Exception as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 500


@settings_bp.route("/api/storage/cache/<cache_name>/clear", methods=["POST"])
def clear_storage_cache(cache_name: str):
    try:
        return jsonify({"success": True, "data": clear_cache(cache_name)})
    except ValueError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 500


@settings_bp.route("/api/providers", methods=["GET"])
def get_providers():
    return jsonify({"success": True, "data": provider_registry.list_providers()})


@settings_bp.route("/api/providers", methods=["POST"])
def create_provider():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"success": False, "error": {"message": "Invalid provider payload"}}), 400

    try:
        created = provider_registry.create_provider(payload)
    except ValueError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 400
    return jsonify({"success": True, "data": created}), 201


@settings_bp.route("/api/providers/<provider_id>", methods=["PATCH"])
def update_provider(provider_id: str):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"success": False, "error": {"message": "Invalid provider payload"}}), 400

    try:
        updated = provider_registry.update_provider(provider_id, payload)
    except KeyError:
        return jsonify({"success": False, "error": {"message": "Provider not found"}}), 404
    return jsonify({"success": True, "data": updated})


@settings_bp.route("/api/providers/<provider_id>", methods=["DELETE"])
def delete_provider(provider_id: str):
    try:
        provider_registry.delete_provider(provider_id)
    except KeyError:
        return jsonify({"success": False, "error": {"message": "Provider not found"}}), 404
    except ValueError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 400
    return jsonify({"success": True})
