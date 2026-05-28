"""
APIMart Image Generation Backend
Flask API server for Gemini-3-Pro-Image-Preview (NanoBananaPro)
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import base64
import io
import json
import os
import re
import sys
import time
import threading
from PIL import Image, ImageFilter
import config
from config import TELEGRAPH_URL
from config import JOBS_DB_PATH, JOB_WORKER_ENABLED, JOB_WORKER_MAX_WORKERS, JOB_PROVIDER_LIMITS
from services.reference_inputs import load_reference_image
from services.public_uploader import upload_public_image
from services.reference_library import ensure_public_url
from services.reference_inputs import normalize_reference_inputs
from services.jobs import JobStore, JobWorker
from services import provider_registry
import concurrent.futures
import openai

import logging
from urllib.parse import urlsplit
from werkzeug.serving import WSGIRequestHandler

# 过滤掉高频成功访问日志刷屏；错误状态仍然保留，方便排查。
QUIET_ACCESS_PATHS = (
    "/api/providers",
    "/api/settings",
    "/api/capabilities",
    "/api/jobs",
    "/api/openai-tasks",
    "/api/provider-accounts",
    "/api/serve-image",
    "/api/thumbnail",
    "/api/gallery",
    "/api/storage/usage",
    "/api/sousaku-task/",
)
QUIET_ACCESS_METHODS = {"GET", "OPTIONS"}
QUIET_ACCESS_STATUS = {200, 204, 304}
QUIET_ACCESS_METHOD_STATUS = {
    ("POST", "/api/jobs", 202),
}


def _is_quiet_access_path(path: str) -> bool:
    return any(path == quiet or path.startswith(f"{quiet}/") for quiet in QUIET_ACCESS_PATHS)


class QuietAccessLogs(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        match = re.search(r'"([A-Z]+)\s+([^"]+?)\s+HTTP/', message)
        if not match:
            return True
        method = match.group(1)
        path = urlsplit(match.group(2)).path
        status_match = re.search(r'"\s+(\d{3})\s+', message)
        status = int(status_match.group(1)) if status_match else None
        if (method, path, status) in QUIET_ACCESS_METHOD_STATUS:
            return False
        if method in QUIET_ACCESS_METHODS and status in QUIET_ACCESS_STATUS and _is_quiet_access_path(path):
            return False
        return True


_original_log_request = WSGIRequestHandler.log_request


def _quiet_log_request(self, code="-", size="-"):
    try:
        method, target, *_ = self.requestline.split()
        status = int(code)
        path = urlsplit(target).path
        if (method, path, status) in QUIET_ACCESS_METHOD_STATUS:
            return
        if method in QUIET_ACCESS_METHODS and status in QUIET_ACCESS_STATUS and _is_quiet_access_path(path):
            return
    except Exception:
        pass
    return _original_log_request(self, code, size)


WSGIRequestHandler.log_request = _quiet_log_request

logging.getLogger("werkzeug").addFilter(QuietAccessLogs())

LOG_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "OK": 25, "WARN": 30, "ERROR": 40}
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _log_color_enabled():
    return bool(config.LOG_COLOR and sys.stdout.isatty())


if _log_color_enabled() and os.name == "nt":
    os.system("")

LOG_COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[37m",
    "OK": "\033[32m",
    "WARN": "\033[33m",
    "ERROR": "\033[31m",
}
SCOPE_COLORS = {
    "APIMART": "\033[94m",
    "CHATGPT2API": "\033[95m",
    "NANOBANANA2": "\033[92m",
    "CLIPROXY": "\033[96m",
    "OPENAI": "\033[95m",
    "SOUSAKU": "\033[93m",
    "COMFYUI": "\033[92m",
    "GALLERY": "\033[95m",
    "URL": "\033[33m",
    "UPLOAD": "\033[33m",
    "STARTUP": "\033[37m",
    "TASK": "\033[36m",
    "MASK": "\033[90m",
    "THOUGHT": "\033[95m",
    "JOB": "\033[36m",
}
LEVEL_ICONS = {
    "DEBUG": "·",
    "INFO": "•",
    "OK": "✅",
    "WARN": "⚠️",
    "ERROR": "❌",
}
FIELD_LABELS = {
    "model": "模型",
    "size": "尺寸",
    "ratio": "比例",
    "quality": "质量",
    "resolution": "分辨率",
    "image_size": "清晰度",
    "n": "数量",
    "refs": "参考图",
    "mask": "遮罩",
    "feather": "羽化",
    "input_max_edge": "输入边长",
    "success": "成功",
    "requested": "请求",
    "images": "图片",
    "file": "文件",
    "status": "状态",
    "progress": "进度",
    "elapsed": "耗时",
    "route": "接口",
    "index": "序号",
    "attempt": "重试",
    "error": "错误",
    "message": "信息",
    "body": "返回",
    "bytes": "字节",
    "count": "数量",
    "item": "请求",
    "files": "文件数",
    "task": "任务",
    "account": "账号",
    "credit_used": "使用额度",
    "credit_left": "剩余额度",
    "credit_before": "提交前额度",
    "token": "Token",
    "attempts": "重试",
    "thinking": "思考",
    "radius": "半径",
    "path": "路径",
    "url": "URL",
    "original": "原始",
    "final": "压缩后",
    "target": "目标",
    "source": "原图",
    "port": "端口",
    "frontend_port": "前端",
    "port_file": "端口文件",
    "default": "默认",
    "actual": "实际",
    "method": "方法",
    "name": "说明",
    "job": "任务",
    "provider": "渠道",
    "max_workers": "Worker数",
}
LOG_RESET = "\033[0m"


def _short(value, limit=140):
    text = str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[:limit] + "..."
    return text


class SousakuProgressPanel:
    def __init__(self):
        self.rows = {}
        self.lines_rendered = 0
        self.lock = None
        try:
            import threading

            self.lock = threading.RLock()
        except Exception:
            self.lock = None

    def is_enabled(self):
        return bool(self.lock and config.SOUSAKU_PROGRESS_PANEL and sys.stdout.isatty())

    def update(self, task_id, *, status, progress, images, elapsed):
        if not self.is_enabled():
            return False
        with self.lock:
            row = self.rows.setdefault(task_id, {"created_at": time.time()})
            row.update({
                "status": status or "running",
                "progress": progress,
                "images": images,
                "elapsed": elapsed,
                "updated_at": time.time(),
            })
            self._render_locked()
        return True

    def finish(self, task_id):
        if not self.is_enabled():
            return
        with self.lock:
            self.rows.pop(task_id, None)
            self._render_locked()

    def before_log(self):
        if not self.is_enabled():
            return
        with self.lock:
            self._clear_locked()

    def after_log(self):
        if not self.is_enabled():
            return
        with self.lock:
            self._render_locked()

    def _clear_locked(self):
        if self.lines_rendered <= 0:
            return
        for _ in range(self.lines_rendered):
            sys.stdout.write("\033[F\033[K")
        sys.stdout.flush()
        self.lines_rendered = 0

    def _render_locked(self):
        self._clear_locked()
        now = time.time()
        self.rows = {
            task_id: row
            for task_id, row in self.rows.items()
            if now - row.get("created_at", now) < config.SOUSAKU_STALE_TASK_SECONDS
        }
        if not self.rows:
            return

        ordered = sorted(self.rows.items(), key=lambda item: item[1].get("created_at", 0))
        lines = [
            f"{time.strftime('%H:%M:%S')} SOUSAKU 任务进度面板 ({len(ordered)} active)",
            "任务ID                 状态       进度   图片   耗时",
        ]
        for task_id, row in ordered:
            progress = row.get("progress")
            progress_text = "-" if progress is None else f"{progress}%"
            lines.append(
                f"{task_id[:20]:<20} "
                f"{_short(row.get('status', 'running'), 10):<10} "
                f"{progress_text:<6} "
                f"{row.get('images', 0):<6} "
                f"{row.get('elapsed', '-')}"
            )

        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()
        self.lines_rendered = len(lines)


_SOUSAKU_PROGRESS_PANEL = SousakuProgressPanel()
_LOG_OUTPUT_LOCK = threading.RLock()


def log_event(scope, message, level="INFO", icon=None, **fields):
    level = level.upper()
    if LOG_LEVEL_ORDER.get(level, 20) < LOG_LEVEL_ORDER.get(config.LOG_LEVEL, 20):
        return

    timestamp = time.strftime("%H:%M:%S")
    scope = scope.upper()
    provider = fields.get("provider")
    provider_name = str(provider or "").upper()
    scope_display = f"JOB={provider_name}" if scope == "JOB" and provider_name else scope
    if scope == "JOB" and provider_name:
        fields.pop("provider", None)
    icon_text = icon or LEVEL_ICONS.get(level, "•")
    scope_text = f"[{scope_display:<11}]"
    prompt = fields.pop("prompt", None)
    parts = []
    if prompt:
        parts.append(f"\"{_short(prompt, 48)}\"")
    for key, value in fields.items():
        if value is None:
            continue
        label = FIELD_LABELS.get(key, key)
        parts.append(f"{label}={_short(value)}")
    field_text = " ".join(parts)

    if _log_color_enabled():
        icon_color = LOG_COLORS.get(level, "")
        scope_color = SCOPE_COLORS.get(scope, LOG_COLORS.get(level, ""))
        provider_color = SCOPE_COLORS.get(provider_name, scope_color)
        if scope == "JOB" and provider_name:
            bracket_width = max(11, len(scope_display))
            padding = " " * max(0, bracket_width - len(scope_display))
            colored_scope = (
                f"{scope_color}[JOB={LOG_RESET}"
                f"{provider_color}{provider_name}{LOG_RESET}"
                f"{scope_color}{padding}]{LOG_RESET}"
            )
            prefix = f"{timestamp} {icon_color}{icon_text}{LOG_RESET} {colored_scope}"
        else:
            prefix = f"{timestamp} {icon_color}{icon_text}{LOG_RESET} {scope_color}{scope_text}{LOG_RESET}"
    else:
        prefix = f"{timestamp} {icon_text} {scope_text}"
    line = f"{prefix} {message}"
    if field_text:
        line += f" | {field_text}"
    with _LOG_OUTPUT_LOCK:
        _SOUSAKU_PROGRESS_PANEL.before_log()
        print(line, flush=True)
        _SOUSAKU_PROGRESS_PANEL.after_log()


_SOUSAKU_TASK_LOG_STATE = {}


def _to_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sousaku_log_state(task_id, now):
    return _SOUSAKU_TASK_LOG_STATE.setdefault(task_id, {
        "started_at": now,
        "last_log_at": 0,
        "last_progress": None,
        "last_image_count": 0,
        "final_logged": False,
    })


def _terminal_sousaku_status(status):
    return status in ("succeeded", "success", "completed", "failed", "error")


def _apply_sousaku_timeout_policy(task_id, result):
    now = time.time()
    status = (result.get("status") or result.get("data", {}).get("status") or "").lower()
    if _terminal_sousaku_status(status):
        return result

    state = _sousaku_log_state(task_id, now)
    elapsed_seconds = int(now - state["started_at"])
    if elapsed_seconds < config.SOUSAKU_STALE_TASK_SECONDS:
        return result

    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    images = data.get("result", {}).get("images", [])
    progress = data.get("progress")
    timeout_minutes = max(1, config.SOUSAKU_STALE_TASK_SECONDS // 60)
    return {
        "status": "failed",
        "error": {
            "message": f"Sousaku 任务超过 {timeout_minutes} 分钟仍未完成，已在 APIMART 本地标记为超时。"
        },
        "data": {
            "status": "failed",
            "task_id": data.get("task_id") or task_id,
            "progress": progress,
            "result": {"images": images},
        },
    }


def _log_sousaku_task_progress(task_id, result):
    now = time.time()
    status = (result.get("status") or result.get("data", {}).get("status") or "").lower()
    data = result.get("data") or {}
    images = data.get("result", {}).get("images", [])
    progress = data.get("progress")
    image_count = len(images)
    error_message = (result.get("error") or {}).get("message")
    state = _sousaku_log_state(task_id, now)
    elapsed_seconds = int(now - state["started_at"])
    elapsed = f"{elapsed_seconds}s"

    if _terminal_sousaku_status(status):
        if state.get("final_logged"):
            return
        state["final_logged"] = True
        _SOUSAKU_PROGRESS_PANEL.finish(task_id)
        log_event(
            "SOUSAKU",
            "任务状态",
            "OK" if status in ("succeeded", "success", "completed") else "ERROR",
            task=task_id[:20],
            status=status,
            progress=progress,
            images=image_count,
            elapsed=elapsed,
            error=error_message,
        )
        return

    if elapsed_seconds >= config.SOUSAKU_STALE_TASK_SECONDS:
        if state.get("stale_logged"):
            return
        state["stale_logged"] = True
        state["final_logged"] = True
        _SOUSAKU_PROGRESS_PANEL.finish(task_id)
        log_event(
            "SOUSAKU",
            "任务疑似卡死，已从进度面板移除",
            "WARN",
            task=task_id[:20],
            status=status or "running",
            progress=progress,
            images=image_count,
            elapsed=elapsed,
        )
        return

    progress_number = _to_number(progress)
    last_progress = _to_number(state.get("last_progress"))
    progress_step_changed = (
        progress_number is not None
        and (last_progress is None or abs(progress_number - last_progress) >= 20)
    )
    image_count_changed = image_count != state.get("last_image_count")
    interval_due = now - state.get("last_log_at", 0) >= 10
    if not (progress_step_changed or image_count_changed or interval_due):
        return

    state["last_log_at"] = now
    state["last_progress"] = progress
    state["last_image_count"] = image_count
    if _SOUSAKU_PROGRESS_PANEL.update(
        task_id,
        status=status or "running",
        progress=progress,
        images=image_count,
        elapsed=elapsed,
    ):
        return
    log_event(
        "SOUSAKU",
        "任务进度",
        "INFO",
        task=task_id[:20],
        status=status or "running",
        progress=progress,
        images=image_count,
        elapsed=elapsed,
    )

# Max file size: 10MB
MAX_FILE_SIZE = 10 * 1024 * 1024

app = Flask(__name__)
app.json.ensure_ascii = False
app.json.compact = True
CORS(app)
app.config["APIMART_LOG_EVENT"] = log_event

from routes.capabilities import capabilities_bp
from routes.files import files_bp
from routes.gallery import gallery_bp
from routes.imports import imports_bp
from routes.references import references_bp
from routes.settings import settings_bp
from services.sousaku_provider import create_task as create_sousaku_task, get_task as get_sousaku_task, refresh_account_records as refresh_sousaku_account_records
from services.sousaku_provider import refresh_account_records_for_tokens as refresh_sousaku_account_records_for_tokens
from services.sousaku.account_pool import ACCOUNT_POOL

app.register_blueprint(capabilities_bp)
app.register_blueprint(files_bp)
app.register_blueprint(gallery_bp)
app.register_blueprint(imports_bp)
app.register_blueprint(references_bp)
app.register_blueprint(settings_bp)

_JOB_STORE = JobStore(JOBS_DB_PATH)
_JOB_WORKER = JobWorker(
    store=_JOB_STORE,
    provider_limits=JOB_PROVIDER_LIMITS,
    max_workers=JOB_WORKER_MAX_WORKERS,
    logger=log_event,
)


def _update_legacy_provider_job_progress(data, provider, results):
    """Update Job progress for legacy sync providers as each image is saved."""
    if not isinstance(data, dict):
        return
    job_id = data.get("_job_id")
    if not job_id:
        return
    try:
        requested = int(data.get("n") or len(results) or 1)
    except (TypeError, ValueError):
        requested = max(1, len(results))
    requested = max(1, requested)
    done = len(results)
    progress = min(95, 5 + int(90 * min(done, requested) / requested))
    normalized = []
    for index, image in enumerate(results, start=1):
        if not isinstance(image, dict):
            continue
        item = dict(image)
        item.update({
            "provider": provider,
            "job_id": job_id,
            "index": index,
        })
        normalized.append(item)
    _JOB_STORE.update_job(job_id, status="running", progress=progress, result_json=normalized)


def _update_legacy_provider_job_count_progress(data, done, requested):
    """Update progress for legacy providers before images are parsed/saved."""
    if not isinstance(data, dict):
        return
    job_id = data.get("_job_id")
    if not job_id:
        return
    try:
        requested = int(requested or data.get("n") or 1)
    except (TypeError, ValueError):
        requested = 1
    requested = max(1, requested)
    progress = min(94, 5 + int(90 * min(done, requested) / requested))
    _JOB_STORE.update_job(job_id, status="running", progress=progress)

# OpenAI Client for Images API
openai_client = openai.OpenAI(
    api_key=config.OPENAI_API_KEY,
    base_url=config.OPENAI_BASE_URL
)

def _chatgpt2api_root_url():
    base = config.OPENAI_BASE_URL.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _chatgpt2api_headers():
    headers = {}
    if config.OPENAI_API_KEY:
        headers["Authorization"] = f"Bearer {config.OPENAI_API_KEY}"
    return headers


def _reference_images_to_uploads(image_urls):
    uploads = []
    for idx, img_info in enumerate(image_urls or []):
        url = img_info.get("url", "") if isinstance(img_info, dict) else img_info
        if not url:
            continue
        try:
            if url.startswith("data:"):
                header, b64_data = url.split(",", 1)
                image_data = base64.b64decode(b64_data)
                mime_type = header.split(";")[0].split(":")[1] if ":" in header else "image/png"
                ext = mime_type.split("/")[1] if "/" in mime_type else "png"
                uploads.append(("image", (f"ref_{idx}.{ext}", image_data, mime_type)))
            else:
                log_event("CHATGPT2API", "准备参考图", "DEBUG", index=idx, url=url[:80])
                image = load_reference_image(url, timeout=30, proxies=config.HTTP_PROXIES)
                uploads.append(("image", (f"ref_{idx}.{image.suffix.lstrip('.')}", image.data, image.content_type)))
        except Exception as e:
            log_event("CHATGPT2API", "参考图处理失败", "WARN", index=idx, error=e)
    return uploads


def _save_openai_task_image(image_url, task_id, index=0):
    if not image_url:
        return None
    os.makedirs(config.OPENAI_SAVE_DIR, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    suffix = os.urandom(2).hex().upper()
    filename = f"openai_task_{timestamp}_{task_id[:8]}_{index+1:02d}_{suffix}.png"
    filepath = os.path.join(config.OPENAI_SAVE_DIR, filename)

    if image_url.startswith("data:"):
        _, b64_data = image_url.split(",", 1)
        image_bytes = base64.b64decode(b64_data)
    elif len(image_url) > 100 and not image_url.startswith(("http://", "https://", "/")):
        image_bytes = base64.b64decode(image_url)
    else:
        proxies = None if "127.0.0.1" in image_url or "localhost" in image_url else config.HTTP_PROXIES
        response = requests.get(image_url, timeout=120, proxies=proxies)
        response.raise_for_status()
        image_bytes = response.content

    with open(filepath, "wb") as f:
        f.write(image_bytes)
    with Image.open(filepath) as saved_img:
        width, height = saved_img.size
    log_event("CHATGPT2API", "保存图片", "OK", icon="🖼️", file=filename)
    return {
        "saved_path": filepath,
        "filename": filename,
        "width": width,
        "height": height,
        "url": image_url,
    }

# CLIProxyAPI Client (OpenAI-compatible local proxy for Codex/Claude/Gemini OAuth)
cliproxy_client = openai.OpenAI(
    api_key=config.CLIPROXY_API_KEY,
    base_url=config.CLIPROXY_BASE_URL
)

# API Headers
def get_headers():
    return {
        "Authorization": f"Bearer {config.API_KEY}",
        "Content-Type": "application/json"
    }


@app.route('/')
def index():
    """Welcome page with API documentation"""
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>APIMart API Server</title>
        <style>
            body { font-family: -apple-system, sans-serif; background: #0a0a0f; color: #fff; padding: 40px; }
            h1 { background: linear-gradient(135deg, #7c3aed, #a855f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
            .endpoint { background: #1a1a25; padding: 15px; border-radius: 8px; margin: 10px 0; border-left: 3px solid #7c3aed; }
            .method { color: #10b981; font-weight: bold; }
            .path { color: #a855f7; }
            a { color: #06b6d4; }
        </style>
    </head>
    <body>
        <h1>✨ APIMart Image Generation API</h1>
        <p>后端服务运行中！</p>
        <h2>📡 API 端点</h2>
        <div class="endpoint"><span class="method">GET</span> <span class="path">/api/balance</span> - 查询账户余额</div>
        <div class="endpoint"><span class="method">POST</span> <span class="path">/api/generate</span> - 提交图像生成任务 (APIMart API)</div>
        <div class="endpoint"><span class="method">POST</span> <span class="path">/api/generate-openai</span> - 图像生成 (ChatGPT2API, 旧同步)</div>
        <div class="endpoint"><span class="method">POST</span> <span class="path">/api/generate-openai-tasks</span> - 创建 ChatGPT2API 图像任务</div>
        <div class="endpoint"><span class="method">GET</span> <span class="path">/api/openai-tasks</span> - 查询 ChatGPT2API 图像任务</div>
        <div class="endpoint"><span class="method">GET</span> <span class="path">/api/task/&lt;id&gt;</span> - 查询任务状态</div>
        <div class="endpoint"><span class="method">POST</span> <span class="path">/api/upload-image</span> - 上传图片到图床</div>
        <div class="endpoint"><span class="method">GET</span> <span class="path">/api/proxy-image</span> - 代理下载图片</div>
        <p style="margin-top: 30px;">📂 <a href="file:///F:/CodeProject/ApiMart/frontend/index.html">打开前端页面</a></p>
    </body>
    </html>
    '''


@app.route('/api/balance', methods=['GET'])
def get_balance():
    """Query account token balance"""
    try:
        response = requests.get(
            f"{config.API_BASE_URL}/v1/balance",
            headers=get_headers()
        )
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/generate', methods=['POST'])
def generate_image():
    """Submit image generation task"""
    try:
        data = request.json
        
        payload = {
            "model": data.get("model", "gemini-3-pro-image-preview"),
            "prompt": data.get("prompt", ""),
            "size": data.get("size", "1:1"),
            "n": 1,
            "resolution": data.get("resolution", "2K")
        }

        if payload["model"] == "gpt-image-2-official":
            quality = data.get("quality")
            moderation = data.get("moderation")
            if quality in {"auto", "low", "medium", "high"}:
                payload["quality"] = quality
            if moderation in {"auto", "low"}:
                payload["moderation"] = moderation
        
        # Handle reference images if provided
        # Frontend sends [{url: "..."}, ...], APIMart API expects ["url1", "url2"] or "url"
        image_urls = data.get("image_urls", [])
        if image_urls:
            # Extract plain URL strings from objects
            urls = []
            for item in image_urls:
                if isinstance(item, dict):
                    url = item.get("public_url") or item.get("url") or ""
                else:
                    url = item
                if not url:
                    continue
                try:
                    urls.append(ensure_public_url(item, provider="apimart"))
                except Exception as exc:
                    if str(url).startswith(("http://", "https://")):
                        urls.append(str(url))
                    else:
                        raise RuntimeError(f"参考图公网上传失败，请重试或更换图床: {exc}") from exc
            if urls:
                payload["image_urls"] = urls
        
        # Handle mask data if provided (for gpt-image-2 models on APIMart)
        mask_data_raw = data.get("mask_data")
        feather = data.get("feather", 0)
        if mask_data_raw:
            payload["mask_data"] = mask_data_raw
            if feather > 0:
                payload["feather"] = feather
        
        log_event(
            "APIMART",
            "开始生成",
            icon="▶️",
            prompt=payload.get("prompt"),
            model=payload.get("model"),
            size=payload.get("size"),
            resolution=payload.get("resolution"),
            refs=len(payload.get("image_urls", [])) if isinstance(payload.get("image_urls"), list) else int(bool(payload.get("image_urls"))),
            mask=bool(mask_data_raw),
            feather=feather if mask_data_raw else None,
        )
        
        response = requests.post(
            f"{config.API_BASE_URL}/v1/images/generations",
            headers=get_headers(),
            json=payload
        )
        
        if response.status_code >= 400:
            log_event("APIMART", "请求失败", "ERROR", status=response.status_code, body=response.text[:300])
        else:
            log_event("APIMART", "任务已提交", "OK", status=response.status_code)
        
        return jsonify(response.json())
    except Exception as e:
        log_event("APIMART", "生成异常", "ERROR", error=e)
        return jsonify({"error": {"message": str(e)}}), 500


@app.route('/api/generate-openai', methods=['POST'])
def generate_image_openai():
    """Generate image using ChatGPT2API's OpenAI-compatible Images API."""
    try:
        data = request.json
        
        prompt = data.get("prompt", "")
        ratio = data.get("ratio") or data.get("size", "16:9")
        try:
            n = int(data.get("n", 1))
        except (TypeError, ValueError):
            n = 1
        n = max(1, min(n, 10))
        
        # Handle reference images if provided (as base64 data URIs or URLs)
        image_urls = data.get("image_urls", [])
        
        log_event("CHATGPT2API", "开始生成", icon="▶️", prompt=prompt, model=config.OPENAI_IMAGE_MODEL, ratio=ratio, n=n, refs=len(image_urls))
        
        # Ensure save directory exists
        os.makedirs(config.OPENAI_SAVE_DIR, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        results = []
        
        # Build API request using multipart format
        api_url = f"{config.OPENAI_BASE_URL}/images/edits"
        headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}
        
        # Prepare multipart data
        form_data = {
            "prompt": prompt if not image_urls else f"请根据参考图中的人物形象生成图片，保持人物特征一致。\n\n{prompt}",
            "model": config.OPENAI_IMAGE_MODEL,
            "n": "1",
            "size": ratio,
            "response_format": "b64_json",
        }
        
        files = []
        temp_files = []  # Track temp files to clean up
        
        # Handle reference images
        if image_urls:
            for idx, img_info in enumerate(image_urls):
                url = img_info.get("url", "") if isinstance(img_info, dict) else img_info
                if not url:
                    continue
                    
                # Check if it's a base64 data URI or URL
                if url.startswith("data:"):
                    # Extract base64 data from data URI
                    try:
                        # Format: data:image/png;base64,xxxxx
                        header, b64_data = url.split(",", 1)
                        image_data = base64.b64decode(b64_data)
                        mime_type = header.split(";")[0].split(":")[1] if ":" in header else "image/png"
                        ext = mime_type.split("/")[1] if "/" in mime_type else "png"
                        
                        files.append(("image", (f"ref_{idx}.{ext}", image_data, mime_type)))
                    except Exception as e:
                        log_event("CHATGPT2API", "参考图解析失败", "WARN", index=idx, error=e)
                else:
                    # It's a URL, download it first
                    try:
                        log_event("CHATGPT2API", "准备参考图", "DEBUG", url=url[:80])
                        image = load_reference_image(url, timeout=30, proxies=config.HTTP_PROXIES)
                        files.append(("image", (f"ref_{idx}.{image.suffix.lstrip('.')}", image.data, image.content_type)))
                    except Exception as e:
                        log_event("CHATGPT2API", "参考图下载失败", "WARN", index=idx, error=e)
        
        images = []
        request_errors = []
        for request_idx in range(n):
            # Match ChatGPT2API's own frontend behavior: submit one image task per output.
            if not files:
                if request_idx == 0:
                    log_event("CHATGPT2API", "选择接口", "DEBUG", route="images/generations")
                api_url = f"{config.OPENAI_BASE_URL}/images/generations"
                json_payload = {
                    "prompt": prompt,
                    "model": config.OPENAI_IMAGE_MODEL,
                    "n": 1,
                    "size": ratio,
                    "response_format": "b64_json",
                }
                response = requests.post(api_url, json=json_payload, headers=headers, timeout=1200)
            else:
                if request_idx == 0:
                    log_event("CHATGPT2API", "选择接口", "DEBUG", route="images/edits", files=len(files))
                response = requests.post(api_url, files=files, data=form_data, headers=headers, timeout=1200)

            if response.status_code != 200:
                error_text = response.text[:500]
                log_event("CHATGPT2API", "接口报错", "ERROR", status=response.status_code, body=error_text, item=f"{request_idx+1}/{n}")
                request_errors.append(f"{request_idx+1}/{n}: HTTP {response.status_code} {error_text}")
                continue

            result = response.json()
            images.extend(result.get("data", []))
        
        log_event("CHATGPT2API", "生成完成", "OK", images=len(images), requested=n)
        if len(images) < n:
            log_event("CHATGPT2API", "返回数量不足", "WARN", images=len(images), requested=n)
        
        # Save images
        for idx, img_data in enumerate(images):
            b64 = img_data.get("b64_json")
            if b64:
                try:
                    filename = f"openai_{timestamp}_{idx+1:02d}_{os.urandom(2).hex().upper()}.png"
                    filepath = os.path.join(config.OPENAI_SAVE_DIR, filename)
                    with open(filepath, "wb") as f:
                        f.write(base64.b64decode(b64))
                    with Image.open(filepath) as saved_img:
                        actual_width, actual_height = saved_img.size
                    log_event("CHATGPT2API", "保存图片", "OK", icon="🖼️", file=filename)
                    
                    # Don't return full base64 to frontend to avoid localStorage bloat
                    results.append({
                        "saved_path": filepath,
                        "filename": filename,
                        "width": actual_width,
                        "height": actual_height,
                        "data_uri": f"data:image/png;base64,{b64[:100]}..."  # Truncated preview
                    })
                except Exception as e:
                    log_event("CHATGPT2API", "保存图片失败", "WARN", index=idx, error=e)
                    # Still add to results with base64 as fallback
                    results.append({
                        "saved_path": None,
                        "filename": f"openai_{timestamp}_{idx+1:02d}.png",
                        "data_uri": f"data:image/png;base64,{b64}"
                    })
        
        if not results:
            error_message = request_errors[-1] if request_errors else "未获取到图片数据"
            return jsonify({"success": False, "error": {"message": error_message}}), 500
        
        return jsonify({
            "success": True,
            "data": results,
            "count": len(results),
            "save_dir": config.OPENAI_SAVE_DIR
        })
        
    except requests.exceptions.Timeout:
        log_event("CHATGPT2API", "请求超时", "ERROR")
        return jsonify({"success": False, "error": {"message": "请求超时，请重试"}}), 408
    except Exception as e:
        log_event("CHATGPT2API", "生成异常", "ERROR", error=e)
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


@app.route('/api/generate-openai-tasks', methods=['POST'])
def generate_image_openai_tasks():
    """Create ChatGPT2API image tasks. APIMart frontend polls them separately."""
    try:
        data = request.json or {}
        prompt = data.get("prompt", "")
        ratio = data.get("ratio") or data.get("size", "16:9")
        try:
            n = int(data.get("n", 1))
        except (TypeError, ValueError):
            n = 1
        n = max(1, min(n, 10))
        image_urls = data.get("image_urls", [])
        uploads = _reference_images_to_uploads(image_urls)

        root_url = _chatgpt2api_root_url()
        headers = _chatgpt2api_headers()
        tasks = []
        mode = "edits" if uploads else "generations"
        log_event("CHATGPT2API", "提交任务", icon="▶️", prompt=prompt, model=config.OPENAI_IMAGE_MODEL, ratio=ratio, n=n, refs=len(image_urls), mode=mode)

        for idx in range(n):
            client_task_id = f"apimart-{int(time.time() * 1000)}-{idx}-{os.urandom(2).hex()}"
            if uploads:
                form_data = {
                    "client_task_id": client_task_id,
                    "prompt": f"请根据参考图中的人物形象生成图片，保持人物特征一致。\n\n{prompt}",
                    "model": config.OPENAI_IMAGE_MODEL,
                    "size": ratio,
                }
                response = requests.post(
                    f"{root_url}/api/image-tasks/edits",
                    headers=headers,
                    files=uploads,
                    data=form_data,
                    timeout=60,
                )
            else:
                payload = {
                    "client_task_id": client_task_id,
                    "prompt": prompt,
                    "model": config.OPENAI_IMAGE_MODEL,
                    "size": ratio,
                }
                response = requests.post(
                    f"{root_url}/api/image-tasks/generations",
                    headers={**headers, "Content-Type": "application/json"},
                    json=payload,
                    timeout=60,
                )

            if response.status_code >= 400:
                error_text = response.text[:500]
                log_event("CHATGPT2API", "任务提交失败", "ERROR", item=f"{idx+1}/{n}", status=response.status_code, body=error_text)
                tasks.append({
                    "task_id": client_task_id,
                    "status": "error",
                    "index": idx,
                    "error": {"message": error_text},
                })
                continue

            item = response.json()
            task_id = item.get("id") or client_task_id
            tasks.append({
                "task_id": task_id,
                "status": item.get("status", "queued"),
                "index": idx,
            })

        return jsonify({"success": True, "data": tasks})
    except Exception as e:
        log_event("CHATGPT2API", "提交任务异常", "ERROR", error=e)
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


@app.route('/api/openai-tasks', methods=['GET'])
def get_openai_tasks():
    """Proxy ChatGPT2API image task status and save successful images locally."""
    ids = request.args.get("ids", "")
    task_ids = [task_id.strip() for task_id in ids.split(",") if task_id.strip()]
    if not task_ids:
        return jsonify({"success": False, "error": {"message": "ids is required"}}), 400

    try:
        root_url = _chatgpt2api_root_url()
        response = requests.get(
            f"{root_url}/api/image-tasks",
            params={"ids": ",".join(task_ids)},
            headers=_chatgpt2api_headers(),
            timeout=60,
        )
        if response.status_code >= 400:
            error_text = response.text[:500]
            log_event("CHATGPT2API", "查询任务失败", "ERROR", status=response.status_code, body=error_text)
            return jsonify({"success": False, "error": {"message": error_text}}), response.status_code

        payload = response.json()
        items = payload.get("items", [])
        normalized = []
        for item in items:
            task_id = item.get("id", "")
            status = item.get("status", "unknown")
            result_images = []
            if status == "success":
                for idx, img in enumerate(item.get("data") or []):
                    image_url = img.get("url") if isinstance(img, dict) else None
                    try:
                        saved = _save_openai_task_image(image_url, task_id, idx)
                        if saved:
                            result_images.append(saved)
                    except Exception as e:
                        log_event("CHATGPT2API", "保存任务图片失败", "WARN", task=task_id[:16], error=e)
                        result_images.append({
                            "url": image_url,
                            "download_failed": True,
                        })

            normalized.append({
                "task_id": task_id,
                "status": status,
                "data": result_images,
                "error": {"message": item.get("error")} if item.get("error") else None,
            })

        return jsonify({
            "success": True,
            "data": normalized,
            "missing_ids": payload.get("missing_ids", []),
        })
    except Exception as e:
        log_event("CHATGPT2API", "查询任务异常", "ERROR", error=e)
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


# ============ Nanobanana2 (Gemini 3.1 Flash Image - Native Protocol) ============

@app.route('/api/generate-nanobanana2', methods=['POST'])
def generate_image_nanobanana2():
    """Generate image using Gemini 3.1 Flash Image via native v1beta protocol (Nanobanana2)"""
    try:
        data = request.json

        prompt = data.get("prompt", "")
        ratio = data.get("ratio") or data.get("size", "16:9")  # Frontend sends ratio as 'size' field
        quality = data.get("quality", "hd")       # standard(1K), medium(2K), hd(4K)
        n = data.get("n", 1)
        image_urls = data.get("image_urls", [])    # Reference images (base64 data URIs or URLs)
        thinking_level = data.get("thinking_level", "High")  # "High" or "Minimal"
        mask_data_raw = data.get("mask_data")       # Mask PNG as base64 data URI
        feather = data.get("feather", 0)

        # Map quality label to Gemini imageSize
        quality_map = {"hd": "4K", "medium": "2K", "standard": "1K", "4k": "4K", "2k": "2K", "1k": "1K"}
        image_size = quality_map.get(quality.lower(), "4K") if quality else "4K"

        has_mask = bool(mask_data_raw)
        log_event("NANOBANANA2", "开始生成", icon="▶️", prompt=prompt, ratio=ratio, quality=quality, image_size=image_size, n=n, refs=len(image_urls), thinking=thinking_level, mask=has_mask)

        # Build content parts
        parts = []

        # Add reference images as inlineData
        for idx, img_info in enumerate(image_urls):
            url = img_info.get("url", "") if isinstance(img_info, dict) else img_info
            if not url:
                continue

            try:
                if url.startswith("data:"):
                    # Base64 data URI
                    header, b64_data = url.split(",", 1)
                    mime_type = header.split(";")[0].split(":")[1] if ":" in header else "image/png"
                    parts.append({"inlineData": {"mimeType": mime_type, "data": b64_data}})
                else:
                    # External URL - download and convert to base64
                    log_event("NANOBANANA2", "准备参考图", "DEBUG", index=idx, url=url[:80])
                    image = load_reference_image(url, timeout=30, proxies=config.HTTP_PROXIES)
                    b64 = base64.b64encode(image.data).decode("utf-8")
                    parts.append({"inlineData": {"mimeType": image.content_type, "data": b64}})
            except Exception as e:
                log_event("NANOBANANA2", "参考图处理失败", "WARN", index=idx, error=e)

        # Add mask as inlineData if provided
        if has_mask:
            try:
                mask_bytes = _decode_mask_data(mask_data_raw, feather=feather)
                mask_b64 = base64.b64encode(mask_bytes).decode("utf-8")
                parts.append({"inlineData": {"mimeType": "image/png", "data": mask_b64}})
                # Modify prompt to instruct model about mask
                prompt = f"This is a mask image (transparent/empty areas should be edited). {prompt}"
                log_event("NANOBANANA2", "已附加遮罩", bytes=len(mask_bytes), feather=feather)
            except Exception as e:
                log_event("NANOBANANA2", "遮罩处理失败", "WARN", error=e)

        # Add text prompt (after images, so model sees images first)
        parts.append({"text": prompt})

        # 构建基础 contents
        contents_list = [
            {
                "role": "user",
                "parts": parts
            }
        ]

        # 如果在 config 中开启了强制突破限制，则追加模型伪装确认的上下文
        if config.ENABLE_NANOBANANA2_JAILBREAK and config.NANOBANANA2_JAILBREAK_PROMPT:
            contents_list.append({
                "role": "model",
                "parts": [{"text": config.NANOBANANA2_JAILBREAK_PROMPT}]
            })

        # Build Gemini native request payload
        payload = {
            "contents": contents_list,
            "generationConfig": {
                "responseModalities": ["IMAGE"],  # 尝试强制出图（尽管Manager可能会过滤它，以防万一还是加上）
                "imageConfig": {
                    "imageSize": image_size,
                    "aspectRatio": ratio
                },
                # 开启原生的“高等级思考”模式，让模型在出图前进行深度构思
                "thinkingConfig": {
                    "thinkingLevel": thinking_level,
                    "includeThoughts": True
                }
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "OFF"}
            ]
        }

        api_url = f"{config.NANOBANANA2_BASE_URL}/v1beta/models/gemini-3.1-flash-image:generateContent"
        nb2_headers = {}
        if config.NANOBANANA2_API_KEY:
            nb2_headers["Authorization"] = f"Bearer {config.NANOBANANA2_API_KEY}"

        # Send N concurrent requests
        def _send_one(idx):
            try:
                resp = requests.post(api_url, json=payload, headers=nb2_headers, timeout=300)
                if resp.status_code != 200:
                    log_event("NANOBANANA2", "请求失败", "ERROR", item=f"{idx+1}/{n}", status=resp.status_code, body=resp.text[:200])
                    return None
                return resp.json()
            except Exception as e:
                log_event("NANOBANANA2", "请求异常", "ERROR", item=f"{idx+1}/{n}", error=e)
                return None

        raw_results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(n, 6)) as executor:
            futures = [executor.submit(_send_one, i) for i in range(n)]
            for f in concurrent.futures.as_completed(futures):
                res = f.result()
                if res:
                    raw_results.append(res)
                    _update_legacy_provider_job_count_progress(data, len(raw_results), n)

        # Parse responses and save images
        os.makedirs(config.NANOBANANA2_SAVE_DIR, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        results = []
        thought_images = []  # 收集草图（thought阶段的图片）
        img_counter = 0

        for ri, result in enumerate(raw_results):
            candidates = result.get("candidates", [])
            for candidate in candidates:
                candidate_parts = candidate.get("content", {}).get("parts", [])
                for part in candidate_parts:
                    is_thought = part.get("thought", False)
                    
                    if "inlineData" in part:
                        mime_type = part["inlineData"].get("mimeType", "image/png")
                        b64_data = part["inlineData"].get("data", "")
                        if not b64_data:
                            continue

                        if is_thought:
                            # 草图：不保存到本地，只传 base64 给前端预览
                            thought_images.append({
                                "data_uri": f"data:{mime_type};base64,{b64_data}",
                                "mime_type": mime_type
                            })
                            continue

                        ext = "png"
                        if "jpeg" in mime_type or "jpg" in mime_type:
                            ext = "jpg"
                        elif "webp" in mime_type:
                            ext = "webp"

                        img_counter += 1
                        filename = f"nb2_{timestamp}_{img_counter:02d}_{os.urandom(2).hex().upper()}.{ext}"
                        filepath = os.path.join(config.NANOBANANA2_SAVE_DIR, filename)

                        try:
                            with open(filepath, "wb") as f:
                                f.write(base64.b64decode(b64_data))
                            log_event("NANOBANANA2", "保存图片", "OK", icon="🖼️", file=filename)
                            results.append({
                                "saved_path": filepath,
                                "filename": filename,
                                "data_uri": f"data:{mime_type};base64,{b64_data[:100]}..."
                            })
                            _update_legacy_provider_job_progress(data, "nanobanana2", results)
                        except Exception as e:
                            log_event("NANOBANANA2", "保存图片失败", "WARN", file=filename, error=e)
                            results.append({
                                "saved_path": None,
                                "filename": filename,
                                "data_uri": f"data:{mime_type};base64,{b64_data}"
                            })
                            _update_legacy_provider_job_progress(data, "nanobanana2", results)

        if thought_images:
            log_event("NANOBANANA2", "收到草图", count=len(thought_images))

        if not results and not thought_images:
            return jsonify({"success": False, "error": {"message": "未获取到图片数据"}}), 500

        return jsonify({
            "success": True,
            "data": results,
            "thought_images": thought_images,
            "count": len(results),
            "save_dir": config.NANOBANANA2_SAVE_DIR
        })

    except requests.exceptions.Timeout:
        log_event("NANOBANANA2", "请求超时", "ERROR")
        return jsonify({"success": False, "error": {"message": "请求超时，请重试"}}), 408
    except Exception as e:
        log_event("NANOBANANA2", "生成异常", "ERROR", error=e)
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


# ============ Shared Mask Utilities ============

def _resize_image_data(img_bytes: bytes, max_edge: int = 1536) -> bytes:
    """Resize an image to a maximum edge length, snapping to multiples of 16."""
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        width, height = img.size
        current_max = max(width, height)
        if current_max <= max_edge:
            return img_bytes
        
        scale = max_edge / float(current_max)
        target = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
        target = (max(16, (target[0] // 16) * 16), max(16, (target[1] // 16) * 16))
        
        resized = img.resize(target, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="PNG")
        log_event("CLIPROXY", "参考图缩放", source=f"{width}x{height}", target=f"{target[0]}x{target[1]}")
        return buf.getvalue()
    except Exception as e:
        log_event("CLIPROXY", "参考图缩放失败", "WARN", error=e)
        return img_bytes

def _decode_mask_data(mask_data: str, feather: int = 0, target_size: tuple = None) -> bytes:
    """Decode a base64 mask data URI and apply optional feathering.
    
    Args:
        mask_data: Data URI string (data:image/png;base64,...)
        feather: Gaussian blur radius for soft edges (0 = hard edge)
        target_size: Optional (width, height) to resize mask to match image
    
    Returns:
        PNG bytes ready for multipart upload
    """
    # Parse data URI
    if mask_data.startswith("data:"):
        _, b64 = mask_data.split(",", 1)
    else:
        b64 = mask_data
    
    mask_bytes = base64.b64decode(b64)
    mask_img = Image.open(io.BytesIO(mask_bytes)).convert("RGBA")
    
    # Resize to match image if needed
    if target_size and mask_img.size != target_size:
        mask_img = mask_img.resize(target_size, Image.Resampling.LANCZOS)
        log_event("MASK", "遮罩缩放", target=f"{target_size[0]}x{target_size[1]}")
    
    # Apply feathering (blur the alpha channel)
    if feather > 0:
        # Extract alpha, blur it, put back
        r, g, b, a = mask_img.split()
        a = a.filter(ImageFilter.GaussianBlur(radius=feather))
        mask_img = Image.merge("RGBA", (r, g, b, a))
        log_event("MASK", "遮罩羽化", radius=feather)
    
    buf = io.BytesIO()
    mask_img.save(buf, format="PNG")
    log_event("MASK", "遮罩已解析", size=f"{mask_img.size[0]}x{mask_img.size[1]}", bytes=len(buf.getvalue()))
    return buf.getvalue()


# ============ CLIProxyAPI (Codex/Claude/Gemini OAuth Local Proxy) ============

def _cliproxy_root_url():
    base = config.CLIPROXY_BASE_URL.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _build_gemini_image_parts(prompt, image_urls, mask_data_raw=None, feather=0, scope="CLIPROXY"):
    parts = []
    for idx, img_info in enumerate(image_urls or []):
        url = img_info.get("url", "") if isinstance(img_info, dict) else img_info
        if not url:
            continue
        try:
            if url.startswith("data:"):
                header, b64_data = url.split(",", 1)
                mime_type = header.split(";")[0].split(":")[1] if ":" in header else "image/png"
                parts.append({"inlineData": {"mimeType": mime_type, "data": b64_data}})
            else:
                log_event(scope, "准备参考图", "DEBUG", index=idx, url=url[:80])
                image = load_reference_image(url, timeout=30, proxies=config.HTTP_PROXIES)
                b64 = base64.b64encode(image.data).decode("utf-8")
                parts.append({"inlineData": {"mimeType": image.content_type, "data": b64}})
        except Exception as e:
            log_event(scope, "参考图处理失败", "WARN", index=idx, error=e)

    if mask_data_raw:
        try:
            mask_bytes = _decode_mask_data(mask_data_raw, feather=feather)
            mask_b64 = base64.b64encode(mask_bytes).decode("utf-8")
            parts.append({"inlineData": {"mimeType": "image/png", "data": mask_b64}})
            prompt = f"This is a mask image (transparent/empty areas should be edited). {prompt}"
            log_event(scope, "已附加遮罩", bytes=len(mask_bytes), feather=feather)
        except Exception as e:
            log_event(scope, "遮罩处理失败", "WARN", error=e)

    parts.append({"text": prompt})
    return parts


def _extract_inline_images_from_gemini_result(result):
    images = []
    for candidate in result.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if not inline:
                continue
            b64_data = inline.get("data", "")
            if not b64_data:
                continue
            mime_type = inline.get("mimeType") or inline.get("mime_type") or "image/png"
            try:
                img_bytes = base64.b64decode(b64_data)
                with Image.open(io.BytesIO(img_bytes)) as img:
                    width, height = img.size
            except Exception:
                width, height = 0, 0
            images.append({
                "b64": b64_data,
                "mime_type": mime_type,
                "width": width,
                "height": height,
                "is_thought": bool(part.get("thought", False)),
            })
    return images


def _save_cliproxy_gemini_image(image, timestamp, img_counter):
    mime_type = image["mime_type"]
    ext = "png"
    if "jpeg" in mime_type or "jpg" in mime_type:
        ext = "jpg"
    elif "webp" in mime_type:
        ext = "webp"

    filename = f"cliproxy_gemini_{timestamp}_{img_counter:02d}_{os.urandom(2).hex().upper()}.{ext}"
    filepath = os.path.join(config.CLIPROXY_SAVE_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(base64.b64decode(image["b64"]))
    log_event("CLIPROXY", "保存图片", "OK", icon="🖼️", file=filename)
    return {
        "saved_path": filepath,
        "filename": filename,
        "width": image.get("width"),
        "height": image.get("height"),
        "data_uri": f"data:{mime_type};base64,{image['b64'][:100]}...",
    }


def _generate_cliproxy_gemini_flash_image(prompt, ratio, resolution, quality, n, image_urls, mask_data_raw=None, feather=0, job_data=None):
    try:
        os.makedirs(config.CLIPROXY_SAVE_DIR, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        quality_to_size = {"low": "1K", "medium": "2K", "high": "4K", "auto": "4K"}
        image_size = resolution if resolution in {"1K", "2K", "4K"} else quality_to_size.get(str(quality).lower(), "4K")
        thinking_level = "High"
        parts = _build_gemini_image_parts(prompt, image_urls, mask_data_raw, feather, scope="CLIPROXY")

        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {
                    "imageSize": image_size,
                    "aspectRatio": ratio,
                },
                "thinkingConfig": {
                    "thinkingLevel": thinking_level,
                    "includeThoughts": True,
                },
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "OFF"},
            ],
        }

        api_url = f"{_cliproxy_root_url()}/api/provider/antigravity/v1beta/models/gemini-3.1-flash-image:generateContent"
        headers = {"Authorization": f"Bearer {config.CLIPROXY_API_KEY}"}

        def _send_one(idx):
            try:
                resp = requests.post(api_url, json=payload, headers=headers, timeout=1200)
                if resp.status_code != 200:
                    log_event("CLIPROXY", "请求失败", "ERROR", item=f"{idx+1}/{n}", status=resp.status_code, body=resp.text[:300])
                    return None
                return resp.json()
            except Exception as e:
                log_event("CLIPROXY", "请求异常", "ERROR", item=f"{idx+1}/{n}", error=e)
                return None

        raw_results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(n, 6)) as executor:
            futures = [executor.submit(_send_one, i) for i in range(n)]
            for f in concurrent.futures.as_completed(futures):
                res = f.result()
                if res:
                    raw_results.append(res)
                    _update_legacy_provider_job_count_progress(job_data, len(raw_results), n)

        results = []
        thought_images = []
        img_counter = 0
        for result in raw_results:
            inline_images = _extract_inline_images_from_gemini_result(result)
            if not inline_images:
                continue
            for image in inline_images:
                if image.get("is_thought"):
                    thought_images.append({
                        "data_uri": f"data:{image['mime_type']};base64,{image['b64']}",
                        "mime_type": image["mime_type"],
                    })
                    continue
                img_counter += 1
                try:
                    results.append(_save_cliproxy_gemini_image(image, timestamp, img_counter))
                    _update_legacy_provider_job_progress(job_data, "cliproxy", results)
                except Exception as e:
                    log_event("CLIPROXY", "保存图片失败", "WARN", error=e)
                    results.append({
                        "saved_path": None,
                        "filename": f"cliproxy_gemini_{timestamp}_{img_counter:02d}.png",
                        "data_uri": f"data:{image['mime_type']};base64,{image['b64']}",
                    })
                    _update_legacy_provider_job_progress(job_data, "cliproxy", results)

        if thought_images:
            log_event("CLIPROXY", "收到草图", count=len(thought_images))
        log_event("CLIPROXY", "生成完成", "OK", success=len(results), requested=n, model="gemini-3.1-flash-image")

        if not results and not thought_images:
            return jsonify({"success": False, "error": {"message": "未获取到图片数据"}}), 500

        return jsonify({
            "success": True,
            "data": results,
            "thought_images": thought_images,
            "count": len(results),
            "save_dir": config.CLIPROXY_SAVE_DIR,
        })
    except Exception as e:
        log_event("CLIPROXY", "Gemini生成异常", "ERROR", error=e)
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


def _resolve_cliproxy_openai_size(size, resolution=None):
    """Convert UI aspect ratio values into pixel sizes accepted by /images/generations."""
    raw = str(size or "").strip()
    if re.fullmatch(r"\d+x\d+", raw):
        return raw

    ratio = raw.replace("：", ":")
    resolution_key = str(resolution or "2K").upper()
    mapping = {
        "1:1": {"1K": "1024x1024", "2K": "2048x2048", "4K": "2880x2880"},
        "3:2": {"1K": "1536x1024", "2K": "2048x1360", "4K": "3504x2336"},
        "2:3": {"1K": "1024x1536", "2K": "1360x2048", "4K": "2336x3504"},
        "4:3": {"1K": "1024x768", "2K": "2048x1536", "4K": "3264x2448"},
        "3:4": {"1K": "768x1024", "2K": "1536x2048", "4K": "2448x3264"},
        "5:4": {"1K": "1280x1024", "2K": "2560x2048", "4K": "3200x2560"},
        "4:5": {"1K": "1024x1280", "2K": "2048x2560", "4K": "2560x3200"},
        "16:9": {"1K": "1536x864", "2K": "2048x1152", "4K": "3840x2160"},
        "9:16": {"1K": "864x1536", "2K": "1152x2048", "4K": "2160x3840"},
        "2:1": {"1K": "2048x1024", "2K": "2688x1344", "4K": "3840x1920"},
        "1:2": {"1K": "1024x2048", "2K": "1344x2688", "4K": "1920x3840"},
        "21:9": {"1K": "2016x864", "2K": "2688x1152", "4K": "3840x1648"},
        "9:21": {"1K": "864x2016", "2K": "1152x2688", "4K": "1648x3840"},
    }
    return mapping.get(ratio, {}).get(resolution_key) or mapping.get(ratio, {}).get("2K") or "2048x1152"


@app.route('/api/generate-cliproxy', methods=['POST'])
def generate_image_cliproxy():
    """Generate image using CLIProxyAPI local proxy (OpenAI-compatible, gpt-image-2)"""
    try:
        data = request.json

        prompt = data.get("prompt", "")
        size = data.get("size", "1024x1024")
        quality = data.get("quality", "high")
        n = data.get("n", 1)
        model = data.get("model", "gpt-image-2")
        image_urls = data.get("image_urls", [])
        mask_data_raw = data.get("mask_data")      # base64 data URI of mask PNG
        feather = data.get("feather", 0)            # mask edge blur radius

        has_mask = bool(mask_data_raw)
        input_max_edge = int(data.get("input_max_edge", 1536))
        log_event("CLIPROXY", "开始生成", icon="▶️", prompt=prompt, model=model, size=size, quality=quality, n=n, refs=len(image_urls), mask=has_mask, feather=feather, input_max_edge=input_max_edge)

        if model == "gemini-3.1-flash-image":
            return _generate_cliproxy_gemini_flash_image(
                prompt=prompt,
                ratio=size or "1:1",
                resolution=data.get("resolution"),
                quality=quality,
                n=n,
                image_urls=image_urls,
                mask_data_raw=mask_data_raw,
                feather=feather,
                job_data=data,
            )

        size = _resolve_cliproxy_openai_size(size, data.get("resolution"))

        # Ensure save directory exists
        os.makedirs(config.CLIPROXY_SAVE_DIR, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        results = []

        # ── Build a function that sends ONE request and returns raw JSON ──
        def _send_one_cliproxy(idx):
            """Send a single CLIProxy generation request (n=1) and return parsed JSON or None."""
            try:
                if image_urls and files_for_reuse:
                    # images/edits with reference images
                    one_form = dict(form_data_for_reuse)  # shallow copy
                    one_form["n"] = "1"
                    resp = requests.post(
                        f"{config.CLIPROXY_BASE_URL}/images/edits",
                        files=files_for_reuse,  # same ref images for all
                        data=one_form,
                        headers={"Authorization": f"Bearer {config.CLIPROXY_API_KEY}"},
                        timeout=1200
                    )
                else:
                    # images/generations (no reference images)
                    resp = requests.post(
                        f"{config.CLIPROXY_BASE_URL}/images/generations",
                        json={
                            "prompt": prompt,
                            "model": model,
                            "n": 1,
                            "size": size,
                            "quality": quality,
                            "moderation": "low",
                            "output_format": "png",
                            "response_format": "b64_json",
                        },
                        headers={"Authorization": f"Bearer {config.CLIPROXY_API_KEY}"},
                        timeout=1200
                    )
                if resp.status_code != 200:
                    log_event("CLIPROXY", "请求失败", "ERROR", item=f"{idx+1}/{n}", status=resp.status_code, body=resp.text[:200])
                    return None
                return resp.json()
            except Exception as e:
                log_event("CLIPROXY", "请求异常", "ERROR", item=f"{idx+1}/{n}", error=e)
                return None

        # Handle reference images (build once, reuse for all concurrent requests)
        files_for_reuse = []
        form_data_for_reuse = {}

        if image_urls:
            form_data_for_reuse = {
                "prompt": prompt,
                "model": model,
                "n": "1",
                "size": size,
                "quality": quality,
                "moderation": "low",
                "output_format": "png",
                "response_format": "b64_json",
            }

            for idx, img_info in enumerate(image_urls):
                url = img_info.get("url", "") if isinstance(img_info, dict) else img_info
                if not url:
                    continue

                if url.startswith("data:"):
                    try:
                        header, b64_data = url.split(",", 1)
                        image_data = base64.b64decode(b64_data)
                        if has_mask:
                            image_data = _resize_image_data(image_data, input_max_edge)
                        files_for_reuse.append(("image", (f"ref_{idx}.png", image_data, "image/png")))
                    except Exception as e:
                        log_event("CLIPROXY", "参考图解析失败", "WARN", index=idx, error=e)
                else:
                    try:
                        log_event("CLIPROXY", "准备参考图", "DEBUG", index=idx, url=url[:80])
                        image = load_reference_image(url, timeout=30, proxies=config.HTTP_PROXIES)
                        image_data = image.data
                        content_type = image.content_type
                        suffix = image.suffix
                        if has_mask:
                            image_data = _resize_image_data(image_data, input_max_edge)
                            content_type = "image/png"
                            suffix = ".png"
                        files_for_reuse.append(("image", (f"ref_{idx}{suffix}", image_data, content_type)))
                    except Exception as e:
                        log_event("CLIPROXY", "参考图下载失败", "WARN", index=idx, error=e)

            # If we have ref images but none could be processed, fall back to no-ref mode
            if not files_for_reuse:
                log_event("CLIPROXY", "参考图不可用，改用文生图", "WARN")

        # ── Handle mask data ──
        mask_bytes = None
        if has_mask and files_for_reuse:
            try:
                # Get the size of the first reference image to match mask dimensions
                first_img_data = files_for_reuse[0][1][1]  # (field, (name, data, mime))
                with Image.open(io.BytesIO(first_img_data)) as ref_img:
                    ref_size = ref_img.size
                mask_bytes = _decode_mask_data(mask_data_raw, feather=feather, target_size=ref_size)
                # Add mask to files
                files_for_reuse.append(("mask", ("mask.png", mask_bytes, "image/png")))
                log_event("CLIPROXY", "已附加遮罩", bytes=len(mask_bytes), feather=feather)
            except Exception as e:
                log_event("CLIPROXY", "遮罩处理失败", "WARN", error=e)
                import traceback
                traceback.print_exc()

        # ── Send N concurrent requests (like Nanobanana2) ──
        raw_results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(n, 6)) as executor:
            futures = [executor.submit(_send_one_cliproxy, i) for i in range(n)]
            for f in concurrent.futures.as_completed(futures):
                res = f.result()
                if res:
                    raw_results.append(res)
                    _update_legacy_provider_job_count_progress(data, len(raw_results), n)

        if raw_results:
            log_event("CLIPROXY", "生成完成", "OK", success=len(raw_results), requested=n)
        else:
            log_event("CLIPROXY", "生成失败", "ERROR", success=0, requested=n, base_url=config.CLIPROXY_BASE_URL)

        # ── Parse all responses and save images ──
        for ri, one_result in enumerate(raw_results):
            images = one_result.get("data", [])
            for idx, img_data in enumerate(images):
                b64 = img_data.get("b64_json")
                if b64:
                    try:
                        img_idx = len(results) + 1
                        filename = f"cliproxy_{timestamp}_{img_idx:02d}_{os.urandom(2).hex().upper()}.png"
                        filepath = os.path.join(config.CLIPROXY_SAVE_DIR, filename)
                        with open(filepath, "wb") as f:
                            f.write(base64.b64decode(b64))
                        with Image.open(filepath) as saved_img:
                            actual_width, actual_height = saved_img.size
                        log_event("CLIPROXY", "保存图片", "OK", icon="🖼️", file=filename)

                        results.append({
                            "saved_path": filepath,
                            "filename": filename,
                            "width": actual_width,
                            "height": actual_height,
                            "data_uri": f"data:image/png;base64,{b64[:100]}..."
                        })
                        _update_legacy_provider_job_progress(data, "cliproxy", results)
                    except Exception as e:
                        log_event("CLIPROXY", "保存图片失败", "WARN", error=e)
                        results.append({
                            "saved_path": None,
                            "filename": f"cliproxy_{timestamp}_{len(results)+1:02d}.png",
                            "data_uri": f"data:image/png;base64,{b64}"
                        })
                        _update_legacy_provider_job_progress(data, "cliproxy", results)

        if not results:
            return jsonify({"success": False, "error": {"message": "未获取到图片数据"}}), 500

        return jsonify({
            "success": True,
            "data": results,
            "count": len(results),
            "save_dir": config.CLIPROXY_SAVE_DIR
        })

    except requests.exceptions.Timeout:
        log_event("CLIPROXY", "请求超时", "ERROR")
        return jsonify({"success": False, "error": {"message": "CLIProxy 请求超时，请重试"}}), 408
    except Exception as e:
        log_event("CLIPROXY", "生成异常", "ERROR", error=e)
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


# ============ Sousaku.ai (Muset SDK adapter) ============

@app.route('/api/generate-sousaku', methods=['POST'])
def generate_image_sousaku():
    """Submit a Sousaku image generation task through the isolated provider adapter."""
    try:
        data = request.json or {}
        log_event(
            "SOUSAKU",
            "提交任务",
            icon="▷",
            prompt=data.get("prompt"),
            model=data.get("model"),
            ratio=data.get("size") or data.get("ratio"),
            n=data.get("n"),
            refs=len(data.get("image_urls") or []),
        )
        result = create_sousaku_task(data)
        if result.get("success"):
            meta = result.get("meta") or {}
            account = meta.get("account") or {}
            credit_left = meta.get("credit_after")
            if credit_left is None and meta.get("credit_before") is not None:
                credit_left = meta.get("credit_before") - (meta.get("estimated_credits") or 0)
            log_event(
                "SOUSAKU",
                "任务已提交",
                "OK",
                account=account.get("account") or account.get("nick_name") or account.get("user_id"),
                model=meta.get("model") or data.get("model"),
                n=data.get("n"),
                credit_used=meta.get("estimated_credits"),
                credit_left=credit_left,
                token=account.get("token_masked"),
            )
        else:
            log_event("SOUSAKU", "任务提交失败", "ERROR", error=result.get("error"))
        return jsonify(result)
    except Exception as e:
        log_event("SOUSAKU", "提交异常", "ERROR", error=e)
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


@app.route('/api/sousaku-task/<task_id>', methods=['GET'])
def get_sousaku_task_status(task_id):
    """Query Sousaku task status and return any partial images already completed."""
    try:
        result = get_sousaku_task(task_id)
        result = _apply_sousaku_timeout_policy(task_id, result)
        _log_sousaku_task_progress(task_id, result)
        return jsonify(result)
    except Exception as e:
        log_event("SOUSAKU", "查询异常", "ERROR", task=task_id[:20], error=e)
        return jsonify({"status": "failed", "error": {"message": str(e)}}), 500


# ============ Unified Job System ============

def _job_provider_from_request(data):
    provider = (
        data.get("provider")
        or data.get("apiType")
        or data.get("api_type")
        or data.get("channel")
        or ""
    )
    return str(provider).strip().lower()


def _normalize_job_images(data):
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    return normalize_reference_inputs(
        data.get("image_urls")
        or data.get("imageUrls")
        or data.get("input_images")
        or data.get("inputImages")
        or params.get("image_urls")
        or params.get("imageUrls")
        or params.get("input_images")
        or params.get("inputImages")
        or []
    )


@app.route('/api/jobs', methods=['POST'])
def create_job():
    """Create a background generation job. This returns immediately."""
    try:
        data = request.json or {}
        provider = _job_provider_from_request(data)
        if not provider:
            return jsonify({"success": False, "error": {"message": "provider is required"}}), 400
        if not provider_registry.is_enabled(provider):
            return jsonify({"success": False, "error": {"message": f"provider disabled or unknown: {provider}"}}), 400
        if provider not in _JOB_WORKER.adapters:
            configure_job_provider_adapters()
        if provider not in _JOB_WORKER.adapters:
            return jsonify({"success": False, "error": {"message": f"provider has no adapter: {provider}"}}), 400

        prompt = data.get("prompt") or ""
        if not prompt:
            return jsonify({"success": False, "error": {"message": "prompt is required"}}), 400

        params = dict(data)
        params.pop("provider", None)
        params.pop("apiType", None)
        params.pop("api_type", None)
        params.pop("channel", None)
        max_attempts = int(data.get("max_attempts") or 1)
        job = _JOB_STORE.create_job(
            provider=provider,
            prompt=prompt,
            params=params,
            input_images=_normalize_job_images(data),
            max_attempts=max_attempts,
        )
        log_event("JOB", "任务入队", "OK", job=job["id"][:12], provider=provider, prompt=prompt)
        return jsonify({"success": True, "job_id": job["id"], "data": job}), 202
    except Exception as e:
        log_event("JOB", "任务入队失败", "ERROR", error=e)
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


@app.route('/api/jobs', methods=['GET'])
def list_jobs():
    try:
        status = request.args.get("status") or None
        active = request.args.get("active") in {"1", "true", "yes"}
        limit = int(request.args.get("limit") or 100)
        jobs = _JOB_STORE.list_jobs(status=status, active=active, limit=limit)
        return jsonify({"success": True, "data": jobs})
    except Exception as e:
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


@app.route('/api/jobs', methods=['DELETE'])
def delete_jobs():
    try:
        data = request.get_json(silent=True) or {}
        include_active = bool(data.get("include_active", True))
        deleted = _JOB_STORE.delete_jobs(include_active=include_active)
        log_event("JOB", "任务记录已清空", "WARN", count=deleted, include_active=include_active)
        return jsonify({"success": True, "data": {"deleted": deleted}})
    except Exception as e:
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


@app.route('/api/jobs/<job_id>', methods=['GET'])
def get_job(job_id):
    job = _JOB_STORE.get_job(job_id)
    if not job:
        return jsonify({"success": False, "error": {"message": "job not found"}}), 404
    return jsonify({"success": True, "data": job})


@app.route('/api/jobs/<job_id>', methods=['DELETE'])
def delete_job(job_id):
    job = _JOB_STORE.delete_job(job_id)
    if not job:
        return jsonify({"success": False, "error": {"message": "job not found"}}), 404
    log_event("JOB", "任务记录已删除", "WARN", job=job_id[:12], provider=job.get("provider"))
    return jsonify({"success": True, "data": job})


@app.route('/api/jobs/<job_id>/cancel', methods=['POST'])
def cancel_job(job_id):
    job = _JOB_STORE.cancel_job(job_id)
    if not job:
        return jsonify({"success": False, "error": {"message": "job not found"}}), 404
    return jsonify({"success": True, "data": job})


@app.route('/api/jobs/<job_id>/retry', methods=['POST'])
def retry_job(job_id):
    job = _JOB_STORE.retry_job(job_id)
    if not job:
        return jsonify({"success": False, "error": {"message": "job not found"}}), 404
    return jsonify({"success": True, "data": job})


@app.route('/api/provider-accounts', methods=['GET'])
def list_provider_accounts():
    """Return provider accounts in a common shape. First provider: Sousaku."""
    try:
        provider = (request.args.get("provider") or "sousaku").strip().lower()
        if provider != "sousaku":
            return jsonify({"success": True, "provider": provider, "data": []})

        if request.args.get("refresh") in {"1", "true", "yes"}:
            records = refresh_sousaku_account_records()
            log_event("SOUSAKU", "账号池已手动刷新", "OK", count=len(records))

        config_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
        accounts_path = os.path.join(config_dir, "sousaku_accounts.json")
        if not os.path.exists(accounts_path):
            return jsonify({"success": True, "provider": provider, "data": []})

        low_credit_threshold = _read_sousaku_low_credit_threshold(default=5)
        with open(accounts_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        accounts = []
        config_payload = _read_sousaku_config_payload()
        disabled_tokens = set(_normalize_sousaku_tokens(config_payload.get("disabled_tokens") or []))
        for index, account in enumerate(payload.get("accounts") or []):
            label = (
                account.get("user_email")
                or account.get("nick_name")
                or account.get("user_name")
                or account.get("user_id")
                or account.get("token_masked")
                or f"Sousaku #{index + 1}"
            )
            total_credit = account.get("total_credit")
            upstream_running_count = int(account.get("running_task_count") or 0)
            error = account.get("error")
            token = str(account.get("token") or "")
            runtime_overlay = ACCOUNT_POOL.overlay_for_token(token) if token else {}
            local_running_count = int(runtime_overlay.get("local_running_jobs") or 0)
            running_count = upstream_running_count + local_running_count
            if token and token in disabled_tokens:
                status = "disabled"
            elif local_running_count > 0:
                status = "busy"
            elif runtime_overlay.get("cooldown_until"):
                status = "invalid"
            elif error:
                status = "invalid"
            elif upstream_running_count > 0:
                status = "busy"
            elif total_credit is not None and float(total_credit) <= low_credit_threshold:
                status = "low_quota"
            else:
                status = "available"

            accounts.append({
                "id": account.get("user_id") or account.get("token_masked") or str(index),
                "provider": "sousaku",
                "label": label,
                "status": status,
                "quota": {
                    "total": total_credit,
                    "remaining": total_credit,
                    "unit": "credits",
                },
                "running_jobs": running_count,
                "last_used_at": account.get("updated_at"),
                "tags": [tag for tag in [account.get("package_level")] if tag],
                "metadata": {
                    "user_id": account.get("user_id"),
                    "user_name": account.get("user_name"),
                    "nick_name": account.get("nick_name"),
                    "user_email": account.get("user_email"),
                    "share_code": account.get("share_code"),
                    "inviter_share_code": account.get("inviter_share_code"),
                    "package_level": account.get("package_level"),
                    "token_masked": account.get("token_masked"),
                    "disabled": status == "disabled",
                    "local_running_jobs": local_running_count,
                    "local_job_ids": runtime_overlay.get("local_job_ids") or [],
                    "upstream_running_jobs": upstream_running_count,
                    "cooldown_until": runtime_overlay.get("cooldown_until"),
                    "cooldown_error": runtime_overlay.get("cooldown_error"),
                    "subscription_credit": account.get("subscription_credit"),
                    "permanent_credit": account.get("permanent_credit"),
                },
            })
        return jsonify({
            "success": True,
            "provider": provider,
            "count": len(accounts),
            "updated_at": payload.get("updated_at"),
            "low_credit_threshold": low_credit_threshold,
            "data": accounts,
        })
    except Exception as e:
        log_event("JOB", "账号池读取失败", "ERROR", error=e)
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


@app.route('/api/provider-accounts/sousaku/tokens', methods=['POST'])
def add_sousaku_tokens():
    """Append tokens to sousaku_config.json, then refresh only newly added account records."""
    try:
        data = request.json or {}
        raw_tokens = data.get("tokens") or data.get("token") or ""
        incoming = _normalize_sousaku_tokens(raw_tokens)
        if not incoming:
            return jsonify({"success": False, "error": {"message": "token is required"}}), 400

        config_payload = _read_sousaku_config_payload()
        current = _normalize_sousaku_tokens(config_payload.get("tokens") or config_payload.get("token") or [])
        seen = set(current)
        added = []
        skipped = []
        for token in incoming:
            if token in seen:
                skipped.append(_mask_token(token))
                continue
            current.append(token)
            seen.add(token)
            added.append(token)

        config_payload["tokens"] = current
        config_payload.pop("token", None)
        with open(config.SOUSAKU_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config_payload, f, ensure_ascii=False, indent=2)

        records = refresh_sousaku_account_records_for_tokens(added)
        log_event(
            "SOUSAKU",
            "账号 Token 已导入",
            "OK",
            added=len(added),
            skipped=len(skipped),
            total=len(current),
            tokens=[_mask_token(token) for token in added],
        )
        return jsonify({
            "success": True,
            "added": len(added),
            "skipped": len(skipped),
            "total": len(current),
            "provider": "sousaku",
            "count": len(records),
            "refreshed": len(records),
        })
    except Exception as e:
        log_event("SOUSAKU", "账号 Token 导入失败", "ERROR", error=e)
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


@app.route('/api/provider-accounts/sousaku/<path:account_id>/refresh', methods=['POST'])
def refresh_sousaku_account(account_id):
    """Refresh one cached Sousaku account record."""
    try:
        token = _find_sousaku_account_token(account_id)
        if not token:
            return jsonify({"success": False, "error": {"message": "account token not found"}}), 404

        records = refresh_sousaku_account_records_for_tokens([token])
        record = records[0] if records else None
        log_event(
            "SOUSAKU",
            "账号已单独刷新",
            "OK",
            account=account_id[:20],
            token=_mask_token(token),
            status="invalid" if record and record.get("error") else "updated",
        )
        return jsonify({
            "success": True,
            "provider": "sousaku",
            "account_id": account_id,
            "token_masked": _mask_token(token),
            "data": record,
        })
    except Exception as e:
        log_event("SOUSAKU", "账号单独刷新失败", "ERROR", account=account_id[:20], error=e)
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


@app.route('/api/provider-accounts/sousaku/<path:account_id>', methods=['PATCH'])
def update_sousaku_account(account_id):
    """Enable or disable a Sousaku token without deleting it from config."""
    try:
        data = request.json or {}
        disabled = bool(data.get("disabled"))
        token = _find_sousaku_account_token(account_id)
        if not token:
            return jsonify({"success": False, "error": {"message": "account token not found"}}), 404

        config_payload = _read_sousaku_config_payload()
        disabled_tokens = _normalize_sousaku_tokens(config_payload.get("disabled_tokens") or [])
        disabled_set = set(disabled_tokens)
        if disabled:
            if token not in disabled_set:
                disabled_tokens.append(token)
        else:
            disabled_tokens = [item for item in disabled_tokens if item != token]
        config_payload["disabled_tokens"] = disabled_tokens
        _write_sousaku_config_payload(config_payload)

        log_event(
            "SOUSAKU",
            "账号状态已更新",
            "OK",
            account=account_id[:20],
            disabled=disabled,
            token=_mask_token(token),
        )
        return jsonify({
            "success": True,
            "provider": "sousaku",
            "account_id": account_id,
            "disabled": disabled,
            "token_masked": _mask_token(token),
        })
    except Exception as e:
        log_event("SOUSAKU", "账号状态更新失败", "ERROR", account=account_id[:20], error=e)
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


@app.route('/api/provider-accounts/sousaku/<path:account_id>', methods=['DELETE'])
def delete_sousaku_account(account_id):
    """Remove a Sousaku token from config and drop its cached account record."""
    try:
        token = _find_sousaku_account_token(account_id)
        if not token:
            return jsonify({"success": False, "error": {"message": "account token not found"}}), 404

        config_payload = _read_sousaku_config_payload()
        tokens = _normalize_sousaku_tokens(config_payload.get("tokens") or config_payload.get("token") or [])
        disabled_tokens = _normalize_sousaku_tokens(config_payload.get("disabled_tokens") or [])
        config_payload["tokens"] = [item for item in tokens if item != token]
        config_payload["disabled_tokens"] = [item for item in disabled_tokens if item != token]
        config_payload.pop("token", None)
        _write_sousaku_config_payload(config_payload)
        _remove_sousaku_account_record(account_id, token)

        log_event("SOUSAKU", "账号已删除", "OK", account=account_id[:20], token=_mask_token(token))
        return jsonify({
            "success": True,
            "provider": "sousaku",
            "account_id": account_id,
            "token_masked": _mask_token(token),
        })
    except Exception as e:
        log_event("SOUSAKU", "账号删除失败", "ERROR", account=account_id[:20], error=e)
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


def _read_sousaku_config_payload() -> dict:
    try:
        with open(config.SOUSAKU_CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else {}
    except FileNotFoundError:
        return {}


def _write_sousaku_config_payload(payload: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(config.SOUSAKU_CONFIG_PATH)), exist_ok=True)
    with open(config.SOUSAKU_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _sousaku_accounts_path() -> str:
    config_dir = os.path.dirname(os.path.abspath(config.SOUSAKU_CONFIG_PATH))
    config_payload = _read_sousaku_config_payload()
    accounts_path = config_payload.get("accounts_path") or "sousaku_accounts.json"
    return accounts_path if os.path.isabs(accounts_path) else os.path.join(config_dir, accounts_path)


def _find_sousaku_account_token(account_id: str) -> str:
    account_id = str(account_id or "")
    accounts_path = _sousaku_accounts_path()
    accounts = []
    if os.path.exists(accounts_path):
        try:
            with open(accounts_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            accounts = payload.get("accounts") or []
        except Exception:
            accounts = []

    for account in accounts:
        if not isinstance(account, dict):
            continue
        token = str(account.get("token") or "")
        matches = {
            str(account.get("user_id") or ""),
            str(account.get("token_masked") or ""),
            token,
            _mask_token(token) if token else "",
        }
        if account_id in matches:
            return token

    config_payload = _read_sousaku_config_payload()
    for token in _normalize_sousaku_tokens(config_payload.get("tokens") or config_payload.get("token") or []):
        if account_id in {token, _mask_token(token)}:
            return token
    for token in _normalize_sousaku_tokens(config_payload.get("disabled_tokens") or []):
        if account_id in {token, _mask_token(token)}:
            return token
    return ""


def _remove_sousaku_account_record(account_id: str, token: str) -> None:
    accounts_path = _sousaku_accounts_path()
    if not os.path.exists(accounts_path):
        return
    try:
        with open(accounts_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        accounts = payload.get("accounts") or []
        next_accounts = []
        for account in accounts:
            if not isinstance(account, dict):
                continue
            account_token = str(account.get("token") or "")
            if account_token == token or str(account.get("user_id") or "") == account_id:
                continue
            next_accounts.append(account)
        payload["accounts"] = next_accounts
        payload["count"] = len(next_accounts)
        with open(accounts_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        log_event("SOUSAKU", "账号缓存删除失败", "WARN", account=account_id[:20], error=exc)


def _read_sousaku_low_credit_threshold(default: int = 5) -> int:
    try:
        payload = _read_sousaku_config_payload()
        return int(payload.get("min_credit_threshold", default))
    except Exception:
        return default


def _normalize_sousaku_tokens(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        pieces = value.replace("\r", "\n").replace(";", "\n").replace(",", "\n").split("\n")
        return [piece.strip() for piece in pieces if piece.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _mask_token(token: str) -> str:
    token = str(token or "")
    if len(token) <= 12:
        return "*" * len(token)
    return f"{token[:6]}...{token[-6:]}"


@app.route('/api/save-thought-image', methods=['POST'])
def save_thought_image():
    """Save a thought/draft image from base64 data URI to local storage"""
    try:
        data = request.json
        data_uri = data.get("data_uri", "")
        
        if not data_uri or not data_uri.startswith("data:"):
            return jsonify({"success": False, "error": {"message": "无效的图片数据"}}), 400
        
        # Parse data URI: data:image/png;base64,xxxxx
        header, b64_data = data_uri.split(",", 1)
        mime_type = header.split(";")[0].split(":")[1] if ":" in header else "image/png"
        
        ext = "png"
        if "jpeg" in mime_type or "jpg" in mime_type:
            ext = "jpg"
        elif "webp" in mime_type:
            ext = "webp"
        
        os.makedirs(config.NANOBANANA2_SAVE_DIR, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"thought_{timestamp}_{os.urandom(4).hex().upper()}.{ext}"
        filepath = os.path.join(config.NANOBANANA2_SAVE_DIR, filename)
        
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(b64_data))
        
        log_event("THOUGHT", "保存草图", "OK", icon="🖼️", file=filename)
        return jsonify({
            "success": True,
            "saved_path": filepath,
            "filename": filename
        })
    except Exception as e:
        log_event("THOUGHT", "保存草图失败", "ERROR", error=e)
        return jsonify({"success": False, "error": {"message": str(e)}}), 500


@app.route('/api/task/<task_id>', methods=['GET'])
def get_task_status(task_id):
    """Query task status and results with retry. Auto-downloads images when completed."""
    max_retries = 3
    last_error = None
    
    for attempt in range(max_retries):
        try:
            response = requests.get(
                f"{config.API_BASE_URL}/v1/tasks/{task_id}",
                headers=get_headers(),
                params={"language": "zh"},
                timeout=30
            )
            result = response.json()
            
            # Get task status
            data = result.get('data', {})
            status = data.get('status', 'unknown')
            status_l = status.lower()
            if status_l in ('completed', 'success', 'succeeded', 'failed', 'error'):
                log_event("TASK", "任务状态", "OK" if status_l in ('completed', 'success', 'succeeded') else "ERROR", task=task_id[:20], status=status)
            
            # Auto-download images when task completes
            if status_l in ('completed', 'success', 'succeeded'):
                task_result = data.get('result')
                normalized_images = []
                
                if isinstance(task_result, dict):
                    images_field = task_result.get('images', [])
                    if isinstance(images_field, list):
                        for img in images_field:
                            if isinstance(img, dict):
                                normalized_images.append(dict(img))
                            elif isinstance(img, str):
                                normalized_images.append({'url': img})
                    elif isinstance(images_field, str):
                        normalized_images.append({'url': images_field})
                        
                    # If still empty, check flat url fields
                    if not normalized_images:
                        img_url = task_result.get('image_url') or task_result.get('url')
                        if isinstance(img_url, str):
                            normalized_images.append({'url': img_url})
                elif isinstance(task_result, str):
                    normalized_images.append({'url': task_result})
                elif isinstance(task_result, list):
                    for img in task_result:
                        if isinstance(img, dict):
                            normalized_images.append(dict(img))
                        elif isinstance(img, str):
                            normalized_images.append({'url': img})
                
                # Download images to local storage
                for img in normalized_images:
                    url = img.get('url')
                    if url and not img.get('saved_path'):
                        saved_path = download_apimart_image(url)
                        if saved_path:
                            img['saved_path'] = saved_path
                        else:
                            # 下载失败但图片确实生成了！不要抛异常丢失整个结果
                            # 把远程 URL 返回给前端，让用户至少能看到和手动保存
                            log_event("TASK", "下载失败，返回远程URL", "WARN", url=url[:100])
                            img['download_failed'] = True
                
                # Reconstruct result to match frontend expectations
                if not isinstance(task_result, dict):
                    data['result'] = {}
                data['result']['images'] = normalized_images
            
            return jsonify(result)
        except Exception as e:
            last_error = e
            log_event("TASK", "查询重试失败", "WARN", attempt=f"{attempt+1}/{max_retries}", error=e)
            if attempt < max_retries - 1:
                time.sleep(1)  # Wait 1 second before retry
    
    log_event("TASK", "查询失败", "ERROR", attempts=max_retries, error=last_error)
    return jsonify({"error": {"message": str(last_error)}}), 500


def download_apimart_image(url):
    """Download image from URL or base64 and save to local storage. Returns saved path or None."""
    try:
        # Handle URL being a list (APIMart sometimes returns ['url'] instead of 'url')
        if isinstance(url, list):
            url = url[0] if url else None
        if not url or not isinstance(url, str):
            log_event("APIMART", "图片URL无效", "WARN")
            return None
            
        # Ensure save directory exists
        os.makedirs(config.OPENAI_SAVE_DIR, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        
        # Check if URL is actually a base64 string
        is_base64 = False
        img_data = None
        ext = 'jpg'
        
        if url.startswith('data:image'):
            is_base64 = True
            header, base64_str = url.split(',', 1)
            if 'png' in header:
                ext = 'png'
            elif 'webp' in header:
                ext = 'webp'
            img_data = base64.b64decode(base64_str)
        elif not url.startswith('http://') and not url.startswith('https://') and len(url) > 100:
            is_base64 = True
            # Fix padding if missing
            pad = len(url) % 4
            if pad:
                url += '=' * (4 - pad)
            img_data = base64.b64decode(url)
            
        if is_base64:
            filename = f"apimart_{timestamp}_{os.urandom(4).hex().upper()}.{ext}"
            filepath = os.path.join(config.OPENAI_SAVE_DIR, filename)
            with open(filepath, 'wb') as f:
                f.write(img_data)
            log_event("APIMART", "保存base64图片", "OK", icon="🖼️", file=filename)
            return filepath
        
        log_event("APIMART", "下载图片", "DEBUG", url=url[:100])
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.get(url, timeout=60)
                if response.status_code == 200:
                    break
                log_event("APIMART", "下载重试失败", "WARN", attempt=f"{attempt+1}/{max_retries}", status=response.status_code)
            except Exception as e:
                log_event("APIMART", "下载重试失败", "WARN", attempt=f"{attempt+1}/{max_retries}", error=e)
            if attempt < max_retries - 1:
                time.sleep(2)
        else:
            log_event("APIMART", "下载失败", "ERROR", attempts=max_retries)
            return None
        
        # Determine extension from content-type or URL
        content_type = response.headers.get('content-type', '')
        if 'png' in content_type or url.endswith('.png'):
            ext = 'png'
        elif 'webp' in content_type or url.endswith('.webp'):
            ext = 'webp'
        else:
            ext = 'jpg'
        
        filename = f"apimart_{timestamp}_{os.urandom(4).hex().upper()}.{ext}"
        filepath = os.path.join(config.OPENAI_SAVE_DIR, filename)
        
        with open(filepath, 'wb') as f:
            f.write(response.content)
        
        log_event("APIMART", "保存图片", "OK", icon="🖼️", file=filename)
        return filepath
    except Exception as e:
        log_event("APIMART", "下载异常", "WARN", error=e)
        return None


@app.route('/api/upload-image', methods=['POST'])
def upload_image():
    """Upload image to Telegraph image hosting service with auto compression"""
    try:
        if 'file' not in request.files:
            return jsonify({"success": False, "message": "No file provided"}), 400
        
        file = request.files['file']
        
        # Read file content into memory first
        file_content = file.read()
        original_size = len(file_content)
        file_name = file.filename or "image.png"
        content_type = file.content_type or "image/png"
        
        # Only compress with Pillow if > 10MB, otherwise upload original directly
        # This avoids the 1-3s Pillow decode/re-encode overhead for normal-sized images
        if original_size > MAX_FILE_SIZE:
            try:
                img = Image.open(io.BytesIO(file_content))
                
                # Convert to RGB if necessary
                if img.mode in ('RGBA', 'LA', 'P'):
                    if img.mode != 'RGBA':
                        img = img.convert('RGBA')
                elif img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # Start with 95% quality and reduce until under 10MB
                quality = 95
                while quality >= 50:
                    buffer = io.BytesIO()
                    img.save(buffer, format='WEBP', quality=quality)
                    file_content = buffer.getvalue()
                    
                    if len(file_content) <= MAX_FILE_SIZE:
                        break
                    quality -= 5
                
                # Update file info
                file_name = file_name.rsplit('.', 1)[0] + '.webp'
                content_type = 'image/webp'
                
                compressed_size = len(file_content)
                log_event("UPLOAD", "图片压缩", original=f"{original_size/1024/1024:.2f}MB", final=f"{compressed_size/1024/1024:.2f}MB", quality=quality)
            except Exception as e:
                log_event("UPLOAD", "压缩失败，上传原图", "WARN", error=e)
        
        result = upload_public_image(
            file_content,
            file_name=file_name,
            content_type=content_type,
            timeout=60,
        )
        log_event("UPLOAD", "图床返回", "OK", url=result.url[:100], final=f"{result.size/1024/1024:.2f}MB")
        return jsonify({
            "success": True,
            "url": result.url,
            "compressed": original_size > MAX_FILE_SIZE or result.compressed,
            "original_size": original_size,
            "final_size": result.size,
        })
            
    except requests.exceptions.Timeout:
        return jsonify({"success": False, "message": "上传超时，请重新上传"}), 408
    except Exception as e:
        return jsonify({"success": False, "message": f"上传失败: {str(e)}"}), 500


@app.route('/api/proxy-image', methods=['GET'])
def proxy_image():
    """Proxy image download to avoid CORS issues"""
    try:
        image_url = request.args.get('url')
        if not image_url:
            return jsonify({"error": "No URL provided"}), 400
        
        response = requests.get(image_url, stream=True)
        
        from flask import Response
        return Response(
            response.content,
            content_type=response.headers.get('Content-Type', 'image/png'),
            headers={
                'Content-Disposition': 'attachment; filename="generated_image.png"'
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/process-url', methods=['POST'])
def process_url():
    """Process external image URL: check size, compress if >10MB, upload to hosting"""
    try:
        data = request.json
        url = data.get('url', '').strip()
        
        if not url:
            return jsonify({"success": False, "message": "No URL provided"}), 400
        
        # Step 1: Try HEAD request to get Content-Length
        file_size = None
        try:
            head_response = requests.head(url, allow_redirects=True, timeout=10)
            content_length = head_response.headers.get('Content-Length')
            if content_length:
                file_size = int(content_length)
                log_event("URL", "HEAD成功", size=f"{file_size / 1024 / 1024:.2f}MB", url=url[:100])
        except Exception as e:
            log_event("URL", "HEAD失败，下载检测", "WARN", error=e)
        
        # Step 2: If size is known and <= 10MB, return original URL
        if file_size is not None and file_size <= MAX_FILE_SIZE:
            return jsonify({
                "success": True,
                "url": url,
                "processed": False,
                "size": file_size
            })
        
        # Step 3: Need to download (either unknown size or >10MB)
        log_event("URL", "下载图片", url=url[:100])
        img_response = requests.get(url, timeout=60)
        img_response.raise_for_status()
        file_content = img_response.content
        actual_size = len(file_content)
        log_event("URL", "下载完成", size=f"{actual_size / 1024 / 1024:.2f}MB")
        
        # If actually <= 10MB, return original URL
        if actual_size <= MAX_FILE_SIZE:
            return jsonify({
                "success": True,
                "url": url,
                "processed": False,
                "size": actual_size
            })
        
        # Step 4: Compress and upload to image hosting
        log_event("URL", "超过10MB，开始压缩", size=f"{actual_size / 1024 / 1024:.2f}MB")
        img = Image.open(io.BytesIO(file_content))
        
        # Convert to RGB if needed
        if img.mode in ('RGBA', 'LA', 'P', 'L'):
            if img.mode != 'RGB':
                img = img.convert('RGB')
        
        # Resize to reduce file size if image is very large
        max_dimension = 4096
        if img.width > max_dimension or img.height > max_dimension:
            ratio = min(max_dimension / img.width, max_dimension / img.height)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            log_event("URL", "图片缩放", target=f"{new_size[0]}x{new_size[1]}")
        
        # Compress to JPEG with progressive encoding
        quality = 90
        while quality >= 50:
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=quality, progressive=True, subsampling=0)
            buffer.seek(0)  # Reset buffer position
            compressed_content = buffer.read()
            
            if len(compressed_content) <= MAX_FILE_SIZE:
                break
            quality -= 10
        
        compressed_size = len(compressed_content)
        log_event("URL", "压缩JPEG", original=f"{actual_size / 1024 / 1024:.2f}MB", final=f"{compressed_size / 1024 / 1024:.2f}MB", quality=quality)
        
        # Upload to Telegraph
        upload_response = requests.post(
            f"{TELEGRAPH_URL}/upload",
            files={"file": ("image.jpg", compressed_content, "image/jpeg")},
            timeout=60
        )
        
        log_event("URL", "图床返回", "OK" if upload_response.status_code == 200 else "WARN", status=upload_response.status_code, body=upload_response.text[:200])
        
        if upload_response.status_code == 200:
            result = upload_response.json()
            if isinstance(result, list) and len(result) > 0 and "src" in result[0]:
                new_url = TELEGRAPH_URL + result[0]["src"]
                return jsonify({
                    "success": True,
                    "url": new_url,
                    "processed": True,
                    "original_size": actual_size,
                    "compressed_size": compressed_size
                })
        
        return jsonify({
            "success": False,
            "message": "图床上传失败，请尝试其他图片"
        }), 500
        
    except requests.exceptions.Timeout:
        return jsonify({"success": False, "message": "下载/上传超时"}), 408
    except Exception as e:
        log_event("URL", "处理失败", "ERROR", error=e)
        return jsonify({"success": False, "message": str(e)}), 500


def find_free_port(start_port=config.SERVER_PORT, max_try=100):
    """从 start_port 开始，找到第一个可用端口"""
    import socket
    for port in range(start_port, start_port + max_try):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('0.0.0.0', port))  # 和 Flask 默认绑定地址一致
                return port
        except OSError:
            continue
    return start_port  # fallback


def configure_job_provider_adapters():
    """Register generation providers for the unified Job System."""
    config.apply_runtime_config()
    _JOB_WORKER.adapters = provider_registry.build_job_adapters(
        app=app,
        endpoints={
            "cliproxy": generate_image_cliproxy,
            "nanobanana2": generate_image_nanobanana2,
            "apimart_submit": generate_image,
            "apimart_status": get_task_status,
            "openai_submit": generate_image_openai_tasks,
            "openai_status": get_openai_tasks,
        },
    )
    _JOB_WORKER.provider_limits = config.JOB_PROVIDER_LIMITS
    _JOB_WORKER.max_workers = max(1, int(config.JOB_WORKER_MAX_WORKERS or 1))


def refresh_sousaku_accounts_async(reason: str) -> None:
    def _run() -> None:
        try:
            records = refresh_sousaku_account_records()
            log_event("SOUSAKU", "账号池已刷新", "OK", reason=reason, count=len(records))
        except Exception as exc:
            log_event("SOUSAKU", "账号池刷新失败", "WARN", reason=reason, error=exc)

    threading.Thread(target=_run, name=f"sousaku-account-refresh-{reason}", daemon=True).start()


configure_job_provider_adapters()
provider_registry.add_change_listener(configure_job_provider_adapters)


if __name__ == '__main__':
    import json as _json
    

    port = find_free_port(config.SERVER_PORT)
    is_reloader_child = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    should_run_startup_side_effects = (not config.BACKEND_USE_RELOADER) or is_reloader_child

    if should_run_startup_side_effects:
        # 把实际端口写入 server_port.json，供前端自动读取
        _port_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'server_port.json')
        with open(_port_file, 'w') as _f:
            _json.dump({"port": port, "frontend_port": config.FRONTEND_PORT}, _f)

        log_event("STARTUP", "后端启动", "OK", port=port, frontend_port=config.FRONTEND_PORT, port_file=_port_file, reload=config.BACKEND_USE_RELOADER)
        if port != config.SERVER_PORT:
            log_event("STARTUP", "端口被占用，已切换", "WARN", default=config.SERVER_PORT, actual=port)
        interrupted_jobs = _JOB_STORE.mark_interrupted_jobs()
        if interrupted_jobs:
            log_event("JOB", "已清理上次中断任务", "WARN", count=interrupted_jobs)
        if JOB_WORKER_ENABLED:
            _JOB_WORKER.start()
        else:
            log_event("JOB", "Job Worker 未启用", "WARN")
        refresh_sousaku_accounts_async("startup")
        log_event("STARTUP", "接口", method="GET", path="/api/balance", name="余额")
        log_event("STARTUP", "接口", method="POST", path="/api/jobs", name="后台任务入队")
        log_event("STARTUP", "接口", method="GET", path="/api/jobs", name="后台任务列表")
        log_event("STARTUP", "接口", method="DELETE", path="/api/jobs", name="清空后台任务记录")
        log_event("STARTUP", "接口", method="GET", path="/api/jobs/<id>", name="后台任务状态")
        log_event("STARTUP", "接口", method="POST", path="/api/generate", name="APIMart生成")
        log_event("STARTUP", "接口", method="POST", path="/api/generate-openai", name="ChatGPT2API生成(旧同步)")
        log_event("STARTUP", "接口", method="POST", path="/api/generate-openai-tasks", name="ChatGPT2API任务")
        log_event("STARTUP", "接口", method="GET", path="/api/openai-tasks", name="ChatGPT2API任务状态")
        log_event("STARTUP", "接口", method="POST", path="/api/generate-nanobanana2", name="Nanobanana2生成")
        log_event("STARTUP", "接口", method="POST", path="/api/generate-cliproxy", name="CLIProxy生成")
        log_event("STARTUP", "接口", method="GET", path="/api/task/<id>", name="任务状态")
        log_event("STARTUP", "接口", method="POST", path="/api/upload-image", name="上传图床")
        log_event("STARTUP", "接口", method="POST", path="/api/process-url", name="处理URL")
        log_event("STARTUP", "接口", method="GET", path="/api/serve-image", name="本地图片")
        log_event("STARTUP", "接口", method="GET", path="/api/thumbnail", name="画廊缩略图")
        log_event("STARTUP", "接口", method="POST", path="/api/open-folder", name="打开目录")
        log_event("STARTUP", "接口", method="POST", path="/api/gallery/import", name="导入画廊")
        log_event("STARTUP", "接口", method="GET", path="/api/capabilities", name="能力探测")
    app.run(debug=True, port=port, threaded=True, use_reloader=config.BACKEND_USE_RELOADER)

