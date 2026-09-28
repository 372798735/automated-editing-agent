"""AutoCut 图形操作台的本地后端（纯标准库 HTTP 服务）。

设计取舍：
    本机没有 Flask/FastAPI 等第三方 Web 框架，也不该为了一个本地小工具去装。
    因此用 `http.server` 手写一个只监听 127.0.0.1 的极简 API 服务：
    - 只绑定本机回环地址，不对外网暴露；
    - 所有写操作都限制在项目 workspace 与用户显式选择的素材目录内；
    - 长任务（跑流水线）放在后台线程执行，前端轮询进度，避免请求超时。

接口一览：
    GET  /                    操作台页面
    GET  /api/status          环境能力 + 工作区状态
    GET  /api/projects        发现项目（按来源类型）
    POST /api/pick-dir        弹出系统目录选择框（Windows），返回所选路径
    POST /api/run             启动批量剪辑任务，立即返回 task_id
    GET  /api/task/<id>       查询任务进度与阶段性结果
    POST /api/cancel/<id>     请求取消任务
    GET  /api/file?path=...   读取工作区内的产物文件（预览页/时间线/报告）
    POST /api/open            用系统程序打开某个产物路径或草稿目录
    GET  /api/drafts          列出已生成的草稿
"""

from __future__ import annotations

import json
import mimetypes
import os
import platform
import subprocess
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..capabilities import detect as detect_capabilities
from ..config import AppConfig
from ..errors import AutoCutError
from ..logging_setup import get_logger, setup_logging
from ..pipeline import Pipeline
from ..sources.base import SourceOptions

logger = get_logger("web")

STATIC_DIR = Path(__file__).resolve().parent / "static"
ALLOWED_SOURCE_KINDS = {"mock", "local", "capcut-web"}


# ---------------------------------------------------------------------------
# 任务模型
# ---------------------------------------------------------------------------

@dataclass
class TaskState:
    """一次批量剪辑任务的可观测状态，前端靠它渲染进度。"""

    id: str
    kind: str = "batch"
    status: str = "pending"          # pending / running / done / failed / cancelled
    source: str = "mock"
    path: str = ""
    months: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    template_id: str = ""
    music_id: str = ""

    total: int = 0
    finished: int = 0
    current_project: str = ""
    stage: str = ""

    results: list[dict[str, Any]] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    error: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0

    # 取消标志：worker 在项目之间检查它
    cancel_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        elapsed = 0.0
        if self.started_at:
            elapsed = (self.ended_at or time.time()) - self.started_at
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "source": self.source,
            "path": self.path,
            "months": self.months,
            "total": self.total,
            "finished": self.finished,
            "current_project": self.current_project,
            "stage": self.stage,
            "results": self.results,
            "messages": self.messages[-50:],
            "error": self.error,
            "elapsed_sec": round(elapsed, 1),
        }


TASKS: dict[str, TaskState] = {}
TASKS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 服务主体
# ---------------------------------------------------------------------------

class OperatorServer:
    """封装配置、任务表与业务动作，供 HTTP 层调用。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # 只读查询
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        report = detect_capabilities(draft_root=str(self.config.resolved_draft_root))
        workspace = self.config.workspace_path
        report_path = workspace / "batch_report.json"
        last_report = None
        if report_path.exists():
            try:
                last_report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                last_report = None
        drafts = self.list_drafts()
        return {
            "workspace": str(workspace),
            "draft_root": str(self.config.resolved_draft_root),
            "capabilities": [c.to_dict() for c in report.capabilities],
            "python_version": report.python_version,
            "last_report": last_report,
            "draft_count": len(drafts),
            "platform": platform.system(),
            "python": platform.python_version(),
        }

    def submit_login(self, wait_sec: int = 180) -> TaskState:
        """把扫码登录也做成任务，前端能统一显示进度。"""
        task = TaskState(id=uuid.uuid4().hex[:12], kind="login")
        with TASKS_LOCK:
            TASKS[task.id] = task
        threading.Thread(target=self._login_worker, args=(task, wait_sec), daemon=True).start()
        return task

    def _login_worker(self, task: TaskState, wait_sec: int) -> None:
        task.status = "running"
        task.started_at = time.time()
        task.total = 1
        task.messages.append("正在打开浏览器窗口并自动点击「登录」；出现二维码后用手机剪映 App 扫码，扫码完成前请勿关闭该窗口…")
        try:
            result = self.login_capcut(wait_sec=wait_sec)
            task.finished = 1
            task.status = "done"
            task.messages.append(f"登录成功，登录态已保存：{result['state_path']}")
        except AutoCutError as exc:
            task.status = "failed"
            task.error = str(exc)
            task.messages.append(str(exc))
        except Exception as exc:  # pragma: no cover - 兜底
            task.status = "failed"
            task.error = str(exc)
        finally:
            task.ended_at = time.time()

    def login_capcut(self, wait_sec: int = 180) -> dict[str, Any]:
        """弹出浏览器窗口让用户扫码登录剪映，保存登录态。需要 Playwright。"""
        try:
            from ..sources.capcut_web import DEFAULT_START_URL, CapCutSession
        except ImportError as exc:  # pragma: no cover
            raise AutoCutError(f"加载剪映模块失败：{exc}") from exc

        session = CapCutSession(
            state_path=self.config.workspace_path / ".auth" / "capcut_state.json",
            debug_dir=self.config.workspace_path / "debug",
            headed=True,
        )
        session.login(start_url=DEFAULT_START_URL, wait_sec=wait_sec)
        return {"state_path": str(session.state_path)}

    def submit_record(self, seconds: int = 90) -> TaskState:
        """云空间页面校准做成任务：打开浏览器让用户走一遍页面，录制产物存 debug/。"""
        task = TaskState(id=uuid.uuid4().hex[:12], kind="record")
        with TASKS_LOCK:
            TASKS[task.id] = task
        threading.Thread(target=self._record_worker, args=(task, seconds), daemon=True).start()
        return task

    def _record_worker(self, task: TaskState, seconds: int) -> None:
        task.status = "running"
        task.started_at = time.time()
        task.total = 1
        task.messages.append(
            f"正在打开浏览器（{seconds} 秒录制窗口）。请在窗口里自己找存放素材的页面（如「云空间/云盘」入口），"
            "点开某个月份和某个项目文件夹让页面充分加载；若网页版找不到云空间入口，停留在能看到素材/草稿列表的页面即可"
        )
        try:
            self.record_capcut(seconds=seconds)
            task.finished = 1
            task.status = "done"
            task.messages.append("校准录制完成，产物已保存到 workspace/debug/。请回到对话里告诉助手「校准完成」。")
        except AutoCutError as exc:
            task.status = "failed"
            task.error = str(exc)
            task.messages.append(str(exc))
        except Exception as exc:  # pragma: no cover - 兜底
            task.status = "failed"
            task.error = str(exc)
        finally:
            task.ended_at = time.time()

    def record_capcut(self, seconds: int = 90) -> dict[str, Any]:
        """打开浏览器录制云空间页面结构与网络请求，供选择器校准。需要 Playwright。"""
        try:
            from ..sources.capcut_web import CapCutSession
        except ImportError as exc:  # pragma: no cover
            raise AutoCutError(f"加载剪映模块失败：{exc}") from exc

        session = CapCutSession(
            state_path=self.config.workspace_path / ".auth" / "capcut_state.json",
            debug_dir=self.config.workspace_path / "debug",
            headed=True,
        )
        return session.record(seconds=seconds)

    def list_options(self) -> dict[str, Any]:
        """可选文案模板与背景音乐，供前端下拉框使用。"""
        templates: list[dict[str, str]] = []
        tpl_path = self.config.resolve_path(self.config.creative.template_file)
        if tpl_path.exists():
            try:
                payload = json.loads(tpl_path.read_text(encoding="utf-8"))
                for item in payload.get("templates", []) or []:
                    templates.append({
                        "id": str(item.get("id") or ""),
                        "name": str(item.get("name") or item.get("id") or ""),
                        "style": str(item.get("style") or ""),
                    })
            except (OSError, json.JSONDecodeError, AttributeError) as exc:
                logger.warning("读取文案模板失败: %s", exc)

        music: list[dict[str, Any]] = []
        music_path = self.config.resolve_path(self.config.creative.music_library_file)
        if music_path.exists():
            try:
                payload = json.loads(music_path.read_text(encoding="utf-8"))
                items = payload.get("music") if isinstance(payload, dict) else payload
                for item in items or []:
                    rel = str(item.get("path") or "")
                    exists = bool(rel) and self.config.resolve_path(rel).exists()
                    music.append({
                        "id": str(item.get("id") or ""),
                        "name": str(item.get("name") or ""),
                        "mood": str(item.get("mood") or ""),
                        "bpm": float(item.get("bpm") or 0),
                        "duration_sec": float(item.get("duration_sec") or 0),
                        "available": exists,
                    })
            except (OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError) as exc:
                logger.warning("读取音乐库失败: %s", exc)

        # 自定义模板目录（config/templates/*.json）与内置模板合并展示
        templates_dir = self.config.root / "config" / "templates"
        if templates_dir.is_dir():
            try:
                from ..creative.copywriter import load_templates
            except ImportError:  # pragma: no cover
                load_templates = None
            if load_templates is not None:
                seen = {t["id"] for t in templates}
                for file in sorted(templates_dir.glob("*.json")):
                    try:
                        for tpl in load_templates(file):
                            if tpl.id in seen:
                                continue
                            seen.add(tpl.id)
                            templates.append({"id": tpl.id, "name": tpl.name, "style": tpl.style})
                    except Exception as exc:  # 用户文件，容错跳过
                        logger.warning("忽略自定义模板 %s: %s", file.name, exc)

        # 音乐目录自动扫描：丢进 assets/music/ 的音频未登记也展示
        music_dir = self.config.root / "assets" / "music"
        if music_dir.is_dir():
            from ..creative.music import AUDIO_EXTS
            seen_ids = {m["id"] for m in music}
            for file in sorted(music_dir.iterdir()):
                if not file.is_file() or file.suffix.lower() not in AUDIO_EXTS:
                    continue
                if file.stem in seen_ids:
                    continue
                seen_ids.add(file.stem)
                music.append({
                    "id": file.stem,
                    "name": file.name,
                    "mood": "",
                    "bpm": 0,
                    "duration_sec": 0,
                    "available": True,
                })

        return {
            "templates": templates,
            "music": music,
            "templates_dir": str(templates_dir),
            "music_dir": str(music_dir),
        }

    def list_drafts(self) -> list[dict[str, Any]]:
        """已生成的草稿目录（含最近修改时间）。"""
        root = self.config.resolved_draft_root
        if not root.exists():
            return []
        items: list[dict[str, Any]] = []
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            content = child / "draft_content.json"
            if not content.exists():
                continue
            info: dict[str, Any] = {
                "name": child.name,
                "path": str(child),
                "modified": content.stat().st_mtime,
                "duration_sec": 0.0,
                "clip_count": 0,
            }
            try:
                payload = json.loads(content.read_text(encoding="utf-8"))
                info["duration_sec"] = round(float(payload.get("duration", 0)) / 1_000_000, 1)
                for track in payload.get("tracks", []):
                    if track.get("type") == "video":
                        info["clip_count"] = len(track.get("segments", []))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
            items.append(info)
        items.sort(key=lambda x: x["modified"], reverse=True)
        return items

    def build_source(self, kind: str, path: str = "", *, months: list[str] | None = None,
                     copy_materials: bool = False):
        """构造素材源。各源接收路径的方式不同，这里统一收口。"""
        if kind not in ALLOWED_SOURCE_KINDS:
            raise AutoCutError(f"不支持的素材来源：{kind}")
        options = SourceOptions(
            copy_materials=copy_materials,
            months=list(months or []),
            recursive_scan=True,
        )
        if kind == "mock":
            from ..sources.mock import MockSource
            return MockSource(options, materials_root=self.config.materials_dir)
        if kind == "local":
            from ..sources.local import LocalSource
            target = (path or "").strip() or str(self.config.materials_dir)
            if not Path(target).exists():
                raise AutoCutError(
                    f"素材目录不存在：{target}"
                    "（把素材放进去，按 月份/项目/视频 摆放；或在页面上点「选择…」换目录）"
                )
            return LocalSource(target, options, materials_root=self.config.materials_dir)
        from ..sources.capcut_web import CapCutWebSource
        return CapCutWebSource(
            state_path=self.config.workspace_path / ".auth" / "capcut_state.json",
            debug_dir=self.config.workspace_path / "debug",
            selectors_path=self.config.root / "config" / "capcut_selectors.json",
            materials_root=self.config.materials_dir,
            options=options,
        )

    def discover(self, kind: str, path: str = "", months: list[str] | None = None) -> list[dict[str, Any]]:
        source = self.build_source(kind, path, months=months)
        projects = source.discover()
        return [
            {
                "id": p.id,
                "name": p.name,
                "month": p.month,
                "material_count": p.material_count,
                "source_path": p.source_path,
                "status": p.status,
            }
            for p in projects
        ]

    # ------------------------------------------------------------------
    # 目录选择（Windows 系统对话框）
    # ------------------------------------------------------------------
    @staticmethod
    def pick_directory(initial: str = "") -> str:
        """弹出系统目录选择框。仅 Windows 支持；其它平台返回空串由前端手输。"""
        if platform.system() != "Windows":
            return ""
        script = (
            "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$d.Description = '选择素材根目录（内部按 月份/项目 两级组织）';"
            "$d.ShowNewFolderButton = $true;"
        )
        if initial and Path(initial).is_dir():
            script += f"$d.SelectedPath = '{initial}';"
        script += "if ($d.ShowDialog() -eq 'OK') { Write-Output $d.SelectedPath }"
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", script],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("目录选择框调用失败: %s", exc)
            return ""
        return (proc.stdout or "").strip()

    # ------------------------------------------------------------------
    # 文件访问（限制在工作区内）
    # ------------------------------------------------------------------
    def safe_path(self, raw: str) -> Path | None:
        """把前端传来的路径限制在「工作区」「草稿根目录」「项目根目录」内，防止越权读取。"""
        if not raw:
            return None
        candidate = Path(raw).expanduser()
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            return None
        roots = [self.config.workspace_path.resolve(), self.config.root.resolve()]
        draft_root = self.config.resolved_draft_root
        if draft_root.exists():
            roots.append(draft_root.resolve())
        for root in roots:
            if resolved == root or root in resolved.parents:
                return resolved
        return None

    @staticmethod
    def open_in_os(target: Path) -> str:
        """用系统默认程序打开文件或目录。"""
        if not target.exists():
            raise AutoCutError(f"路径不存在：{target}")
        system = platform.system()
        if system == "Windows":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.run(["open", str(target)], check=False)
        else:
            subprocess.run(["xdg-open", str(target)], check=False)
        return str(target)

    # ------------------------------------------------------------------
    # 任务执行
    # ------------------------------------------------------------------
    def submit(self, body: dict[str, Any]) -> TaskState:
        source_kind = str(body.get("source") or "mock")
        if source_kind not in ALLOWED_SOURCE_KINDS:
            raise AutoCutError(f"不支持的素材来源：{source_kind}")
        path = str(body.get("path") or "")
        months = [str(m) for m in (body.get("months") or []) if str(m).strip()]
        selected = [str(p) for p in (body.get("projects") or []) if str(p).strip()]
        template_id = str(body.get("template_id") or "")
        music_id = str(body.get("music_id") or "")

        if source_kind == "local":
            if not path:
                raise AutoCutError("本地素材模式必须指定素材目录")
            if not Path(path).is_dir():
                raise AutoCutError(f"素材目录不存在：{path}")

        task = TaskState(
            id=uuid.uuid4().hex[:12],
            source=source_kind,
            path=path,
            months=months,
            projects=selected,
            template_id=template_id,
            music_id=music_id,
        )
        with TASKS_LOCK:
            TASKS[task.id] = task
        threading.Thread(target=self._worker, args=(task,), daemon=True).start()
        return task

    def _worker(self, task: TaskState) -> None:
        """后台执行批量剪辑。每个项目跑完立刻回填结果，前端可以边跑边看。"""
        task.status = "running"
        task.started_at = time.time()
        try:
            source = self.build_source(task.source, task.path, months=task.months)
            projects = source.discover()
            if task.projects:
                wanted = set(task.projects)
                projects = [p for p in projects if p.id in wanted or p.name in wanted]
            task.total = len(projects)
            if not projects:
                task.messages.append("没有发现任何项目，请检查素材目录结构（月份/项目 两级）")
                task.status = "done"
                task.ended_at = time.time()
                return

            pipeline = Pipeline(
                self.config, source,
                template_id=task.template_id or None,
                music_id=task.music_id,
            )

            for project in projects:
                if task.cancel_requested:
                    task.messages.append("任务已按请求取消")
                    task.status = "cancelled"
                    break
                task.current_project = project.name
                task.stage = "准备素材"
                task.messages.append(f"开始处理：{project.name}")
                try:
                    artifacts = pipeline.run_project(project)
                except AutoCutError as exc:
                    task.messages.append(f"「{project.name}」失败：{exc}")
                    task.finished += 1
                    continue
                except Exception as exc:  # pragma: no cover - 兜底
                    logger.exception("项目处理异常")
                    task.messages.append(f"「{project.name}」异常：{exc}")
                    task.finished += 1
                    continue

                result = artifacts.result
                if result is None:
                    task.finished += 1
                    continue
                task.results.append(result.to_dict())
                task.finished += 1
                task.messages.append(
                    f"完成：{result.project_name} · {result.clip_count} 个镜头 · {result.duration_sec:.1f}s"
                )

            if task.status == "running":
                task.status = "done"
                self._write_summary(task)
        except AutoCutError as exc:
            task.status = "failed"
            task.error = str(exc)
        except Exception as exc:  # pragma: no cover - 兜底
            task.status = "failed"
            task.error = f"{exc}\n{traceback.format_exc()}"
        finally:
            task.current_project = ""
            task.stage = ""
            task.ended_at = time.time()

    def _write_summary(self, task: TaskState) -> None:
        summary = {
            "total": len(task.results),
            "succeeded": len([r for r in task.results if r.get("status") == "DRAFT_READY"]),
            "failed": len([r for r in task.results if r.get("status") != "DRAFT_READY"]),
            "source": task.source,
            "projects": task.results,
        }
        path = self.config.workspace_path / "batch_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "AutoCutOperator/0.1"
    operator: OperatorServer

    # 静默默认日志，改用项目自己的 logger
    def log_message(self, fmt: str, *args: Any) -> None:  # pragma: no cover
        logger.debug("HTTP %s", fmt % args)

    # --- 工具 ---
    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message: str, status: int = 400) -> None:
        self._send_json({"ok": False, "error": message}, status)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        if not raw.strip():
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AutoCutError(f"请求体不是合法 JSON：{exc}") from exc
        return payload if isinstance(payload, dict) else {}

    def _send_file(self, path: Path) -> None:
        mime, _ = mimetypes.guess_type(str(path))
        if path.suffix.lower() in {".html", ".htm"}:
            mime = "text/html"
        if mime and mime.startswith("text/") or path.suffix.lower() in {".html", ".htm", ".json", ".js", ".css", ".svg"}:
            if not mime:
                mime = "text/plain"
            mime = f"{mime}; charset=utf-8"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # --- 路由 ---
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)
        try:
            if route in {"/", "/index.html"}:
                return self._serve_static("index.html")
            if route == "/api/status":
                return self._send_json({"ok": True, "data": self.operator.status()})
            if route == "/api/projects":
                kind = (query.get("source") or ["mock"])[0]
                path = (query.get("path") or [""])[0]
                months = query.get("month") or []
                projects = self.operator.discover(kind, path, months)
                return self._send_json({"ok": True, "data": projects})
            if route == "/api/drafts":
                return self._send_json({"ok": True, "data": self.operator.list_drafts()})
            if route == "/api/options":
                return self._send_json({"ok": True, "data": self.operator.list_options()})
            if route.startswith("/api/task/"):
                task_id = route.rsplit("/", 1)[-1]
                with TASKS_LOCK:
                    task = TASKS.get(task_id)
                if task is None:
                    return self._send_error_json("任务不存在", 404)
                return self._send_json({"ok": True, "data": task.to_dict()})
            if route == "/api/file":
                raw = (query.get("path") or [""])[0]
                target = self.operator.safe_path(raw)
                if target is None or not target.is_file():
                    return self._send_error_json("文件不存在或不在允许范围内", 404)
                return self._send_file(target)
            if route == "/static/preview":
                # 让前端可以内嵌预览页
                raw = (query.get("path") or [""])[0]
                target = self.operator.safe_path(raw)
                if target is None or not target.is_file():
                    return self._send_error_json("预览文件不存在", 404)
                return self._send_file(target)
            if route.startswith("/assets/"):
                return self._serve_static(route.lstrip("/"))
            return self._send_error_json("未知接口", 404)
        except AutoCutError as exc:
            return self._send_error_json(str(exc), 400)
        except Exception as exc:  # pragma: no cover - 兜底
            logger.exception("GET %s 处理失败", route)
            return self._send_error_json(f"服务内部错误：{exc}", 500)

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        try:
            body = self._read_body()
            if route == "/api/pick-dir":
                chosen = self.operator.pick_directory(str(body.get("initial") or ""))
                return self._send_json({"ok": True, "data": {"path": chosen}})
            if route == "/api/run":
                task = self.operator.submit(body)
                return self._send_json({"ok": True, "data": {"task_id": task.id}})
            if route == "/api/login":
                wait_sec = int(body.get("wait_sec") or 180)
                task = self.operator.submit_login(wait_sec)
                return self._send_json({"ok": True, "data": {"task_id": task.id}})
            if route == "/api/record":
                seconds = int(body.get("seconds") or 90)
                seconds = max(30, min(seconds, 300))
                task = self.operator.submit_record(seconds)
                return self._send_json({"ok": True, "data": {"task_id": task.id}})
            if route.startswith("/api/cancel/"):
                task_id = route.rsplit("/", 1)[-1]
                with TASKS_LOCK:
                    task = TASKS.get(task_id)
                if task is None:
                    return self._send_error_json("任务不存在", 404)
                task.cancel_requested = True
                return self._send_json({"ok": True, "data": {"task_id": task_id}})
            if route == "/api/open":
                raw = str(body.get("path") or "")
                target = self.operator.safe_path(raw)
                if target is None:
                    return self._send_error_json("路径不在允许范围内", 400)
                opened = self.operator.open_in_os(target)
                return self._send_json({"ok": True, "data": {"opened": opened}})
            return self._send_error_json("未知接口", 404)
        except AutoCutError as exc:
            return self._send_error_json(str(exc), 400)
        except Exception as exc:  # pragma: no cover - 兜底
            logger.exception("POST %s 处理失败", route)
            return self._send_error_json(f"服务内部错误：{exc}", 500)

    def _serve_static(self, rel: str) -> None:
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
            return self._send_error_json("非法静态资源路径", 400)
        if not target.is_file():
            return self._send_error_json(f"静态资源不存在：{rel}", 404)
        return self._send_file(target)


def serve(config: AppConfig, host: str = "127.0.0.1", port: int = 8765,
          open_browser: bool = True) -> int:
    """启动操作台服务，阻塞直到 Ctrl+C。"""
    setup_logging(config.logs_dir, verbose=False)
    operator = OperatorServer(config)
    handler = type("BoundHandler", (Handler,), {"operator": operator})

    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        print(f"端口 {port} 无法监听：{exc}")
        print(f"试试换一个端口：python run.py serve --port {port + 1}")
        return 1

    url = f"http://{host}:{port}/"
    print("=" * 66)
    print("  AutoCut 操作台已启动")
    print("=" * 66)
    print(f"  页面地址：{url}")
    print(f"  工作区  ：{config.workspace_path}")
    print(f"  草稿目录：{config.resolved_draft_root}")
    print("  按 Ctrl+C 停止服务")
    print("=" * 66)

    if open_browser:
        def _open() -> None:
            time.sleep(0.8)
            try:
                if platform.system() == "Windows":
                    os.startfile(url)  # type: ignore[attr-defined]
                elif platform.system() == "Darwin":
                    subprocess.run(["open", url], check=False)
                else:
                    subprocess.run(["xdg-open", url], check=False)
            except Exception:  # pragma: no cover
                pass
        threading.Thread(target=_open, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n操作台已停止。")
    finally:
        httpd.server_close()
    return 0
