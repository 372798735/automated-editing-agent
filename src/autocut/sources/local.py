"""本地素材源。

目录约定（与剪映云空间一致，便于从云空间导出后直接使用）：

    <root>/
      2026-08/
        阳光花园-张先生-全屋通铺/
          VID_001.mp4 ...
      2026-09/
        星河湾-李女士-实木多层/
          ...

单独的项目目录也可以直接作为 root（此时 month 从目录名或修改时间推断）。
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from ..errors import SourceError
from ..logging_setup import get_logger
from ..media import probe
from ..models import Material, Project
from .base import MaterialSource, SourceOptions

logger = get_logger("sources.local")

MONTH_PATTERN = re.compile(r"^(20\d{2})[-_.年]?(0[1-9]|1[0-2])月?$")
MAX_DEPTH_SCAN = 2


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value.strip())
    return re.sub(r"-{2,}", "-", cleaned).strip("-") or "project"


def _project_id(month: str, name: str) -> str:
    digest = hashlib.sha1(f"{month}/{name}".encode("utf-8")).hexdigest()[:8]
    return f"{month or 'unknown'}-{_slugify(name)[:24]}-{digest}"


def looks_like_month(name: str) -> bool:
    return bool(MONTH_PATTERN.match(name))


class LocalSource(MaterialSource):
    name = "local"

    def __init__(self, root: str | Path, options: SourceOptions | None = None, materials_root: Path | None = None) -> None:
        super().__init__(options)
        self.root = Path(root)
        self.materials_root = materials_root
        if not self.root.exists():
            raise SourceError(f"素材目录不存在: {self.root}")
        if not self.root.is_dir():
            raise SourceError(f"素材源必须是目录: {self.root}")

    # ------------------------------------------------------------------
    # 发现
    # ------------------------------------------------------------------
    def discover(self) -> list[Project]:
        projects: list[Project] = []
        for child in sorted(self.root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if looks_like_month(child.name):
                for sub in sorted(child.iterdir()):
                    if sub.is_dir() and not sub.name.startswith("."):
                        projects.append(self._build_project(child.name, sub))
            else:
                # root 直接就是项目目录集合（无月份层级）
                projects.append(self._build_project("", child))
        logger.info("本地源发现 %s 个项目（root=%s）", len(projects), self.root)
        return self.filter_projects(projects)

    def _build_project(self, month: str, path: Path) -> Project:
        return Project(
            id=_project_id(month, path.name),
            name=path.name,
            month=month,
            source_path=str(path),
            status="DISCOVERED",
        )

    # ------------------------------------------------------------------
    # 拉取
    # ------------------------------------------------------------------
    def fetch(self, project: Project) -> list[Material]:
        source_dir = Path(project.source_path)
        if not source_dir.is_dir():
            raise SourceError(f"项目目录不存在: {source_dir}")

        files = self._scan_media(source_dir)
        if not files:
            logger.warning("项目「%s」没有可用素材文件", project.name)
            return []

        target_dir = None
        if self.options.copy_materials and self.materials_root is not None:
            target_dir = self.materials_root / (project.month or "unknown") / _slugify(project.name)
            target_dir.mkdir(parents=True, exist_ok=True)

        materials: list[Material] = []
        for index, path in enumerate(files, start=1):
            material_id = f"{project.id}-m{index:03d}"
            target = path
            if target_dir is not None:
                target = target_dir / path.name
                if not target.exists() or target.stat().st_size != path.stat().st_size:
                    shutil.copy2(path, target)
            material = probe.probe_material(target, material_id, project.id)
            materials.append(material)
        logger.info("项目「%s」拉取素材 %s 个", project.name, len(materials))
        return materials

    def _scan_media(self, directory: Path) -> list[Path]:
        if self.options.recursive_scan:
            candidates = [p for p in directory.rglob("*") if probe.is_media(p)]
        else:
            candidates = [p for p in directory.glob("*") if probe.is_media(p)]
        # 忽略隐藏目录下的文件（如 .thumbnails）
        filtered = [p for p in candidates if not any(part.startswith(".") for part in p.parts)]
        return sorted(filtered, key=lambda p: str(p).lower())


class LocalPlaceholderSource(MaterialSource):
    """当用户只想验证「项目目录存不存在/结构对不对」时使用。"""

    name = "local-scan"

    def __init__(self, root: str | Path, options: SourceOptions | None = None) -> None:
        super().__init__(options)
        self.root = Path(root)

    def discover(self) -> list[Project]:
        return LocalSource(self.root, self.options).discover()

    def fetch(self, project: Project) -> list[Material]:
        return []
