"""领域模型。

全部用 dataclass 表达，保持「可序列化、可单测、无框架耦合」：
- 媒体层产出 Material / Shot
- 创意层产出 Copywriting / MusicAsset
- 时间线层产出 Timeline（纯逻辑，可脱离剪映独立验证）
- 草稿层消费 Timeline，不反向依赖
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 常量：镜头类型（与 PRD 第 3.2 节选片策略一一对应）
# ---------------------------------------------------------------------------


class ShotType:
    HOOK = "hook"          # 开场钩子：完工全景 / 大场景
    PROCESS = "process"    # 过程展示：铺装动作
    DETAIL = "detail"      # 细节特写：拼接缝 / 收边
    FINISH = "finish"      # 收尾：全屋整体效果
    INVALID = "invalid"    # 无效镜头：晃动 / 过暗 / 过短 / 重复机位

    ALL = (HOOK, PROCESS, DETAIL, FINISH, INVALID)
    USABLE = (HOOK, PROCESS, DETAIL, FINISH)


def _default(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    raise TypeError(f"无法序列化的类型: {type(obj)!r}")


class SerializableMixin:
    """提供 to_dict / to_json / from_dict，避免各模型重复实现。"""

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(dataclasses.asdict(self), default=_default, ensure_ascii=False))

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        return cls(**data)


# ---------------------------------------------------------------------------
# 素材与镜头
# ---------------------------------------------------------------------------


@dataclass
class Material(SerializableMixin):
    """一个素材文件（视频/图片）。"""

    id: str
    project_id: str
    name: str
    path: str
    file_type: str = "video"           # video / image
    duration_sec: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    size_bytes: int = 0
    checksum: str = ""
    probe_source: str = "unknown"      # ffprobe / mp4box / mock
    has_audio: bool = False


@dataclass
class Shot(SerializableMixin):
    """素材切分后的候选镜头。"""

    id: str
    material_id: str
    material_path: str
    start_sec: float
    end_sec: float
    shot_type: str = ShotType.PROCESS
    quality_score: float = 0.0          # 0-100，画面可用度（亮度/清晰度/稳定度）
    aesthetic_score: float = 0.0        # 0-100，画面观感
    description: str = ""
    analyzer: str = "heuristic"         # heuristic / vlm
    score_detail: dict[str, float] = field(default_factory=dict)
    is_selected: bool = False

    @property
    def duration_sec(self) -> float:
        return round(max(0.0, self.end_sec - self.start_sec), 3)

    @property
    def final_score(self) -> float:
        """综合分：质量 7 : 观感 3。"""
        return round(self.quality_score * 0.7 + self.aesthetic_score * 0.3, 2)


# ---------------------------------------------------------------------------
# 创意产物
# ---------------------------------------------------------------------------


@dataclass
class Copywriting(SerializableMixin):
    """一个项目的文案产物。"""

    project_id: str
    title: str = ""
    subtitles: list[str] = field(default_factory=list)   # 逐镜头字幕
    hashtags: list[str] = field(default_factory=list)
    template_id: str = ""
    generator: str = "template"                          # template / template+llm
    variables: dict[str, str] = field(default_factory=dict)


@dataclass
class MusicAsset(SerializableMixin):
    """背景音乐资产。"""

    id: str
    name: str
    path: str
    duration_sec: float = 0.0
    bpm: float = 0.0
    mood: str = ""
    loudness_ok: bool = True


# ---------------------------------------------------------------------------
# 时间线（剪辑决策结果）
# ---------------------------------------------------------------------------


@dataclass
class TimelineClip(SerializableMixin):
    """时间线上的一个视频片段。"""

    index: int
    material_id: str
    material_path: str
    source_start_sec: float
    duration_sec: float
    target_start_sec: float
    shot_type: str
    subtitle: str = ""
    transition: str = ""      # "" 表示硬切；其余为叠化等转场名
    volume: float = 0.0       # 原声保留比例，0 = 静音（由 BGM 主导）


@dataclass
class Timeline(SerializableMixin):
    """完整成片时间线，是草稿生成器唯一的输入。"""

    project_id: str
    project_name: str
    width: int = 1080
    height: int = 1920
    fps: float = 30.0
    total_duration_sec: float = 0.0
    clips: list[TimelineClip] = field(default_factory=list)
    music: MusicAsset | None = None
    music_start_sec: float = 0.0
    music_volume: float = 0.8
    title: str = ""
    title_duration_sec: float = 2.5
    outro_text: str = ""
    outro_duration_sec: float = 2.5
    copywriting: Copywriting | None = None
    selected_shot_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        if self.music is not None:
            data["music"] = self.music.to_dict()
        if self.copywriting is not None:
            data["copywriting"] = self.copywriting.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Timeline":
        payload = dict(data)
        clips = [TimelineClip.from_dict(c) for c in payload.pop("clips", [])]
        music = payload.pop("music", None)
        copywriting = payload.pop("copywriting", None)
        timeline = cls(**payload)
        timeline.clips = clips
        timeline.music = MusicAsset.from_dict(music) if music else None
        timeline.copywriting = Copywriting.from_dict(copywriting) if copywriting else None
        return timeline


# ---------------------------------------------------------------------------
# 项目与任务
# ---------------------------------------------------------------------------


@dataclass
class Project(SerializableMixin):
    """一个剪辑项目 = 剪映云空间里的一个项目文件夹。"""

    id: str
    name: str
    month: str = ""
    source_path: str = ""
    material_count: int = 0
    status: str = "PENDING"
    variables: dict[str, str] = field(default_factory=dict)


@dataclass
class PipelineResult(SerializableMixin):
    """一次流水线执行的结果摘要，用于 CLI 输出与报告落盘。"""

    project_id: str
    project_name: str
    status: str
    duration_sec: float = 0.0
    clip_count: int = 0
    material_count: int = 0
    shot_count: int = 0
    selected_count: int = 0
    draft_path: str = ""
    preview_path: str = ""
    render_script_path: str = ""
    timeline_path: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)


def dump_all(items: Iterable[SerializableMixin]) -> list[dict[str, Any]]:
    return [item.to_dict() for item in items]
