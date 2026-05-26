from __future__ import annotations

import json
import sqlite3

from flask import Blueprint, jsonify, request, send_file

import config
from services import reference_library
from services.reference_inputs import import_remote_reference_image


references_bp = Blueprint("references", __name__)


@references_bp.route("/api/reference-images", methods=["POST"])
def upload_reference_image():
    try:
        if "file" in request.files:
            file = request.files["file"]
            data = file.read()
            asset = reference_library.save_reference_file(
                data,
                filename=file.filename or "reference.png",
                content_type=file.content_type or "image/png",
            )
            return jsonify({"success": True, "data": asset.to_dict()})

        payload = request.get_json(silent=True) or {}
        url = str(payload.get("url") or "").strip() if isinstance(payload, dict) else ""
        if url.startswith(("http://", "https://")):
            imported = import_remote_reference_image(url, timeout=60, name=payload.get("name"))
            asset = reference_library.get_reference(imported.ref_id or "")
            return jsonify({"success": True, "data": asset.to_dict()})

        return jsonify({"success": False, "error": {"message": "No file or URL provided"}}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 500


@references_bp.route("/api/reference-images", methods=["GET"])
def list_reference_images():
    try:
        limit = _clamp_int(request.args.get("limit"), default=120, minimum=1, maximum=500)
        offset = _clamp_int(request.args.get("offset"), default=0, minimum=0, maximum=1_000_000)
        refs = reference_library.list_references(limit=limit, offset=offset)
        return jsonify({"success": True, "data": refs, "total": reference_library.count_references()})
    except Exception as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 500


@references_bp.route("/api/reference-images/<ref_id>", methods=["GET"])
def serve_reference_image(ref_id: str):
    try:
        asset = reference_library.get_reference(ref_id)
        if not asset.path.exists():
            return jsonify({"success": False, "error": {"message": "Reference image not found"}}), 404
        return send_file(asset.path, mimetype=asset.content_type, max_age=60 * 60 * 24, conditional=True)
    except FileNotFoundError:
        return jsonify({"success": False, "error": {"message": "Reference image not found"}}), 404
    except Exception as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 500


@references_bp.route("/api/reference-images/<ref_id>/thumbnail", methods=["GET"])
def serve_reference_thumbnail(ref_id: str):
    try:
        width = _clamp_int(request.args.get("w"), default=512, minimum=128, maximum=1536)
        quality = _clamp_int(request.args.get("q"), default=82, minimum=45, maximum=92)
        path = reference_library.thumbnail_path(ref_id, width=width, quality=quality)
        return send_file(path, mimetype="image/webp", max_age=60 * 60 * 24 * 30, conditional=True)
    except FileNotFoundError:
        return jsonify({"success": False, "error": {"message": "Reference image not found"}}), 404
    except Exception as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 500


@references_bp.route("/api/reference-images/<ref_id>", methods=["DELETE"])
def delete_reference_image(ref_id: str):
    try:
        if _is_reference_used_by_active_job(ref_id):
            return jsonify({"success": False, "error": {"message": "该参考图正在被运行中的任务使用，暂时不能删除"}}), 409
        reference_library.delete_reference(ref_id)
        return jsonify({"success": True})
    except FileNotFoundError:
        return jsonify({"success": False, "error": {"message": "Reference image not found"}}), 404
    except Exception as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 500


def _clamp_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _is_reference_used_by_active_job(ref_id: str) -> bool:
    connection = None
    try:
        normalized = reference_library.normalize_ref_id(ref_id)
        connection = sqlite3.connect(config.JOBS_DB_PATH)
        rows = connection.execute(
            "SELECT input_images_json FROM jobs WHERE status IN ('queued', 'submitting', 'running', 'saving')"
        ).fetchall()
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()

    for (raw_images,) in rows:
        try:
            images = json.loads(raw_images or "[]")
        except json.JSONDecodeError:
            continue
        if _images_contain_ref(images, normalized):
            return True
    return False


def _images_contain_ref(images, ref_id: str) -> bool:
    if not isinstance(images, list):
        return False
    for item in images:
        if isinstance(item, dict):
            if str(item.get("ref_id") or "").lower() == ref_id:
                return True
            if reference_library.ref_id_from_url(str(item.get("url") or "")) == ref_id:
                return True
    return False
