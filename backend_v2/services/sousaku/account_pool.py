from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config


DEFAULT_PER_TOKEN_LIMIT = 1
DEFAULT_FAILURE_COOLDOWN_SECONDS = 180


def token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def mask_token(token: str) -> str:
    token = str(token or "")
    if len(token) <= 12:
        return "*" * len(token)
    return f"{token[:6]}...{token[-6:]}"


def _normalize_tokens(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        pieces = value.replace("\r", "\n").replace(";", "\n").replace(",", "\n").split("\n")
        return [piece.strip() for piece in pieces if piece.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


@dataclass
class TokenLease:
    pool: "SousakuAccountPool"
    token: str
    token_hash: str
    token_masked: str
    job_id: str | None
    account: dict[str, Any] | None = None
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        self.pool.release(self)


class SousakuAccountPool:
    """Fast local account selector with small critical sections.

    The cached account file is used only to choose likely-good tokens. Network
    failures are fed back into a short in-memory cooldown so stale local state
    does not make following jobs hammer the same bad token.
    """

    def __init__(self, *, per_token_limit: int = DEFAULT_PER_TOKEN_LIMIT):
        self.per_token_limit = max(1, int(per_token_limit or DEFAULT_PER_TOKEN_LIMIT))
        self._lock = threading.RLock()
        self._tokens: list[str] = []
        self._disabled: set[str] = set()
        self._records_by_hash: dict[str, dict[str, Any]] = {}
        self._config_mtime: float | None = None
        self._accounts_mtime: float | None = None
        self._accounts_path: str | None = None
        self._min_credit_threshold = 5
        self._in_flight: dict[str, set[str]] = {}
        self._cooldowns: dict[str, dict[str, Any]] = {}
        self._cursor = 0

    def acquire(
        self,
        *,
        required_credits: int,
        job_id: str | None = None,
        exclude_token_hashes: set[str] | None = None,
    ) -> TokenLease:
        with self._lock:
            self._reload_if_needed_locked()
            now = time.time()
            candidates = self._candidate_tokens_locked(
                required_credits=required_credits,
                now=now,
                exclude_token_hashes=exclude_token_hashes,
            )
            if not candidates:
                raise RuntimeError(self._no_account_message_locked(required_credits, now))

            _, token, record = candidates[0]
            digest = token_hash(token)
            if job_id:
                self._in_flight.setdefault(digest, set()).add(str(job_id))
            else:
                self._in_flight.setdefault(digest, set()).add(f"anonymous-{time.time_ns()}")
            self._cursor += 1
            return TokenLease(
                pool=self,
                token=token,
                token_hash=digest,
                token_masked=mask_token(token),
                job_id=job_id,
                account=record,
            )

    def release(self, lease: TokenLease) -> None:
        with self._lock:
            jobs = self._in_flight.get(lease.token_hash)
            if not jobs:
                return
            if lease.job_id:
                jobs.discard(str(lease.job_id))
            else:
                jobs.clear()
            if not jobs:
                self._in_flight.pop(lease.token_hash, None)

    def report_failure(self, token_digest: str, error: str, *, cooldown_seconds: int = DEFAULT_FAILURE_COOLDOWN_SECONDS) -> None:
        with self._lock:
            self._cooldowns[token_digest] = {
                "until": time.time() + max(10, int(cooldown_seconds or DEFAULT_FAILURE_COOLDOWN_SECONDS)),
                "error": str(error or "token temporarily unavailable"),
            }

    def report_success(self, token_digest: str) -> None:
        with self._lock:
            self._cooldowns.pop(token_digest, None)

    def token_for_hash(self, token_digest: str) -> str | None:
        with self._lock:
            self._reload_if_needed_locked()
            for token in self._tokens:
                if token_hash(token) == token_digest:
                    return token
        return None

    def overlay_for_token(self, token: str) -> dict[str, Any]:
        digest = token_hash(token)
        with self._lock:
            cooldown = self._cooldowns.get(digest) or {}
            until = float(cooldown.get("until") or 0)
            jobs = sorted(self._in_flight.get(digest) or [])
            return {
                "token_hash": digest,
                "local_running_jobs": len(jobs),
                "local_job_ids": jobs,
                "cooldown_until": until if until > time.time() else None,
                "cooldown_error": cooldown.get("error") if until > time.time() else None,
            }

    def _candidate_tokens_locked(
        self,
        *,
        required_credits: int,
        now: float,
        exclude_token_hashes: set[str] | None = None,
    ) -> list[tuple[tuple[Any, ...], str, dict[str, Any] | None]]:
        ordered_tokens = self._tokens[self._cursor % len(self._tokens):] + self._tokens[:self._cursor % len(self._tokens)] if self._tokens else []
        candidates: list[tuple[tuple[Any, ...], str, dict[str, Any] | None]] = []
        excluded = exclude_token_hashes or set()
        for index, token in enumerate(ordered_tokens):
            digest = token_hash(token)
            if digest in excluded:
                continue
            if token in self._disabled:
                continue
            cooldown = self._cooldowns.get(digest)
            if cooldown and float(cooldown.get("until") or 0) > now:
                continue
            local_count = len(self._in_flight.get(digest) or [])
            if local_count >= self.per_token_limit:
                continue

            record = self._records_by_hash.get(digest)
            if record and record.get("error"):
                continue
            total_credit = _to_float(record.get("total_credit")) if record else None
            threshold = self._min_credit_threshold_locked()
            if total_credit is not None and total_credit < threshold:
                continue
            if total_credit is not None and total_credit < required_credits:
                continue

            remote_running = _to_int(record.get("running_task_count")) if record else 0
            credit_score = -(total_credit if total_credit is not None else 10**9)
            # Prefer accounts that are not running upstream tasks, then spread
            # load by round-robin order. Remote running is not a hard block
            # because the snapshot may be stale.
            score = (local_count, remote_running or 0, credit_score, index)
            candidates.append((score, token, record))
        candidates.sort(key=lambda item: item[0])
        return candidates

    def _no_account_message_locked(self, required_credits: int, now: float) -> str:
        active_cooldowns = [
            item.get("error")
            for item in self._cooldowns.values()
            if float(item.get("until") or 0) > now and item.get("error")
        ]
        suffix = f" Recent unavailable token: {active_cooldowns[-1]}" if active_cooldowns else ""
        return f"No available Sousaku account for {required_credits} credits.{suffix}"

    def _reload_if_needed_locked(self) -> None:
        config_mtime = _mtime(config.SOUSAKU_CONFIG_PATH)
        config_changed = config_mtime != self._config_mtime
        if config_changed:
            payload = _read_json(config.SOUSAKU_CONFIG_PATH)
            self._tokens = _normalize_tokens(payload.get("tokens") or payload.get("token"))
            self._disabled = set(_normalize_tokens(payload.get("disabled_tokens") or []))
            self._accounts_path = _accounts_path(payload)
            self._min_credit_threshold = _min_credit_threshold(payload)
            self._config_mtime = config_mtime

        accounts_mtime = _mtime(self._accounts_path)
        if config_changed or accounts_mtime != self._accounts_mtime:
            self._records_by_hash = {}
            payload = _read_json(self._accounts_path)
            for record in payload.get("accounts") or []:
                if not isinstance(record, dict):
                    continue
                token = str(record.get("token") or "")
                if token:
                    self._records_by_hash[token_hash(token)] = record
            self._accounts_mtime = accounts_mtime

    def _min_credit_threshold_locked(self) -> int:
        return self._min_credit_threshold


def _accounts_path(payload: dict[str, Any]) -> str:
    config_dir = os.path.dirname(os.path.abspath(config.SOUSAKU_CONFIG_PATH))
    path = payload.get("accounts_path") or "sousaku_accounts.json"
    return path if os.path.isabs(path) else os.path.join(config_dir, path)


def _min_credit_threshold(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get("min_credit_threshold", 5))
    except (TypeError, ValueError):
        return 5


def _read_json(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _mtime(path: str | os.PathLike[str] | None) -> float | None:
    if not path:
        return None
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


ACCOUNT_POOL = SousakuAccountPool()
