import base64
import hashlib
import os
import sys
import tempfile
import threading
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from services.reference_inputs import load_reference_image
from services.sousaku.account_pool import ACCOUNT_POOL, TokenLease
from services.sousaku.client_factory import create_sousaku_client
from services.sousaku.task_bindings import TASK_BINDINGS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sdk.sousaku import SousakuClient  # noqa: E402
from sdk.sousaku.exceptions import SousakuError, SousakuTaskFailedError  # noqa: E402


_SAVED_BY_TASK: dict[str, dict[str, str]] = {}
_TASK_TOKENS: dict[str, str] = {}
_TASK_LEASES: dict[str, TokenLease] = {}
_TASK_LOCK = threading.RLock()
_SAVED_LOCK = threading.RLock()
_ACCOUNTS_FILE_LOCK = threading.RLock()
_IMAGE_MODEL_DEFAULT_RESOLUTIONS = {
    "gpt-image-2-low": "4k",
    "gpt-image-2": "4k",
    "gpt-image-2-high": "4k",
    "seedream-4.5": "2k",
    "wan-image-2.7-pro": "4k",
}
_IMAGE_MODELS_FIXED_NUMBER = {
    "mj-image-v7",
    "mj-image-niji-7",
}

def _sousaku_resolution(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return normalized.lower() if normalized in {"1K", "2K", "4K"} else str(value or "").strip()


def create_task(data: dict[str, Any]) -> dict[str, Any]:
    prompt = data.get("prompt", "")
    if not prompt:
        return {"success": False, "error": {"message": "prompt is required"}}

    temp_paths: list[str] = []
    active_lease: TokenLease | None = None
    task_id = ""
    try:
        metadata_client = create_sousaku_client()
        n = _safe_int(data.get("n"), default=1, minimum=1, maximum=10)
        ratio = data.get("size") or data.get("ratio") or "1:1"
        model = data.get("model") or "medium"
        resolved_model = metadata_client.normalize_model(model)
        if resolved_model in _IMAGE_MODELS_FIXED_NUMBER:
            n = 4
        resolution = ""
        if resolved_model in _IMAGE_MODEL_DEFAULT_RESOLUTIONS:
            resolution = _sousaku_resolution(data.get("resolution") or _IMAGE_MODEL_DEFAULT_RESOLUTIONS[resolved_model])
        auto_optimize = bool(data.get("auto_optimize", False))
        estimated_credits = metadata_client.estimate_credits(resolved_model, n)
        temp_paths = _reference_images_to_temp_files(data.get("image_urls") or [])
        excluded_token_hashes: set[str] = set()
        attempted_tokens: list[str] = []
        last_error = ""

        while True:
            lease: TokenLease | None = None
            try:
                lease = ACCOUNT_POOL.acquire(
                    required_credits=estimated_credits,
                    job_id=data.get("_job_id"),
                    exclude_token_hashes=excluded_token_hashes,
                )
                active_lease = lease
            except Exception as exc:
                if last_error:
                    return {
                        "success": False,
                        "error": {
                            "message": (
                                f"Sousaku 账号余额不足，已尝试 {len(attempted_tokens)} 个本地额度满足的账号。"
                                f"最后错误: {last_error}"
                            ),
                        },
                    }
                return {"success": False, "error": {"message": str(exc)}}

            attempted_tokens.append(lease.token_masked)
            client = create_sousaku_client(token=lease.token)
            account_before = _account_snapshot_from_record(lease.account, lease)

            try:
                reference_images = client.upload_reference_images(temp_paths) if temp_paths else []
                task_id = client.create_image(
                    prompt,
                    model=model,
                    ratio=ratio,
                    resolution=resolution,
                    auto_optimize=auto_optimize,
                    number=n,
                    reference_images=reference_images,
                )
                _bind_task(task_id, lease, data.get("_job_id"))
                return {
                    "success": True,
                    "meta": {
                        "account": account_before,
                        "model": resolved_model,
                        "estimated_credits": estimated_credits,
                        "credit_before": (account_before or {}).get("total_credit"),
                        "credit_after": None,
                        "account_attempts": attempted_tokens,
                    },
                    "data": [{
                        "status": "submitted",
                        "task_id": task_id,
                    }],
                }
            except Exception as exc:
                last_error = str(exc)
                if _looks_like_insufficient_credit_error(exc):
                    ACCOUNT_POOL.report_failure(lease.token_hash, last_error)
                    try:
                        _refresh_account_record_for_token(lease.token)
                    except Exception:
                        pass
                    excluded_token_hashes.add(lease.token_hash)
                    lease.release()
                    active_lease = None
                    continue

                if _looks_like_token_error(exc):
                    ACCOUNT_POOL.report_failure(lease.token_hash, last_error)
                    try:
                        _refresh_account_record_for_token(lease.token)
                    except Exception:
                        pass
                lease.release()
                active_lease = None
                return {"success": False, "error": {"message": last_error}}
    except Exception as exc:
        return {"success": False, "error": {"message": str(exc)}}
    finally:
        if active_lease and not task_id:
            active_lease.release()
        for path in temp_paths:
            try:
                os.remove(path)
            except OSError:
                pass


def get_task(task_id: str) -> dict[str, Any]:
    client = _client_for_task(task_id)
    try:
        task = client.get_task_status(task_id)
        images = []
        with _SAVED_LOCK:
            saved_for_task = _SAVED_BY_TASK.setdefault(task_id, {})
        for index, image in enumerate(task.images, start=1):
            saved_path = saved_for_task.get(image.url)
            if not saved_path:
                try:
                    filename = _image_filename(task_id, index, image.url)
                    saved_path = client.download_image(image, save_dir=config.SOUSAKU_SAVE_DIR, filename=filename)
                    saved_for_task[image.url] = saved_path
                except Exception:
                    saved_path = None

            images.append({
                "url": image.url,
                "saved_path": saved_path,
                "width": image.width,
                "height": image.height,
                "thumbnail_url": image.thumbnail_url,
                "file_id": image.file_id,
                "content_id": image.content_id,
                "download_failed": saved_path is None,
            })

        status = task.status.lower()
        response: dict[str, Any] = {
            "status": status,
            "data": {
                "status": status,
                "task_id": task.task_id,
                "progress": task.progress,
                "result": {"images": images},
            },
        }
        if task.is_failed:
            response["error"] = {"message": _task_error_message(task.raw)}
        if task.is_success or task.is_failed:
            release_task_account(task_id)
        return response
    except SousakuTaskFailedError as exc:
        release_task_account(task_id)
        return {
            "status": "failed",
            "error": {"message": exc.message},
            "data": {
                "status": "failed",
                "task_id": task_id,
                "result": {"images": []},
            },
        }
    except SousakuError as exc:
        release_task_account(task_id)
        return {"status": "failed", "error": {"message": str(exc)}}
    except Exception as exc:
        if _looks_like_token_error(exc):
            lease = _lease_for_task(task_id)
            if lease:
                ACCOUNT_POOL.report_failure(lease.token_hash, str(exc))
        release_task_account(task_id)
        return {"status": "failed", "error": {"message": str(exc)}}


def refresh_account_records() -> list[dict[str, Any]]:
    client = create_sousaku_client()
    with _ACCOUNTS_FILE_LOCK:
        return client.save_account_records(include_token=True, include_raw=False)


def refresh_account_records_for_tokens(tokens: list[str]) -> list[dict[str, Any]]:
    records = []
    normalized_tokens = [str(token).strip() for token in tokens if str(token).strip()]
    if not normalized_tokens:
        return records

    for token in normalized_tokens:
        record = _refresh_account_record_for_token(token)
        records.append(record)
    return records


def refresh_task_account(task_id: str) -> dict[str, Any] | None:
    task_token = _token_for_task(task_id)
    if not task_token:
        return None
    record = _refresh_account_record_for_token(task_token)
    lease = _lease_for_task(task_id)
    if lease:
        ACCOUNT_POOL.report_success(lease.token_hash)
    return record


def release_task_account(task_id: str) -> None:
    with _TASK_LOCK:
        lease = _TASK_LEASES.pop(task_id, None)
    if lease:
        lease.release()


def _bind_task(task_id: str, lease: TokenLease, job_id: str | None) -> None:
    with _TASK_LOCK:
        _TASK_TOKENS[task_id] = lease.token
        _TASK_LEASES[task_id] = lease
    TASK_BINDINGS.bind(
        task_id=task_id,
        job_id=str(job_id) if job_id else None,
        token_hash=lease.token_hash,
        token_masked=lease.token_masked,
    )


def _lease_for_task(task_id: str) -> TokenLease | None:
    with _TASK_LOCK:
        return _TASK_LEASES.get(task_id)


def _token_for_task(task_id: str) -> str | None:
    with _TASK_LOCK:
        token = _TASK_TOKENS.get(task_id)
    if token:
        return token
    binding = TASK_BINDINGS.get(task_id)
    if not binding:
        return None
    return ACCOUNT_POOL.token_for_hash(str(binding.get("token_hash") or ""))


def _client_for_task(task_id: str) -> SousakuClient:
    token = _token_for_task(task_id)
    return create_sousaku_client(token=token) if token else create_sousaku_client()


def _refresh_account_record_for_token(token: str) -> dict[str, Any]:
    client = create_sousaku_client(token=token)
    try:
        record = client.get_account_record(include_token=True, include_raw=False)
    except Exception as exc:
        record = {
            "token": token,
            "token_masked": client._mask_token(token),
            "error": str(exc),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    with _ACCOUNTS_FILE_LOCK:
        _merge_account_record(client.accounts_path, record)
    return record


def _merge_account_record(accounts_path: str | None, record: dict[str, Any]) -> None:
    if not accounts_path:
        return
    path = Path(accounts_path)
    payload: dict[str, Any] = {"accounts": []}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {"accounts": []}

    accounts = payload.get("accounts") if isinstance(payload.get("accounts"), list) else []
    token = record.get("token")
    user_id = record.get("user_id")
    token_masked = record.get("token_masked")
    updated = False
    for index, account in enumerate(accounts):
        if not isinstance(account, dict):
            continue
        if (token and account.get("token") == token) or (user_id and account.get("user_id") == user_id) or (token_masked and account.get("token_masked") == token_masked):
            accounts[index] = record
            updated = True
            break
    if not updated:
        accounts.append(record)

    from datetime import datetime, timezone

    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload["count"] = len(accounts)
    payload["accounts"] = accounts
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _reference_images_to_temp_files(image_urls: list[Any]) -> list[str]:
    paths = []
    for idx, item in enumerate(image_urls):
        url = item.get("url", "") if isinstance(item, dict) else str(item or "")
        if not url:
            continue
        suffix = ".png"
        try:
            if url.startswith("data:"):
                header, b64_data = url.split(",", 1)
                mime_type = header.split(";", 1)[0].split(":", 1)[1] if ":" in header else "image/png"
                suffix = _suffix_from_content_type(mime_type)
                content = base64.b64decode(b64_data, validate=True)
            else:
                image = load_reference_image(url, timeout=60, proxies=config.HTTP_PROXIES)
                suffix = image.suffix or _suffix_from_url(url)
                content = image.data

            handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix=f"sousaku_ref_{idx}_")
            with handle:
                handle.write(content)
            paths.append(handle.name)
        except Exception:
            continue
    return paths


def _safe_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _safe_account_snapshot(client: SousakuClient) -> dict[str, Any] | None:
    try:
        profile = client.get_user_profile()
    except Exception:
        return None
    return _account_snapshot_from_profile(client, profile)


def _account_snapshot_from_profile(client: SousakuClient, profile: Any) -> dict[str, Any]:
    account_name = profile.user_email or profile.nick_name or profile.user_name or profile.user_id
    return {
        "account": account_name,
        "user_id": profile.user_id,
        "nick_name": profile.nick_name,
        "user_email": profile.user_email,
        "share_code": profile.share_code,
        "inviter_share_code": profile.inviter_share_code,
        "package_level": profile.package_level,
        "total_credit": profile.total_credit,
        "subscription_credit": profile.subscription_credit,
        "permanent_credit": profile.permanent_credit,
        "running_task_count": profile.running_task_count,
        "token_masked": client._mask_token(client.token),
    }


def _account_snapshot_from_record(record: dict[str, Any] | None, lease: TokenLease) -> dict[str, Any]:
    record = record if isinstance(record, dict) else {}
    account_name = (
        record.get("user_email")
        or record.get("nick_name")
        or record.get("user_name")
        or record.get("user_id")
        or lease.token_masked
    )
    return {
        "account": account_name,
        "user_id": record.get("user_id"),
        "nick_name": record.get("nick_name"),
        "user_email": record.get("user_email"),
        "share_code": record.get("share_code"),
        "inviter_share_code": record.get("inviter_share_code"),
        "package_level": record.get("package_level"),
        "total_credit": record.get("total_credit"),
        "subscription_credit": record.get("subscription_credit"),
        "permanent_credit": record.get("permanent_credit"),
        "running_task_count": record.get("running_task_count"),
        "token_masked": lease.token_masked,
    }


def _looks_like_token_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "token",
        "auth",
        "unauthorized",
        "forbidden",
        "credit",
        "quota",
        "balance",
        "account",
        "rate limit",
        "too many",
        "insufficient",
    )
    return any(marker in text for marker in markers)


def _looks_like_insufficient_credit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "insufficient credit",
        "not enough credit",
        "credit insufficient",
        "余额不足",
        "额度不足",
    )
    return any(marker in text for marker in markers)


def _image_filename(task_id: str, index: int, url: str) -> str:
    url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12] if url else f"{index:02d}"
    return f"sousaku_{task_id[:16]}_{url_hash}.{_suffix_from_url(url).lstrip('.') or 'png'}"


def _suffix_from_content_type(content_type: str) -> str:
    value = content_type.lower().split(";", 1)[0].strip()
    if value == "image/png":
        return ".png"
    if value in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if value == "image/webp":
        return ".webp"
    return ".png"


def _suffix_from_url(url: str) -> str:
    path = url.lower().split("?", 1)[0]
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        if path.endswith(suffix):
            return ".jpg" if suffix == ".jpeg" else suffix
    return ".png"


def _task_error_message(task: dict[str, Any]) -> str:
    message = task.get("error_message")
    if message:
        return str(message)
    if task.get("is_nsfw_error"):
        return "content rejected by model compliance check"
    return "Sousaku task failed"
