from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Any

from services.jobs.providers import ProviderAdapter, ProviderTimeout, SousakuAdapter
from services.jobs.store import JobStore


def _requested_image_count(job: dict[str, Any]) -> int:
    params = job.get("params") if isinstance(job.get("params"), dict) else {}
    for key in ("n", "number", "imageCount"):
        try:
            value = int(params.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return 1


class JobWorker:
    def __init__(
        self,
        *,
        store: JobStore,
        adapters: dict[str, ProviderAdapter] | None = None,
        provider_limits: dict[str, int] | None = None,
        max_workers: int = 4,
        idle_sleep: float = 0.5,
        logger=None,
    ):
        self.store = store
        self.adapters = adapters or {"sousaku": SousakuAdapter()}
        self.provider_limits = provider_limits or {"*": 1}
        self.max_workers = max(1, int(max_workers or 1))
        self.idle_sleep = max(0.1, float(idle_sleep or 0.5))
        self.logger = logger
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._active: set[str] = set()
        self._lock = threading.RLock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="apimart-job")
        self._thread = threading.Thread(target=self._run_loop, name="apimart-job-dispatcher", daemon=True)
        self._thread.start()
        self._log("STARTUP", "Job Worker 已启动", "OK", max_workers=self.max_workers)

    def stop(self) -> None:
        self._stop.set()
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=False)

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                with self._lock:
                    active_count = len(self._active)
                if active_count >= self.max_workers:
                    time.sleep(self.idle_sleep)
                    continue

                job = self.store.claim_next_job(self.provider_limits)
                if not job:
                    time.sleep(self.idle_sleep)
                    continue

                with self._lock:
                    self._active.add(job["id"])
                assert self._executor is not None
                self._executor.submit(self._process_job, job)
            except Exception as exc:
                self._log("JOB", "调度异常", "ERROR", error=exc)
                time.sleep(1)

    def _process_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        provider = str(job.get("provider") or "").lower()
        try:
            adapter = self.adapters.get(provider)
            if not adapter:
                raise ValueError(f"unknown job provider: {provider}")

            self._log("JOB", "开始执行", "INFO", job=job_id[:12], provider=provider)
            result = adapter.run(job, self.store)
            self.store.finish_job(job_id, "succeeded", result=result)
            requested = _requested_image_count(job)
            succeeded = len(result)
            self._log("JOB", "任务完成", "OK", job=job_id[:12], provider=provider, images=f"{succeeded}/{requested}")
        except ProviderTimeout as exc:
            self.store.finish_job(job_id, "timeout", error=str(exc))
            self._log("JOB", "任务超时", "ERROR", job=job_id[:12], provider=provider, error=exc)
        except Exception as exc:
            self.store.finish_job(job_id, "failed", error=str(exc))
            self._log("JOB", "任务失败", "ERROR", job=job_id[:12], provider=provider, error=exc)
        finally:
            with self._lock:
                self._active.discard(job_id)

    def _log(self, scope: str, message: str, level: str = "INFO", **fields: Any) -> None:
        if not self.logger:
            return
        try:
            self.logger(scope, message, level, **fields)
        except Exception:
            pass
