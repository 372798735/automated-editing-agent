"""品牌套件（Brand Kit）：片头标题、字幕、落版的统一样式。

把「样式」从「内容」里拆出来，是为了让同一套视觉调性能被所有项目复用，
同时让草稿生成器保持纯粹的格式转换职责。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ConfigError

HEX_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"


@dataclass
class TextStyle:
    font_size: int = 8
    text_color: str = "#FFFFFF"
    border_color: str = "#000000"
    border_width: float = 0.08
    alignment: int = 1                 # 1=居中（会写入富文本内联样式）
    position_x: float = 0.0            # 归一化坐标，0 = 居中
    position_y: float = -0.7           # 负值 = 靠上
    line_spacing: float = 0.02
    background_alpha: float = 0.0
    bold: bool = False
    has_shadow: bool = True

    def to_dict(self) -> dict:
        return {
            "font_size": self.font_size,
            "text_color": self.text_color,
            "border_color": self.border_color,
            "border_width": self.border_width,
            "alignment": self.alignment,
            "position_x": self.position_x,
            "position_y": self.position_y,
            "line_spacing": self.line_spacing,
            "background_alpha": self.background_alpha,
            "bold": self.bold,
            "has_shadow": self.has_shadow,
        }


@dataclass
class BrandKit:
    name: str = "default"
    # 标题：暖金色加粗（地板/家装暖色调），深棕描边压得住画面
    title_style: TextStyle = field(default_factory=lambda: TextStyle(
        font_size=12, text_color="#FFD76A", border_color="#4A2B10",
        border_width=0.12, bold=True, position_y=-0.55))
    # 字幕：白字 + 深色描边 + 轻阴影，任何画面上都清晰
    subtitle_style: TextStyle = field(default_factory=lambda: TextStyle(
        font_size=8, text_color="#FFFFFF", border_color="#1A1A1A",
        border_width=0.10, position_y=0.62))
    outro_text: str = ""
    # 落版：与标题呼应的金色
    outro_style: TextStyle = field(default_factory=lambda: TextStyle(
        font_size=10, text_color="#FFD76A", border_color="#4A2B10",
        border_width=0.10, background_alpha=0.15, position_y=0.0))
    title_duration_sec: float = 2.5
    outro_duration_sec: float = 2.5

    @classmethod
    def load(cls, path: Path) -> "BrandKit":
        if not path.exists():
            raise ConfigError(f"品牌套件文件不存在: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, data: dict) -> "BrandKit":
        def style(raw: dict | None, default: TextStyle) -> TextStyle:
            if not raw:
                return default
            fields = {f for f in TextStyle.__dataclass_fields__}  # type: ignore[attr-defined]
            merged = {**default.to_dict(), **{k: v for k, v in raw.items() if k in fields}}
            return TextStyle(**merged)

        return cls(
            name=data.get("name", "default"),
            title_style=style(data.get("title_style"), TextStyle(
                font_size=12, text_color="#FFD76A", border_color="#4A2B10",
                border_width=0.12, bold=True, position_y=-0.55)),
            subtitle_style=style(data.get("subtitle_style"), TextStyle(
                font_size=8, text_color="#FFFFFF", border_color="#1A1A1A",
                border_width=0.10, position_y=0.62)),
            outro_text=data.get("outro_text", ""),
            outro_style=style(data.get("outro_style"), TextStyle(
                font_size=10, text_color="#FFD76A", border_color="#4A2B10",
                border_width=0.10, background_alpha=0.15, position_y=0.0)),
            title_duration_sec=float(data.get("title_duration_sec", 2.5)),
            outro_duration_sec=float(data.get("outro_duration_sec", 2.5)),
        )

    def render_outro(self, variables: dict[str, str]) -> str:
        text = self.outro_text or "专业地板铺装｜{brand}"
        for key, value in variables.items():
            text = text.replace("{" + key + "}", value or "")
        return text
