"""Mock 素材源：演示与自测用。

在没有真实视频、没有剪映、没有 ffmpeg 的机器上也能把整条流水线跑通，
产出结构完整的时间线、草稿 JSON 和预览页面，用于验证「决策链路」是否正确。

素材元数据由项目名 + 序号确定性生成，保证多次运行结果一致（可复现、可对比）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..logging_setup import get_logger
from ..models import Material, Project
from .base import MaterialSource, SourceOptions

logger = get_logger("sources.mock")

DEMO_PROJECTS = [
    ("2026-09", "阳光花园-张先生-全屋通铺"),
    ("2026-09", "星河湾-李女士-实木多层"),
    ("2026-08", "翠湖天地-王先生-人字拼"),
]

RESOLUTIONS = [(1920, 1080), (1080, 1920), (2160, 3840), (1440, 1080)]


def _unit(seed: str) -> float:
    return int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:4], "big") / 0xFFFFFFFF


class MockSource(MaterialSource):
    name = "mock"

    def __init__(
        self,
        options: SourceOptions | None = None,
        materials_per_project: int = 9,
        materials_root: Path | None = None,
    ) -> None:
        super().__init__(options)
        self.materials_per_project = materials_per_project
        self.materials_root = materials_root

    def discover(self) -> list[Project]:
        projects: list[Project] = []
        for month, name in DEMO_PROJECTS:
            projects.append(Project(
                id=f"{month}-{name}-mock",
                name=name,
                month=month,
                source_path=f"capcut://cloud/{month}/{name}",
                status="DISCOVERED",
                variables={"month": month},
            ))
        logger.info("Mock 源发现 %s 个演示项目", len(projects))
        return self.filter_projects(projects)

    def fetch(self, project: Project) -> list[Material]:
        materials: list[Material] = []
        for index in range(1, self.materials_per_project + 1):
            seed = f"{project.id}-{index}"
            unit = _unit(seed)
            width, height = RESOLUTIONS[int(unit * len(RESOLUTIONS)) % len(RESOLUTIONS)]
            # 素材时长 6-28s，码率 6-14 Mbps
            duration = round(6 + _unit(seed + "d") * 22, 1)
            bitrate = 6_000_000 + _unit(seed + "b") * 8_000_000
            size_bytes = int(bitrate * duration / 8)
            material_id = f"{project.id}-m{index:03d}"
            fake_path = (self.materials_root or Path("workspace/materials")) / project.month / project.name / f"MOCK_{index:03d}.mp4"
            materials.append(Material(
                id=material_id,
                project_id=project.id,
                name=f"MOCK_{index:03d}.mp4",
                path=str(fake_path),
                file_type="video",
                duration_sec=duration,
                width=width,
                height=height,
                fps=30.0 if unit > 0.3 else 60.0,
                size_bytes=size_bytes,
                checksum=hashlib.sha256(seed.encode()).hexdigest()[:32],
                probe_source="mock",
                has_audio=index % 3 == 0,
            ))
        logger.info("Mock 项目「%s」生成 %s 条合成素材", project.name, len(materials))
        return materials
