"""配置加载与校验。

MVP 采用「零第三方依赖」策略：用 dataclass + 显式校验替代 Pydantic，
配置来源优先级：CLI 参数 > 环境变量 > config/default.json > 内置默认值。

配置文件同时支持 .json 与 .yaml（安装了 PyYAML 时），方便运营直接手改模板。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .capabilities import default_jianying_draft_dir
from .errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def load_structured_file(path: Path) -> dict[str, Any]:
    """读取 json / yaml 配置，yaml 需要 PyYAML（缺失时给出明确提示）。"""
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".json", ""}:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"JSON 解析失败 {path}: {exc}") from exc
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise ConfigError(
                f"{path} 是 YAML 配置，但当前环境未安装 PyYAML。"
                "请执行 pip install pyyaml，或改用同名的 .json 配置。"
            ) from exc
        return yaml.safe_load(text) or {}
    raise ConfigError(f"不支持的配置格式: {path.suffix}")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并字典，override 优先。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# 配置段
# ---------------------------------------------------------------------------


@dataclass
class VideoConfig:
    width: int = 1080
    height: int = 1920
    fps: float = 30.0
    target_min_sec: float = 25.0
    target_max_sec: float = 45.0
    clip_min_sec: float = 2.0
    clip_max_sec: float = 6.0
    max_clips: int = 12
    min_clips: int = 5
    opening_sec: float = 3.0
    transition: str = "叠化"                # 单一转场（preset=single 时用）
    transition_duration_sec: float = 0.5
    transition_preset: str = "mixed"        # mixed=轮换多种 / single=只用 transition / off=硬切
    transitions: list[str] = field(default_factory=lambda: [
        "叠化", "闪白", "推近", "拉远", "擦除", "雾化",
    ])
    ken_burns: bool = True                  # 镜头缓慢推近/拉远（画面动感）

    def validate(self) -> None:
        if self.target_min_sec <= 0 or self.target_max_sec <= self.target_min_sec:
            raise ConfigError("video.target_min_sec / target_max_sec 非法")
        if self.clip_min_sec <= 0 or self.clip_max_sec < self.clip_min_sec:
            raise ConfigError("video.clip_min_sec / clip_max_sec 非法")
        if self.max_clips < self.min_clips:
            raise ConfigError("video.max_clips 不能小于 min_clips")
        if not 0 < self.transition_duration_sec <= 2.0:
            raise ConfigError("video.transition_duration_sec 应在 (0, 2] 秒之间")
        if self.transition_preset not in ("mixed", "single", "off"):
            raise ConfigError("video.transition_preset 只能是 mixed / single / off")


@dataclass
class SelectionConfig:
    min_quality_score: float = 35.0
    min_shot_sec: float = 1.5
    dedupe_same_material: bool = True     # 同一素材默认只用 1 个镜头，避免重复机位
    max_clips_per_material: int = 2
    detail_insert_ratio: float = 0.35     # 特写镜头占过程段比例上限
    require_hook: bool = True
    fallback_fill: bool = True            # 镜头不足时允许放宽阈值补足

    def validate(self) -> None:
        if not 0 <= self.min_quality_score <= 100:
            raise ConfigError("selection.min_quality_score 应在 0-100 之间")


@dataclass
class AnalysisConfig:
    enabled: bool = True
    provider: str = "heuristic"           # heuristic / vlm / auto
    frames_per_shot: int = 3
    keyframe_interval_sec: float = 2.0
    min_shot_sec: float = 2.0
    max_shot_sec: float = 8.0


@dataclass
class LLMConfig:
    enabled: bool = False
    base_url: str = "https://api.deepseek.com/v1"
    api_key_env: str = "AUTOCUT_LLM_API_KEY"
    model: str = "deepseek-chat"
    timeout_sec: int = 60
    temperature: float = 0.8
    max_retries: int = 2
    vision_model: str = "qwen-vl-max"
    vision_enabled: bool = False

    def resolve_api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()


@dataclass
class CreativeConfig:
    template_file: str = "config/copy_templates.json"
    brand_kit_file: str = "config/brand_kit.json"
    music_library_file: str = "config/music_library.json"
    default_template_id: str = "floor_showcase"
    banned_words: list[str] = field(default_factory=lambda: [
        "最好", "第一", "国家级", "绝对", "100%", "永久", "根治",
    ])
    max_hashtags: int = 5


@dataclass
class DraftConfig:
    # 剪映专业版草稿目录；为空则写入 workspace/draft_output（离线预览模式）
    draft_root: str = ""
    schema_profile: str = "jianying_5_9"
    write_preview_html: bool = True
    emit_render_script: bool = True


@dataclass
class AppConfig:
    workspace: str = "workspace"
    log_level: str = "INFO"
    video: VideoConfig = field(default_factory=VideoConfig)
    selection: SelectionConfig = field(default_factory=SelectionConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    creative: CreativeConfig = field(default_factory=CreativeConfig)
    draft: DraftConfig = field(default_factory=DraftConfig)

    # ---- 路径辅助 ----
    @property
    def root(self) -> Path:
        return PROJECT_ROOT

    @property
    def workspace_path(self) -> Path:
        path = Path(self.workspace)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def materials_dir(self) -> Path:
        return self.workspace_path / "materials"

    @property
    def projects_dir(self) -> Path:
        return self.workspace_path / "projects"

    @property
    def logs_dir(self) -> Path:
        return self.workspace_path / "logs"

    @property
    def draft_output_dir(self) -> Path:
        return self.workspace_path / "draft_output"

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def resolved_draft_root(self) -> Path:
        """草稿实际输出目录：显式配置 > 剪映默认草稿目录（存在时）> 工作区目录。"""
        configured = self.draft.draft_root
        if configured:
            return Path(configured)
        # 剪映专业版装在本机时，直接写进它的草稿目录，打开剪映即可见
        jianying_dir = default_jianying_draft_dir()
        if jianying_dir.is_dir():
            return jianying_dir
        return self.draft_output_dir

    # ---- 构建 ----
    @classmethod
    def load(cls, config_path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> "AppConfig":
        data: dict[str, Any] = {}
        path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        if path.exists():
            data = load_structured_file(path)
        if overrides:
            data = deep_merge(data, overrides)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        unknown = set(data) - known
        if unknown:
            raise ConfigError(f"配置包含未知字段: {sorted(unknown)}")

        def build(klass, raw, name):
            raw = raw or {}
            fields = {f.name for f in klass.__dataclass_fields__.values()}  # type: ignore[attr-defined]
            bad = set(raw) - fields
            if bad:
                raise ConfigError(f"配置段 {name} 包含未知字段: {sorted(bad)}")
            return klass(**raw)

        config = cls(
            workspace=data.get("workspace", "workspace"),
            log_level=data.get("log_level", "INFO"),
            video=build(VideoConfig, data.get("video"), "video"),
            selection=build(SelectionConfig, data.get("selection"), "selection"),
            analysis=build(AnalysisConfig, data.get("analysis"), "analysis"),
            llm=build(LLMConfig, data.get("llm"), "llm"),
            creative=build(CreativeConfig, data.get("creative"), "creative"),
            draft=build(DraftConfig, data.get("draft"), "draft"),
        )
        config.video.validate()
        config.selection.validate()
        return config
