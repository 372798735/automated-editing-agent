"""镜头分析器抽象。"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path

from ..config import AppConfig
from ..models import Material, Shot


@dataclass
class AnalyzeContext:
    """一次分析所需的运行时上下文。"""

    config: AppConfig
    project_id: str
    project_name: str
    variables: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class ShotAnalyzer(abc.ABC):
    """把（素材 + 时间窗）变成 Shot 记录。"""

    name = "base"

    def __init__(self, context: AnalyzeContext) -> None:
        self.context = context
        self.config = context.config

    @abc.abstractmethod
    def segment(self, material: Material) -> list[tuple[float, float]]:
        """把素材切分为候选镜头时间窗 [(start, end), ...]。"""

    @abc.abstractmethod
    def analyze(self, material: Material, window: tuple[float, float]) -> Shot:
        """对单个候选镜头打分与分类。"""

    # ------------------------------------------------------------------
    def analyze_material(self, material: Material) -> list[Shot]:
        shots: list[Shot] = []
        for index, window in enumerate(self.segment(material)):
            shot = self.analyze(material, window)
            shot.id = f"{material.id}-s{index:03d}"
            shots.append(shot)
        return shots

    def analyze_all(self, materials: list[Material]) -> list[Shot]:
        shots: list[Shot] = []
        for material in materials:
            shots.extend(self.analyze_material(material))
        return shots

    def set_project(self, project_id: str, project_name: str, variables: dict[str, str] | None = None) -> None:
        self.context.project_id = project_id
        self.context.project_name = project_name
        if variables:
            self.context.variables = variables
