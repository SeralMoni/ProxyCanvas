import hashlib
import json
import mimetypes
import os
import re
import time
from datetime import datetime, timezone
from collections.abc import Iterable
from typing import Any, Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .exceptions import SousakuAPIError, SousakuAuthError, SousakuTaskFailedError, SousakuTimeoutError
from .models import SousakuImage, SousakuModelConfig, SousakuTask, SousakuUserProfile


DEFAULT_BASE_URL = "https://api.sousaku.ai"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_CONFIG_PATH = "sousaku_config.json"
MODEL_ALIASES = {
    "gpt-image-2 4k low": "gpt-image-2-low",
    "gpt-image-2 low": "gpt-image-2-low",
    "gpt image 2.0 low": "gpt-image-2-low",
    "gpt-image-2-low": "gpt-image-2-low",
    "low": "gpt-image-2-low",
    "gpt-image-2-4k": "gpt-image-2",
    "gpt-image-2 4k medium": "gpt-image-2",
    "gpt-image-2 medium": "gpt-image-2",
    "gpt image 2.0 medium": "gpt-image-2",
    "gpt-image-2-medium": "gpt-image-2",
    "gpt-image-2": "gpt-image-2",
    "medium": "gpt-image-2",
    "gpt-image-2-high-4k": "gpt-image-2-high",
    "gpt-image-2 4k high": "gpt-image-2-high",
    "gpt-image-2 high": "gpt-image-2-high",
    "gpt image 2.0 high": "gpt-image-2-high",
    "gpt-image-2-high": "gpt-image-2-high",
    "high": "gpt-image-2-high",
    "seedream": "seedream-4.5",
    "seedream-4.5": "seedream-4.5",
    "wan": "wan-image-2.7-pro",
    "wan image": "wan-image-2.7-pro",
    "wan image 2.7 pro": "wan-image-2.7-pro",
    "wan-image-2.7-pro": "wan-image-2.7-pro",
    "mj": "mj-image-v7",
    "mj v7": "mj-image-v7",
    "midjourney": "mj-image-v7",
    "midjourney v7": "mj-image-v7",
    "mj-image-v7": "mj-image-v7",
    "niji": "mj-image-niji-7",
    "niji 7": "mj-image-niji-7",
    "mj niji": "mj-image-niji-7",
    "mj niji 7": "mj-image-niji-7",
    "midjourney niji": "mj-image-niji-7",
    "midjourney niji 7": "mj-image-niji-7",
    "mj-image-niji-7": "mj-image-niji-7",
    "seedance": "seedance-lite",
    "seedance-lite": "seedance-lite",
}
DEFAULT_MODEL_CONFIGS = {
    "gpt-image-2-low": SousakuModelConfig(
        model="gpt-image-2-low",
        label="GPT Image 2.0 Low",
        credits_per_image=2,
    ),
    "gpt-image-2": SousakuModelConfig(
        model="gpt-image-2",
        label="GPT Image 2.0 Medium",
        credits_per_image=4,
    ),
    "gpt-image-2-high": SousakuModelConfig(
        model="gpt-image-2-high",
        label="GPT Image 2.0 High",
        credits_per_image=13,
    ),
    "seedream-4.5": SousakuModelConfig(
        model="seedream-4.5",
        label="Seedream 4.5",
        credits_per_image=2,
    ),
    "wan-image-2.7-pro": SousakuModelConfig(
        model="wan-image-2.7-pro",
        label="WAN Image 2.7 Pro",
        credits_per_image=2,
    ),
    "mj-image-v7": SousakuModelConfig(
        model="mj-image-v7",
        label="Midjourney V7",
        credits_per_image=2,
    ),
    "mj-image-niji-7": SousakuModelConfig(
        model="mj-image-niji-7",
        label="Midjourney Niji 7",
        credits_per_image=2,
    ),
    "seedance-lite": SousakuModelConfig(
        model="seedance-lite",
        label="Seedance Lite",
        credits_per_image=3,
    ),
}
IMAGE_MODELS_SUPPORTING_RESOLUTION = {
    "gpt-image-2-low",
    "gpt-image-2",
    "gpt-image-2-high",
    "seedream-4.5",
    "wan-image-2.7-pro",
}
IMAGE_MODEL_DEFAULT_RESOLUTIONS = {
    "gpt-image-2-low": "4k",
    "gpt-image-2": "4k",
    "gpt-image-2-high": "4k",
    "seedream-4.5": "2k",
    "wan-image-2.7-pro": "4k",
}
IMAGE_MODELS_FIXED_NUMBER = {
    "mj-image-v7",
    "mj-image-niji-7",
}


class SousakuClient:
    def __init__(
        self,
        tokens: str | Iterable[str] | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 30,
        retry_total: int = 3,
        auto_rotate_token: bool = True,
        model_configs: dict[str, int | SousakuModelConfig | dict[str, Any]] | None = None,
        save_dir: str | None = None,
        accounts_path: str | None = None,
        generation_timeout: int = 1200,
        poll_interval: float = 3,
        min_credit_threshold: int = 5,
        config_path: str | None = None,
        session: requests.Session | None = None,
    ):
        self.tokens = self._normalize_tokens(tokens)
        if not self.tokens:
            raise ValueError("Sousaku token is required. Pass tokens=... or set SOUSAKU_TOKEN.")

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.auto_rotate_token = auto_rotate_token
        self.save_dir = save_dir
        self.accounts_path = accounts_path
        self.generation_timeout = generation_timeout
        self.poll_interval = poll_interval
        self.min_credit_threshold = int(min_credit_threshold)
        self.config_path = config_path
        self.token_index = 0
        self.session = session or requests.Session()
        self.model_configs = self._build_model_configs(model_configs or self._model_configs_from_env())

        retries = Retry(
            total=retry_total,
            backoff_factor=1,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=("GET", "POST", "PUT"),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    @classmethod
    def from_env(cls, env_name: str = "SOUSAKU_TOKEN", **kwargs: Any) -> "SousakuClient":
        if "save_dir" not in kwargs:
            kwargs["save_dir"] = os.getenv("SOUSAKU_SAVE_DIR") or None
        return cls(tokens=os.getenv(env_name), **kwargs)

    @classmethod
    def from_config(cls, path: str = DEFAULT_CONFIG_PATH, **kwargs: Any) -> "SousakuClient":
        config_path = os.path.abspath(path)
        config_dir = os.path.dirname(config_path)
        with open(config_path, "r", encoding="utf-8-sig") as file:
            config = json.load(file)
        save_dir = cls._resolve_config_path(config.get("save_dir"), config_dir)
        accounts_path = cls._resolve_config_path(config.get("accounts_path"), config_dir)
        tokens = cls._normalize_tokens(config.get("tokens") or config.get("token"))
        disabled_tokens = set(cls._normalize_tokens(config.get("disabled_tokens") or []))
        active_tokens = [token for token in tokens if token not in disabled_tokens]

        merged = {
            "tokens": active_tokens,
            "base_url": config.get("base_url", DEFAULT_BASE_URL),
            "timeout": config.get("timeout", 30),
            "retry_total": config.get("retry_total", 3),
            "auto_rotate_token": config.get("auto_rotate_token", True),
            "model_configs": config.get("model_configs") or config.get("model_credits"),
            "save_dir": save_dir,
            "accounts_path": accounts_path,
            "generation_timeout": config.get("generation_timeout", 1200),
            "poll_interval": config.get("poll_interval", 3),
            "min_credit_threshold": config.get("min_credit_threshold", 5),
            "config_path": config_path,
        }
        merged.update(kwargs)
        return cls(**merged)

    @staticmethod
    def _normalize_tokens(tokens: str | Iterable[str] | None) -> list[str]:
        if tokens is None:
            tokens = os.getenv("SOUSAKU_TOKEN", "")
        if isinstance(tokens, str):
            return [token.strip() for token in tokens.replace(";", ",").split(",") if token.strip()]
        return [str(token).strip() for token in tokens if str(token).strip()]

    @staticmethod
    def _resolve_config_path(path: str | None, base_dir: str) -> str | None:
        if not path:
            return None
        return path if os.path.isabs(path) else os.path.join(base_dir, path)

    @property
    def token(self) -> str:
        return self.tokens[self.token_index]

    def set_token(self, token: str) -> None:
        token = token.strip()
        if not token:
            raise ValueError("token cannot be empty")
        if token not in self.tokens:
            self.tokens.insert(0, token)
        self.token_index = self.tokens.index(token)

    def add_token(self, token: str) -> None:
        token = token.strip()
        if token and token not in self.tokens:
            self.tokens.append(token)

    def remove_token(self, token: str, *, persist: bool = True) -> bool:
        token = token.strip()
        if token not in self.tokens:
            return False
        remove_index = self.tokens.index(token)
        self.tokens.pop(remove_index)
        if not self.tokens:
            raise SousakuAuthError("All Sousaku tokens were removed.")
        if self.token_index >= len(self.tokens):
            self.token_index = len(self.tokens) - 1
        elif remove_index <= self.token_index and self.token_index > 0:
            self.token_index -= 1
        if persist:
            self._persist_tokens_to_config()
        return True

    def rotate_token(self) -> str:
        self.token_index = (self.token_index + 1) % len(self.tokens)
        return self.token

    def headers(self, *, json_content: bool = True) -> dict[str, str]:
        headers = {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "authorization": self.token,
            "cookie": f"pp_user_token={self.token}",
            "origin": "https://sousaku.ai",
            "referer": "https://sousaku.ai/",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "x-sousaku-version": "2.0.0",
        }
        if json_content:
            headers["content-type"] = "application/json"
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        attempts = len(self.tokens) if self.auto_rotate_token else 1
        last_response: requests.Response | None = None

        for _ in range(attempts):
            response = self.session.request(method, url, timeout=kwargs.pop("timeout", self.timeout), **kwargs)
            last_response = response
            if response.status_code not in {401, 403}:
                response.raise_for_status()
                return response
            if attempts > 1:
                self.rotate_token()

        detail = last_response.text[:500] if last_response is not None else ""
        raise SousakuAuthError(f"All Sousaku tokens were rejected. Last response: {detail}")

    def _json(self, response: requests.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise SousakuAPIError(f"Invalid JSON response: {response.text[:500]}") from exc
        if data.get("success") is False:
            message = data.get("error_message") or data.get("message") or data.get("error") or data
            raise SousakuAPIError(f"Sousaku API error: {message}")
        return data

    def upload_reference_images(self, file_paths: Iterable[str]) -> list[dict[str, Any]]:
        paths = [os.fspath(path) for path in file_paths]
        if not paths:
            return []

        for path in paths:
            if not os.path.isfile(path):
                raise FileNotFoundError(path)

        payload = [{"action": 6, "file_name": os.path.basename(path)} for path in paths]
        data = self._json(self._request(
            "POST",
            "/v1/common/upload/batch",
            headers=self.headers(),
            json=payload,
        ))
        upload_items = data.get("data") or []
        if len(upload_items) < len(paths):
            raise SousakuAPIError(f"Upload URL count mismatch: {data}")

        results: list[dict[str, Any]] = []
        for path, item in zip(paths, upload_items):
            upload_url = item.get("upload_url")
            if not upload_url:
                raise SousakuAPIError(f"Missing upload_url in upload response: {item}")

            mime_type = item.get("original_mime_type") or mimetypes.guess_type(path)[0] or "image/jpeg"
            with open(path, "rb") as file:
                put_response = self.session.put(
                    upload_url,
                    headers={"Content-Type": mime_type},
                    data=file,
                    timeout=self.timeout,
                )
            put_response.raise_for_status()

            results.append({
                "file_id": item.get("file_id"),
                "download_url": item.get("download_url"),
                "thumbnail_url": item.get("thumbnail_url") or item.get("download_url"),
                "mime_type": mime_type,
                "raw": item,
            })

        return results

    def create_image(
        self,
        prompt: str,
        *,
        model: str = DEFAULT_MODEL,
        ratio: str = "1:1",
        resolution: str = "",
        number: int = 1,
        reference_images: list[dict[str, Any]] | None = None,
        credits: int | None = None,
        source: int | str | None = None,
        auto_optimize: bool = False,
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        if not prompt:
            raise ValueError("prompt cannot be empty")
        if number < 1:
            raise ValueError("number must be >= 1")

        model = self.normalize_model(model)
        if model in IMAGE_MODELS_FIXED_NUMBER:
            number = 4
            resolution = ""
        elif model not in IMAGE_MODELS_SUPPORTING_RESOLUTION:
            resolution = ""
        elif not resolution:
            resolution = IMAGE_MODEL_DEFAULT_RESOLUTIONS.get(model, "")
        refs = self._normalize_reference_images(reference_images or [])
        final_prompt = prompt

        payload = {
            "source": source if source is not None else 2,
            "prompt": final_prompt,
            "media": {"reference_images": refs, "characters": []},
            "parameters": {
                "ratio": ratio,
                "size": "",
                "resolution": resolution,
                "auto_optimize": auto_optimize,
            },
            "number": number,
            "credits": credits if credits is not None else self.estimate_credits(model, number),
            "model": model,
        }
        if extra_payload:
            payload.update(extra_payload)

        data = self._json(self._request(
            "POST",
            "/v1/generations/create_image",
            headers=self.headers(),
            json=payload,
        ))
        task_id = self._extract_task_id(data)
        if not task_id:
            raise SousakuAPIError(f"Could not extract task_id: {data}")
        return task_id

    def create_video(
        self,
        prompt: str,
        *,
        model: str = "seedance-lite",
        ratio: str = "1:1",
        resolution: str = "480p",
        duration: int = 3,
        number: int = 1,
        credits: int | None = None,
        source: int | str | None = None,
        camera_fixed: bool = False,
        auto_optimize: bool = False,
        media: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        if not prompt:
            raise ValueError("prompt cannot be empty")
        if number < 1:
            raise ValueError("number must be >= 1")

        model = self.normalize_model(model)
        payload = {
            "source": source if source is not None else 2,
            "prompt": prompt,
            "media": media or {},
            "parameters": {
                "ratio": ratio,
                "resolution": resolution,
                "duration": duration,
                "camerafixed": camera_fixed,
                "auto_optimize": auto_optimize,
            },
            "number": number,
            "credits": credits if credits is not None else self.estimate_credits(model, number),
            "model": model,
        }
        if extra_payload:
            payload.update(extra_payload)

        data = self._json(self._request(
            "POST",
            "/v1/generations/create_video",
            headers=self.headers(),
            json=payload,
        ))
        task_id = self._extract_task_id(data)
        if not task_id:
            raise SousakuAPIError(f"Could not extract task_id: {data}")
        return task_id

    def get_task_status(self, task_id: str, *, language: str = "zh-CN") -> SousakuTask:
        tasks = self.get_task_statuses([task_id], language=language)
        if not tasks:
            raise SousakuAPIError(f"Task not found: {task_id}")
        return tasks[0]

    def get_task_statuses(self, task_ids: Iterable[str], *, language: str = "zh-CN") -> list[SousakuTask]:
        ids = [str(task_id).strip() for task_id in task_ids if str(task_id).strip()]
        if not ids:
            return []

        data = self._json(self._request(
            "GET",
            "/v1/generations/task_status",
            headers=self.headers(),
            params={"task_ids": ",".join(ids), "language": language},
            timeout=15,
        ))
        body = data.get("data") or []
        if isinstance(body, dict):
            body = [body]

        tasks_by_id: dict[str, SousakuTask] = {}
        for task_raw in body:
            if not isinstance(task_raw, dict):
                continue
            task = self._task_from_raw(task_raw)
            if task.task_id:
                tasks_by_id[task.task_id] = task

        return [tasks_by_id[task_id] for task_id in ids if task_id in tasks_by_id]

    def get_history(self, *, page: int = 1, page_size: int = 15, language: str = "zh-CN") -> dict[str, Any]:
        return self._json(self._request(
            "GET",
            "/v1/generations/history",
            headers=self.headers(),
            params={"page": page, "page_size": page_size, "language": language},
            timeout=20,
        ))

    def list_history_tasks(self, *, page: int = 1, page_size: int = 15, language: str = "zh-CN") -> list[SousakuTask]:
        data = self.get_history(page=page, page_size=page_size, language=language)
        tasks = data.get("data") or []
        return [
            self._task_from_raw(task)
            for task in tasks
            if isinstance(task, dict)
        ]

    def get_user(self) -> dict[str, Any]:
        data = self._json(self._request(
            "GET",
            "/v1/user",
            headers=self.headers(),
            timeout=20,
        ))
        return data.get("data") or {}

    def get_credit_balance(self) -> int | None:
        user = self.get_user()
        subscription = user.get("subscription") or {}
        total = subscription.get("total_credit")
        return int(total) if total is not None else None

    def get_user_profile(self) -> SousakuUserProfile:
        user = self.get_user()
        subscription = user.get("subscription") or {}
        task = user.get("task") or {}
        generations = user.get("generations") or {}
        return SousakuUserProfile(
            user_id=user.get("user_id"),
            user_name=user.get("user_name"),
            nick_name=user.get("nick_name"),
            user_email=user.get("user_email"),
            share_code=user.get("share_code"),
            inviter_share_code=user.get("inviter_share_code") or task.get("inviter_share_code"),
            inviter_share_code_status=task.get("inviter_share_code_status"),
            total_credit=self._to_int(subscription.get("total_credit")),
            subscription_credit=self._to_int(subscription.get("subscription_credit")),
            permanent_credit=self._to_int(subscription.get("permanent_credit")),
            package_level=subscription.get("package_level"),
            running_task_count=self._to_int(generations.get("running_task_count")),
            complete_pending_claim_num=self._to_int(task.get("complete_pending_claim_num")),
            raw=user,
        )

    def save_user_profile(self, path: str) -> SousakuUserProfile:
        profile = self.get_user_profile()
        payload = self._profile_to_dict(profile, include_raw=True)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        return profile

    def get_account_record(self, *, include_token: bool = True, include_raw: bool = False) -> dict[str, Any]:
        profile = self.get_user_profile()
        record = self._profile_to_dict(profile, include_raw=include_raw)
        record["token_index"] = self.token_index
        record["token_masked"] = self._mask_token(self.token)
        if include_token:
            record["token"] = self.token
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        return record

    def select_token_for_generation(
        self,
        *,
        required_credits: int,
        min_credit_threshold: int | None = None,
        skip_running: bool = True,
        purge_low_credit: bool = True,
    ) -> SousakuUserProfile:
        threshold = self.min_credit_threshold if min_credit_threshold is None else int(min_credit_threshold)
        original_index = self.token_index
        candidates: list[tuple[int, str, SousakuUserProfile]] = []
        low_credit_tokens: list[str] = []
        skipped: list[str] = []

        for offset in range(len(self.tokens)):
            index = (original_index + offset) % len(self.tokens)
            token = self.tokens[index]
            self.token_index = index
            try:
                profile = self.get_user_profile()
            except Exception as exc:
                skipped.append(f"{self._mask_token(token)}: {exc}")
                continue

            total_credit = profile.total_credit
            running_count = profile.running_task_count or 0
            if total_credit is not None and total_credit < threshold:
                low_credit_tokens.append(token)
                skipped.append(f"{self._mask_token(token)}: credit {total_credit} < threshold {threshold}")
                continue
            if total_credit is not None and total_credit < required_credits:
                skipped.append(f"{self._mask_token(token)}: credit {total_credit} < required {required_credits}")
                continue
            if skip_running and running_count > 0:
                skipped.append(f"{self._mask_token(token)}: running {running_count}")
                continue
            candidates.append((index, token, profile))

        if purge_low_credit:
            for token in low_credit_tokens:
                if token in self.tokens:
                    self.remove_token(token, persist=False)
            if low_credit_tokens:
                self._persist_tokens_to_config()
                try:
                    self.save_account_records(include_token=True, include_raw=False)
                except Exception:
                    pass

        if candidates:
            selected_token = candidates[0][1]
            if selected_token not in self.tokens:
                raise SousakuAPIError("Selected Sousaku token was removed unexpectedly.")
            self.token_index = self.tokens.index(selected_token)
            return candidates[0][2]

        if self.tokens:
            self.token_index = min(original_index, len(self.tokens) - 1)
        detail = "; ".join(skipped[-6:])
        raise SousakuAPIError(f"No available Sousaku account for {required_credits} credits. {detail}")

    def collect_account_records(
        self,
        *,
        include_token: bool = True,
        include_raw: bool = False,
    ) -> list[dict[str, Any]]:
        original_index = self.token_index
        records = []
        seen_user_ids: set[str] = set()
        try:
            for index, token in enumerate(self.tokens):
                self.token_index = index
                try:
                    record = self.get_account_record(include_token=include_token, include_raw=include_raw)
                except Exception as exc:
                    records.append({
                        "token_index": index,
                        "token": token if include_token else None,
                        "token_masked": self._mask_token(token),
                        "error": str(exc),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })
                    continue
                user_id = record.get("user_id")
                # Multiple tokens can point to the same account. Keep the first full record and append aliases.
                if user_id and user_id in seen_user_ids:
                    records.append({
                        "user_id": user_id,
                        "token_index": index,
                        "token": token if include_token else None,
                        "token_masked": self._mask_token(token),
                        "duplicate": True,
                        "updated_at": record["updated_at"],
                    })
                    continue
                if user_id:
                    seen_user_ids.add(user_id)
                records.append(record)
        finally:
            self.token_index = original_index
        return records

    def save_account_records(
        self,
        path: str | None = None,
        *,
        include_token: bool = True,
        include_raw: bool = False,
    ) -> list[dict[str, Any]]:
        output_path = path or self.accounts_path or "sousaku_accounts.json"
        records = self.collect_account_records(include_token=include_token, include_raw=include_raw)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(records),
            "accounts": records,
        }
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        return records

    def _persist_tokens_to_config(self) -> None:
        if not self.config_path:
            return
        try:
            with open(self.config_path, "r", encoding="utf-8-sig") as file:
                config = json.load(file)
        except FileNotFoundError:
            config = {}
        config["tokens"] = self.tokens
        config.pop("token", None)
        os.makedirs(os.path.dirname(os.path.abspath(self.config_path)), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as file:
            json.dump(config, file, ensure_ascii=False, indent=2)

    def _task_from_raw(self, task_raw: dict[str, Any], *, task_id: str | None = None) -> SousakuTask:
        resolved_task_id = task_id or task_raw.get("task_id") or task_raw.get("id") or ""
        status = str(task_raw.get("status", "unknown"))
        return SousakuTask(
            task_id=resolved_task_id,
            status=status,
            progress=task_raw.get("progress"),
            images=self._extract_images(task_raw),
            raw=task_raw,
        )

    def wait_for_task(
        self,
        task_id: str,
        *,
        timeout: int = 180,
        poll_interval: float = 3,
        language: str = "zh-CN",
        on_update: Callable[[SousakuTask], None] | None = None,
    ) -> SousakuTask:
        deadline = time.time() + timeout
        last_task: SousakuTask | None = None

        while time.time() < deadline:
            last_task = self.get_task_status(task_id, language=language)
            if on_update:
                on_update(last_task)
            if last_task.is_success:
                return last_task
            if last_task.is_failed:
                message = self._task_error_message(last_task.raw)
                raise SousakuTaskFailedError(task_id, message, raw=last_task.raw)
            time.sleep(poll_interval)

        raise SousakuTimeoutError(f"Sousaku task timed out: {task_id}; last={last_task}")

    def generate_sync(self, prompt: str, **kwargs: Any) -> list[SousakuImage]:
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.generation_timeout
        if "poll_interval" not in kwargs:
            kwargs["poll_interval"] = self.poll_interval
        return_partial_on_timeout = kwargs.pop("return_partial_on_timeout", True)
        wait_kwargs = {
            key: kwargs.pop(key)
            for key in list(kwargs.keys())
            if key in {"timeout", "poll_interval", "language"}
        }
        save_dir = kwargs.pop("save_dir", None) or self.save_dir
        task_id = self.create_image(prompt, **kwargs)
        partial_images: list[SousakuImage] = []
        seen_urls: set[str] = set()

        def save_partial(task: SousakuTask) -> None:
            for image in task.images:
                if image.url in seen_urls:
                    continue
                seen_urls.add(image.url)
                partial_images.append(image)
                if save_dir:
                    stem = task_id or image.file_id or image.content_id or "image"
                    ext = self._extension_from_url_or_content_type(image.url, "")
                    filename = self._image_filename(stem, image.url, ext, fallback_index=len(partial_images))
                    self.download_image(image, save_dir=save_dir, filename=filename)

        try:
            task = self.wait_for_task(task_id, on_update=save_partial, **wait_kwargs)
        except SousakuTimeoutError:
            if return_partial_on_timeout and partial_images:
                return partial_images
            raise

        save_partial(task)
        return task.images

    def generate_video_sync(self, prompt: str, **kwargs: Any) -> list[SousakuImage]:
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.generation_timeout
        if "poll_interval" not in kwargs:
            kwargs["poll_interval"] = self.poll_interval
        return_partial_on_timeout = kwargs.pop("return_partial_on_timeout", True)
        wait_kwargs = {
            key: kwargs.pop(key)
            for key in list(kwargs.keys())
            if key in {"timeout", "poll_interval", "language"}
        }
        save_dir = kwargs.pop("save_dir", None) or self.save_dir
        task_id = self.create_video(prompt, **kwargs)
        partial_media: list[SousakuImage] = []
        seen_urls: set[str] = set()

        def save_partial(task: SousakuTask) -> None:
            for item in task.images:
                if item.url in seen_urls:
                    continue
                seen_urls.add(item.url)
                partial_media.append(item)
                if save_dir:
                    stem = task_id or item.file_id or item.content_id or "video"
                    ext = self._extension_from_url_or_content_type(item.url, "")
                    filename = self._image_filename(stem, item.url, ext, fallback_index=len(partial_media))
                    self.download_image(item, save_dir=save_dir, filename=filename)

        try:
            task = self.wait_for_task(task_id, on_update=save_partial, **wait_kwargs)
        except SousakuTimeoutError:
            if return_partial_on_timeout and partial_media:
                return partial_media
            raise

        save_partial(task)
        return task.images

    def download_image(self, image: SousakuImage, *, save_dir: str, filename: str | None = None) -> str:
        os.makedirs(save_dir, exist_ok=True)
        response = self.session.get(image.url, timeout=120)
        response.raise_for_status()

        if not filename:
            ext = self._extension_from_url_or_content_type(
                image.url,
                response.headers.get("Content-Type", ""),
            )
            stem = image.file_id or image.content_id or os.urandom(4).hex()
            filename = f"sousaku_{self._safe_filename(stem)}.{ext}"

        path = os.path.join(save_dir, filename)
        with open(path, "wb") as file:
            file.write(response.content)
        image.saved_path = path
        return path

    def download_images(
        self,
        images: list[SousakuImage],
        *,
        save_dir: str,
        task_id: str | None = None,
    ) -> list[str]:
        paths = []
        for index, image in enumerate(images, start=1):
            stem = task_id or image.file_id or image.content_id or "image"
            ext = self._extension_from_url_or_content_type(image.url, "")
            filename = self._image_filename(stem, image.url, ext, fallback_index=index)
            paths.append(self.download_image(image, save_dir=save_dir, filename=filename))
        return paths

    @staticmethod
    def normalize_model(model: str) -> str:
        return MODEL_ALIASES.get(model.strip().lower(), model)

    def estimate_credits(self, model: str, number: int = 1) -> int:
        model = self.normalize_model(model)
        config = self.model_configs.get(model)
        per_image = config.credits_per_image if config else 4
        return per_image * number

    def set_model_config(self, model: str, credits_per_image: int, label: str | None = None) -> None:
        model = self.normalize_model(model)
        self.model_configs[model] = SousakuModelConfig(
            model=model,
            credits_per_image=int(credits_per_image),
            label=label,
        )

    @staticmethod
    def _build_model_configs(
        overrides: dict[str, int | SousakuModelConfig | dict[str, Any]] | None = None,
    ) -> dict[str, SousakuModelConfig]:
        configs = dict(DEFAULT_MODEL_CONFIGS)
        for key, value in (overrides or {}).items():
            model = SousakuClient.normalize_model(key)
            if isinstance(value, SousakuModelConfig):
                configs[model] = value
            elif isinstance(value, int):
                configs[model] = SousakuModelConfig(model=model, credits_per_image=value)
            elif isinstance(value, dict):
                configs[model] = SousakuModelConfig(
                    model=model,
                    credits_per_image=int(value["credits_per_image"]),
                    label=value.get("label"),
                )
            else:
                raise TypeError(f"Unsupported model config for {key}: {value!r}")
        return configs

    @staticmethod
    def _model_configs_from_env(env_name: str = "SOUSAKU_MODEL_CREDITS") -> dict[str, int]:
        value = os.getenv(env_name, "")
        configs: dict[str, int] = {}
        for chunk in value.replace(";", ",").split(","):
            item = chunk.strip()
            if not item or "=" not in item:
                continue
            model, credits = item.split("=", 1)
            model = model.strip()
            try:
                configs[model] = int(credits.strip())
            except ValueError:
                continue
        return configs

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _profile_to_dict(profile: SousakuUserProfile, *, include_raw: bool = False) -> dict[str, Any]:
        data = {
            "user_id": profile.user_id,
            "user_name": profile.user_name,
            "nick_name": profile.nick_name,
            "user_email": profile.user_email,
            "share_code": profile.share_code,
            "inviter_share_code": profile.inviter_share_code,
            "inviter_share_code_status": profile.inviter_share_code_status,
            "total_credit": profile.total_credit,
            "subscription_credit": profile.subscription_credit,
            "permanent_credit": profile.permanent_credit,
            "package_level": profile.package_level,
            "running_task_count": profile.running_task_count,
            "complete_pending_claim_num": profile.complete_pending_claim_num,
        }
        if include_raw:
            data["raw"] = profile.raw
        return data

    @staticmethod
    def _mask_token(token: str) -> str:
        if len(token) <= 12:
            return "*" * len(token)
        return f"{token[:6]}...{token[-6:]}"

    @staticmethod
    def _normalize_reference_images(reference_images: list[dict[str, Any]]) -> list[dict[str, Any]]:
        refs = []
        for item in reference_images:
            file_id = item.get("file_id")
            download_url = item.get("download_url") or item.get("url")
            if not file_id or not download_url:
                continue
            ref = {
                "file_id": file_id,
                "download_url": download_url,
            }
            thumbnail_url = item.get("thumbnail_url")
            if thumbnail_url:
                ref["thumbnail_url"] = thumbnail_url
            refs.append(ref)
        return refs

    @staticmethod
    def ratio_from_size(width: int, height: int) -> str:
        if width <= 0 or height <= 0:
            return "1:1"
        value = width / height
        if value >= 2.05:
            return "21:9"
        if value >= 1.63:
            return "16:9"
        if value >= 1.41:
            return "3:2"
        if value >= 1.16:
            return "4:3"
        if value >= 0.87:
            return "1:1"
        if value >= 0.70:
            return "3:4"
        if value >= 0.61:
            return "2:3"
        return "9:16"

    @staticmethod
    def _extract_task_id(data: dict[str, Any]) -> str | None:
        body = data.get("data")
        if isinstance(body, str):
            return body
        if isinstance(body, dict):
            return body.get("task_id") or body.get("id")
        if isinstance(body, list) and body:
            first = body[0]
            if isinstance(first, dict):
                return first.get("task_id") or first.get("id")
        return data.get("task_id") or data.get("id")

    @staticmethod
    def _extract_first_task(data: dict[str, Any], task_id: str) -> dict[str, Any]:
        body = data.get("data", data)
        if isinstance(body, list):
            for item in body:
                if isinstance(item, dict) and str(item.get("id") or item.get("task_id")) == str(task_id):
                    return item
            return body[0] if body and isinstance(body[0], dict) else {}
        if isinstance(body, dict):
            return body
        return {}

    @classmethod
    def _extract_images(cls, task: dict[str, Any]) -> list[SousakuImage]:
        images: list[SousakuImage] = []
        seen: set[str] = set()

        def add_from_content(item: dict[str, Any]) -> None:
            url = item.get("download_url")
            if not isinstance(url, str) or not cls._looks_like_media_url(url):
                return
            if url in seen:
                return
            seen.add(url)
            images.append(SousakuImage(
                url=url,
                width=item.get("image_width") or item.get("width"),
                height=item.get("image_height") or item.get("height"),
                thumbnail_url=item.get("thumbnail_url"),
                attachment_url=item.get("attachment_download_url"),
                file_id=item.get("file_id"),
                content_id=item.get("id"),
                status=item.get("status"),
                raw=item,
            ))

        content = task.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type", "image")).lower() not in {"image", "video"}:
                    continue
                # Failed/running content entries can have empty output_data and should not become results.
                if str(item.get("status", "")).lower() not in {"succeeded", "success", "completed", "done"}:
                    continue
                add_from_content(item)

        return images

    @staticmethod
    def _looks_like_media_url(value: str) -> bool:
        if value.startswith("data:image/"):
            return True
        if not value.startswith(("http://", "https://")):
            return False
        lowered = value.lower().split("?", 1)[0]
        return (
            lowered.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov"))
            or "image" in lowered
            or "video" in lowered
        )

    @staticmethod
    def _task_error_message(task: dict[str, Any]) -> str:
        message = task.get("error_message")
        if message:
            return str(message)

        failed_items = []
        for item in task.get("content") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("status", "")).lower() not in {"failed", "error"}:
                continue
            item_message = item.get("error_message")
            if item.get("is_nsfw_error"):
                item_message = item_message or "content rejected by model compliance check"
            if item_message:
                failed_items.append(str(item_message))

        if failed_items:
            return "; ".join(dict.fromkeys(failed_items))
        if task.get("is_nsfw_error"):
            return "content rejected by model compliance check"
        return "unknown task failure"

    @staticmethod
    def _extension_from_url_or_content_type(url: str, content_type: str) -> str:
        content_type = content_type.lower().split(";", 1)[0].strip()
        if content_type == "image/png":
            return "png"
        if content_type in {"image/jpeg", "image/jpg"}:
            return "jpg"
        if content_type == "image/webp":
            return "webp"
        if content_type == "video/mp4":
            return "mp4"
        if content_type == "video/webm":
            return "webm"
        if content_type == "video/quicktime":
            return "mov"

        path = url.lower().split("?", 1)[0]
        for ext in ("png", "jpg", "jpeg", "webp", "gif", "mp4", "webm", "mov"):
            if path.endswith(f".{ext}"):
                return "jpg" if ext == "jpeg" else ext
        return "png"

    @staticmethod
    def _safe_filename(value: str) -> str:
        value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("._")
        return value or os.urandom(4).hex()

    @classmethod
    def _image_filename(
        cls,
        stem: str,
        url: str,
        ext: str,
        *,
        fallback_index: int | None = None,
    ) -> str:
        if url:
            suffix = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        elif fallback_index is not None:
            suffix = f"{fallback_index:02d}"
        else:
            suffix = os.urandom(4).hex()
        return f"sousaku_{cls._safe_filename(stem)}_{suffix}.{ext or 'png'}"
