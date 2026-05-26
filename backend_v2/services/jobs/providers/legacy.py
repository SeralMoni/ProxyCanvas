from __future__ import annotations

import time
from typing import Any, Callable

from flask import Response

from config import JOB_DEFAULT_TIMEOUT_SECONDS, JOB_POLL_INTERVAL_SECONDS
from services.jobs.providers.base import ProviderAdapter, ProviderError, ProviderTimeout
from services.jobs.store import JobStore


JsonCallable = Callable[..., Any]


class FlaskEndpointAdapter(ProviderAdapter):
    """Wrap an existing Flask generation endpoint as a background provider.

    This keeps the public route and the worker path sharing one implementation
    while providers are being migrated into standalone service modules.
    """

    def __init__(
        self,
        *,
        name: str,
        app,
        endpoint: JsonCallable,
        path: str,
    ):
        self.name = name
        self.app = app
        self.endpoint = endpoint
        self.path = path

    def run(self, job: dict[str, Any], store: JobStore) -> list[dict[str, Any]]:
        payload = self.normalize_payload(job)
        payload["_job_id"] = job["id"]
        payload["_job_provider"] = self.name
        store.update_job(job["id"], status="running", progress=5)

        response = self._call_endpoint(self.endpoint, payload, self.path)
        if not response.get("success", False):
            raise ProviderError(self._error_message(response, f"{self.name} generation failed"))

        results = self._normalize_images(response.get("data") or [], job["id"])

        store.update_job(job["id"], status="saving", progress=95, result_json=results)
        return results

    def _call_endpoint(self, endpoint: JsonCallable, payload: dict[str, Any], path: str) -> dict[str, Any]:
        with self.app.test_request_context(path, method="POST", json=payload):
            return self._response_to_json(endpoint())

    def _response_to_json(self, result: Any) -> dict[str, Any]:
        status_code = 200
        response = result
        if isinstance(result, tuple):
            response = result[0]
            if len(result) > 1 and isinstance(result[1], int):
                status_code = result[1]

        if isinstance(response, Response):
            payload = response.get_json(silent=True) or {}
            status_code = response.status_code if response.status_code else status_code
        elif isinstance(response, dict):
            payload = response
        else:
            payload = {}

        if status_code >= 400 and payload.get("success") is not False:
            payload = {
                "success": False,
                "error": {"message": self._error_message(payload, f"HTTP {status_code}")},
            }
        return payload

    def _normalize_images(self, images: Any, job_id: str) -> list[dict[str, Any]]:
        if not isinstance(images, list):
            return []
        normalized: list[dict[str, Any]] = []
        for index, image in enumerate(images, start=1):
            if not isinstance(image, dict):
                continue
            item = dict(image)
            item.update({
                "provider": self.name,
                "job_id": job_id,
                "index": index,
            })
            normalized.append(item)
        return normalized

    def _error_message(self, payload: dict[str, Any], fallback: str) -> str:
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if payload.get("message"):
            return str(payload["message"])
        return fallback


class APIMartAdapter(FlaskEndpointAdapter):
    name = "apimart"

    def __init__(
        self,
        *,
        app,
        submit_endpoint: JsonCallable,
        status_endpoint: JsonCallable,
        poll_interval: int = JOB_POLL_INTERVAL_SECONDS,
        timeout_seconds: int = JOB_DEFAULT_TIMEOUT_SECONDS,
    ):
        super().__init__(
            name="apimart",
            app=app,
            endpoint=submit_endpoint,
            path="/api/generate",
        )
        self.submit_endpoint = submit_endpoint
        self.status_endpoint = status_endpoint
        self.poll_interval = max(1, int(poll_interval or 3))
        self.timeout_seconds = max(30, int(timeout_seconds or 1800))

    def run(self, job: dict[str, Any], store: JobStore) -> list[dict[str, Any]]:
        payload = self.normalize_payload(job)
        store.update_job(job["id"], status="running", progress=5)

        submitted = self._call_endpoint(self.submit_endpoint, payload, "/api/generate")
        task_id = self._extract_task_id(submitted)
        if not task_id:
            if submitted.get("data"):
                return self._normalize_images(submitted.get("data"), job["id"])
            raise ProviderError(self._error_message(submitted, "APIMart did not return task_id"))

        store.update_job(job["id"], external_task_id=task_id, progress=10)

        started_at = time.time()
        last_progress = None
        while True:
            current = store.get_job(job["id"])
            if current and current.get("status") == "cancelled":
                raise ProviderError("cancelled by user")
            if time.time() - started_at > self.timeout_seconds:
                raise ProviderTimeout(f"APIMart task timeout after {self.timeout_seconds}s")

            status_payload = self._call_status_endpoint(task_id)
            data = status_payload.get("data") if isinstance(status_payload.get("data"), dict) else {}
            status = str(data.get("status") or status_payload.get("status") or "").lower()
            progress = self._safe_int(data.get("progress"), default=0)
            images = self._extract_images(data)
            normalized = self._normalize_images(images, job["id"])

            if progress != last_progress or normalized:
                store.update_job(job["id"], status="running", progress=progress, result_json=normalized)
                last_progress = progress

            if status in {"completed", "success", "succeeded"}:
                return normalized
            if status in {"failed", "error", "cancelled", "timeout"}:
                raise ProviderError(self._error_message(status_payload, "APIMart task failed"))
            time.sleep(self.poll_interval)

    def _call_status_endpoint(self, task_id: str) -> dict[str, Any]:
        with self.app.test_request_context(f"/api/task/{task_id}", method="GET"):
            return self._response_to_json(self.status_endpoint(task_id))

    def _extract_task_id(self, payload: dict[str, Any]) -> str:
        if payload.get("task_id"):
            return str(payload["task_id"])
        data = payload.get("data")
        if isinstance(data, list) and data:
            return str((data[0] or {}).get("task_id") or (data[0] or {}).get("id") or "")
        if isinstance(data, dict):
            return str(data.get("task_id") or data.get("id") or "")
        return ""

    def _extract_images(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        result = data.get("result")
        if isinstance(result, dict):
            images = result.get("images")
            if isinstance(images, list):
                return [img if isinstance(img, dict) else {"url": img} for img in images]
            if isinstance(images, str):
                return [{"url": images}]
        if isinstance(result, list):
            return [img if isinstance(img, dict) else {"url": img} for img in result]
        if isinstance(result, str):
            return [{"url": result}]
        return []

    def _safe_int(self, value: Any, *, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


class OpenAITaskAdapter(FlaskEndpointAdapter):
    name = "openai"

    def __init__(
        self,
        *,
        app,
        submit_endpoint: JsonCallable,
        status_endpoint: JsonCallable,
        poll_interval: int = 2,
        timeout_seconds: int = JOB_DEFAULT_TIMEOUT_SECONDS,
    ):
        super().__init__(
            name="openai",
            app=app,
            endpoint=submit_endpoint,
            path="/api/generate-openai-tasks",
        )
        self.status_endpoint = status_endpoint
        self.poll_interval = max(1, int(poll_interval or 2))
        self.timeout_seconds = max(30, int(timeout_seconds or 1800))

    def run(self, job: dict[str, Any], store: JobStore) -> list[dict[str, Any]]:
        payload = self.normalize_payload(job)
        store.update_job(job["id"], status="running", progress=5)
        submitted = self._call_endpoint(self.endpoint, payload, self.path)
        if not submitted.get("success"):
            raise ProviderError(self._error_message(submitted, "OpenAI task submit failed"))

        tasks = submitted.get("data") or []
        task_ids = [str(task.get("task_id")) for task in tasks if isinstance(task, dict) and task.get("task_id")]
        if not task_ids:
            raise ProviderError("OpenAI did not return task ids")
        store.update_job(job["id"], external_task_id=",".join(task_ids), progress=10)

        pending = set(task_ids)
        results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        started_at = time.time()
        while pending:
            if time.time() - started_at > self.timeout_seconds:
                raise ProviderTimeout(f"OpenAI task timeout after {self.timeout_seconds}s")
            status_payload = self._call_openai_status(sorted(pending))
            if not status_payload.get("success"):
                raise ProviderError(self._error_message(status_payload, "OpenAI task status failed"))

            for missing_id in status_payload.get("missing_ids") or []:
                pending.discard(str(missing_id))
                failure = {"task_id": str(missing_id), "error": "任务不存在"}
                failures.append(failure)

            for item in status_payload.get("data") or []:
                task_id = str(item.get("task_id") or "")
                status = str(item.get("status") or "").lower()
                if status == "success":
                    pending.discard(task_id)
                    task_images = [image for image in item.get("data") or [] if isinstance(image, dict)]
                    if not task_images:
                        failure = {"task_id": task_id, "error": "任务成功但没有返回图片"}
                        failures.append(failure)
                    for image in item.get("data") or []:
                        if isinstance(image, dict):
                            results.append(image)
                elif status in {"error", "failed", "cancelled"}:
                    pending.discard(task_id)
                    error = item.get("error")
                    failure = {"task_id": task_id, "error": error}
                    failures.append(failure)

            progress = 100 if not pending else int(((len(task_ids) - len(pending)) / max(1, len(task_ids))) * 90) + 10
            normalized = self._normalize_images(results, job["id"])
            store.update_job(job["id"], status="running", progress=progress, result_json=normalized)
            if pending:
                time.sleep(self.poll_interval)

        normalized = self._normalize_images(results, job["id"])
        if not normalized:
            summary = self._summarize_failures(failures)
            raise ProviderError(summary or "ChatGPT2API finished but produced 0 images")
        return normalized

    def _call_openai_status(self, task_ids: list[str]) -> dict[str, Any]:
        with self.app.test_request_context(f"/api/openai-tasks?ids={','.join(task_ids)}", method="GET"):
            return self._response_to_json(self.status_endpoint())

    def _summarize_failures(self, failures: list[dict[str, Any]]) -> str:
        if not failures:
            return ""
        messages: list[str] = []
        for failure in failures[:3]:
            error = failure.get("error")
            if isinstance(error, dict):
                text = str(error.get("message") or error)
            else:
                text = str(error or "unknown error")
            task_id = str(failure.get("task_id") or "")[:12]
            messages.append(f"{task_id}: {text}" if task_id else text)
        suffix = f"; 另有 {len(failures) - 3} 个失败" if len(failures) > 3 else ""
        return "ChatGPT2API produced 0 images. " + " | ".join(messages) + suffix
