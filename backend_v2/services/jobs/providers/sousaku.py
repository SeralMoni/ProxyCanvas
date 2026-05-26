from __future__ import annotations

import time
from typing import Any

from config import JOB_DEFAULT_TIMEOUT_SECONDS, JOB_POLL_INTERVAL_SECONDS
from services.jobs.providers.base import ProviderAdapter, ProviderError, ProviderTimeout
from services.jobs.store import JobStore
from services.sousaku_provider import create_task as create_sousaku_task
from services.sousaku_provider import get_task as get_sousaku_task
from services.sousaku_provider import refresh_task_account
from services.sousaku_provider import release_task_account


TERMINAL_OK = {"succeeded", "success", "completed"}
TERMINAL_FAILED = {"failed", "error", "cancelled", "timeout"}


class SousakuAdapter(ProviderAdapter):
    name = "sousaku"

    def __init__(self, *, poll_interval: int = JOB_POLL_INTERVAL_SECONDS, timeout_seconds: int = JOB_DEFAULT_TIMEOUT_SECONDS):
        self.poll_interval = max(1, int(poll_interval or 3))
        self.timeout_seconds = max(30, int(timeout_seconds or 1800))

    def run(self, job: dict[str, Any], store: JobStore) -> list[dict[str, Any]]:
        payload = self.normalize_payload(job)
        payload["_job_id"] = job["id"]

        submitted = create_sousaku_task(payload)
        if not submitted.get("success"):
            message = (submitted.get("error") or {}).get("message") or "Sousaku submit failed"
            raise ProviderError(message)

        task_id = self._extract_task_id(submitted)
        if not task_id:
            raise ProviderError("Sousaku did not return task_id")

        try:
            store.update_job(job["id"], status="running", external_task_id=task_id, progress=0)

            started_at = time.time()
            last_progress = None
            last_image_count = -1
            while True:
                current = store.get_job(job["id"])
                if current and current.get("status") == "cancelled":
                    raise ProviderError("cancelled by user")
                if time.time() - started_at > self.timeout_seconds:
                    raise ProviderTimeout(f"Sousaku task timeout after {self.timeout_seconds}s")

                status_payload = get_sousaku_task(task_id)
                status = str(status_payload.get("status") or status_payload.get("data", {}).get("status") or "").lower()
                data = status_payload.get("data") if isinstance(status_payload.get("data"), dict) else {}
                progress = self._safe_int(data.get("progress"), default=0)
                images = data.get("result", {}).get("images", []) if isinstance(data.get("result"), dict) else []
                image_count = len(images) if isinstance(images, list) else 0

                if progress != last_progress or image_count != last_image_count:
                    partial_images = self._normalize_images(images, task_id)
                    store.update_job(job["id"], status="running", progress=progress, result_json=partial_images)
                    last_progress = progress
                    last_image_count = image_count

                if status in TERMINAL_OK:
                    normalized = self._normalize_images(images, task_id)
                    self._refresh_account_snapshot(task_id)
                    return normalized

                if status in TERMINAL_FAILED:
                    message = (status_payload.get("error") or {}).get("message") or "Sousaku task failed"
                    self._refresh_account_snapshot(task_id)
                    raise ProviderError(message)

                time.sleep(self.poll_interval)
        finally:
            release_task_account(task_id)

    def _extract_task_id(self, submitted: dict[str, Any]) -> str:
        data = submitted.get("data")
        if isinstance(data, list) and data:
            return str((data[0] or {}).get("task_id") or "")
        if isinstance(data, dict):
            return str(data.get("task_id") or "")
        return ""

    def _normalize_images(self, images: Any, task_id: str) -> list[dict[str, Any]]:
        if not isinstance(images, list):
            return []
        normalized = []
        seen = set()
        for index, image in enumerate(images, start=1):
            if not isinstance(image, dict):
                continue
            identity = image.get("content_id") or image.get("file_id") or image.get("url") or image.get("saved_path")
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            item = dict(image)
            item.update({
                "provider": "sousaku",
                "task_id": task_id,
                "index": index,
            })
            normalized.append(item)
        return normalized

    def _safe_int(self, value: Any, *, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _refresh_account_snapshot(self, task_id: str) -> None:
        try:
            refresh_task_account(task_id)
        except Exception:
            pass
