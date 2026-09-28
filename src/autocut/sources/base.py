"""素材源抽象。

统一契约：
    discover(months) -> list[Project]      发现可处理的项目（月份文件夹 → 项目文件夹）
    fetch(project)   -> list[Material]     拉取素材并完成探测，产出可直接进入分析流水线的记录

三种实现：
    LocalSource     本地/网络盘素材目录（MVP 推荐，零依赖）
    MockSource      演示与自测用，合成确定性素材元数据，不需要真实视频
    CapCutWebSource 剪映云空间网页版（Playwright，需要用户扫码授权）
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Material, Project


@dataclass
class SourceOptions:
    """跨实现的通用参数。"""

    copy_materials: bool = False        # 是否把素材复制到工作区（云空间源默认复制）
    months: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    recursive_scan: bool = True


class MaterialSource(abc.ABC):
    """素材源基类。"""

    name = "base"

    def __init__(self, options: SourceOptions | None = None) -> None:
        self.options = options or SourceOptions()

    @abc.abstractmethod
    def discover(self) -> list[Project]:
        """发现项目列表。"""

    @abc.abstractmethod
    def fetch(self, project: Project) -> list[Material]:
        """拉取并探测某个项目的素材。"""

    # --- 通用工具 ---
    def filter_projects(self, projects: list[Project]) -> list[Project]:
        result = projects
        if self.options.months:
            wanted = {m.strip() for m in self.options.months if m.strip()}
            result = [p for p in result if p.month in wanted]
        if self.options.projects:
            wanted = {p.strip() for p in self.options.projects if p.strip()}
            result = [p for p in result if p.name in wanted or p.id in wanted]
        return result

    def describe(self) -> str:
        return self.name

    def close(self) -> None:
        """释放资源（浏览器等），默认无操作。"""
