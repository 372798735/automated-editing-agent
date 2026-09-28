"""运行环境能力探测。

MVP 的关键设计：**能力可降级**。核心流水线只依赖标准库即可跑通，
外部能力（ffmpeg / LLM / VLM / Playwright / 剪映草稿目录）存在则自动增强。

`autocut doctor` 命令会把这里的结果打印成一张体检表。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Capability:
    key: str
    name: str
    available: bool
    detail: str
    impact: str          # 缺失时对功能的影响
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "available": self.available,
            "detail": self.detail,
            "impact": self.impact,
            "required": self.required,
        }


@dataclass
class CapabilityReport:
    python_version: str
    platform: str
    capabilities: list[Capability] = field(default_factory=list)

    def add(self, cap: Capability) -> None:
        self.capabilities.append(cap)

    def get(self, key: str) -> Capability | None:
        for cap in self.capabilities:
            if cap.key == key:
                return cap
        return None

    @property
    def missing_required(self) -> list[Capability]:
        return [c for c in self.capabilities if c.required and not c.available]

    def to_dict(self) -> dict[str, Any]:
        return {
            "python_version": self.python_version,
            "platform": self.platform,
            "capabilities": [c.to_dict() for c in self.capabilities],
        }


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


def _binary_version(binary: str, args: tuple[str, ...] = ("-version",)) -> str:
    path = shutil.which(binary)
    if not path:
        return ""
    try:
        proc = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace"
        )
        first_line = (proc.stdout or proc.stderr or "").strip().splitlines()
        return first_line[0][:80] if first_line else path
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return path


def default_jianying_draft_dir() -> Path:
    """剪映专业版默认草稿目录（Windows）。"""
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        return Path(local) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"
    return Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"


def detect(draft_root: str = "", llm_api_key_env: str = "AUTOCUT_LLM_API_KEY") -> CapabilityReport:
    report = CapabilityReport(python_version=sys.version.split()[0], platform=sys.platform)

    # --- 硬性要求 ---
    report.add(Capability(
        key="python",
        name="Python 3.10+",
        available=sys.version_info >= (3, 10),
        detail=sys.version.split()[0],
        impact="运行流水线的最低要求",
        required=True,
    ))

    # --- 媒体处理 ---
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    report.add(Capability(
        key="ffmpeg",
        name="FFmpeg",
        available=bool(ffmpeg),
        detail=ffmpeg or "未检测到",
        impact="缺失时无法抽帧做画面质量分析，退回「元数据启发式」打分；渲染脚本同样不可用",
    ))
    report.add(Capability(
        key="ffprobe",
        name="FFprobe",
        available=bool(ffprobe),
        detail=ffprobe or "未检测到",
        impact="缺失时使用内置纯 Python MP4 解析器提取时长/分辨率/帧率（MP4/MOV 有效）",
    ))

    # --- AI 能力 ---
    api_key = os.environ.get(llm_api_key_env, "").strip()
    report.add(Capability(
        key="llm",
        name="LLM 文案润色",
        available=bool(api_key),
        detail=f"环境变量 {llm_api_key_env} " + ("已配置" if api_key else "未配置"),
        impact="缺失时使用模板填槽生成文案（可用，但缺少个性化润色）",
    ))
    report.add(Capability(
        key="vlm",
        name="多模态镜头理解",
        available=False if not api_key else _module_available("json"),
        detail="基于 OpenAI 兼容 /chat/completions 接口，标准库 urllib 调用",
        impact="缺失时使用启发式镜头分类（按位置/时长/画质推断类型）",
    ))

    # --- 素材接入 ---
    report.add(Capability(
        key="playwright",
        name="Playwright（剪映云空间同步）",
        available=_module_available("playwright"),
        detail="已安装" if _module_available("playwright") else "未安装（pip install playwright && playwright install chromium）",
        impact="缺失时无法自动同步云空间，可改用「本地素材目录」模式",
    ))

    # --- 剪映草稿目录 ---
    target = Path(draft_root) if draft_root else default_jianying_draft_dir()
    report.add(Capability(
        key="draft_dir",
        name="剪映草稿目录",
        available=target.exists(),
        detail=str(target),
        impact="缺失时（如未装剪映/非本机）草稿写入 workspace/draft_output，可手动拷贝到剪映目录",
    ))

    # --- 可选增强 ---
    report.add(Capability(
        key="pyyaml",
        name="PyYAML",
        available=_module_available("yaml"),
        detail="可选，用于读取 .yaml 配置与模板",
        impact="缺失时请使用 .json 配置（MVP 默认）",
    ))
    return report


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe_path() -> str | None:
    return shutil.which("ffprobe")
