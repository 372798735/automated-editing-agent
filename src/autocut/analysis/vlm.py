"""多模态 VLM 镜头分析器（可选增强通道）。

工作方式：对每个候选镜头抽取 3 帧 JPEG，以 base64 内联发给视觉大模型，
要求返回 JSON：镜头类型、画质分、观感分、一句话描述。
任何一步失败（未配 Key / 网络异常 / JSON 解析失败）都回退到启发式结果，
保证流水线永不因 AI 通道不可用而中断。
"""

from __future__ import annotations

import base64
from pathlib import Path

from ..llm import build_client
from ..logging_setup import get_logger
from ..media import frames as frames_mod
from ..models import Material, Shot, ShotType
from .base import AnalyzeContext
from .heuristic import HeuristicAnalyzer

logger = get_logger("analysis.vlm")

SYSTEM_PROMPT = """你是短视频剪辑师，负责给「地板铺装」施工素材的候选镜头分类打分。
镜头类型只能是以下之一：
- hook：开场钩子，完工全景或大场景，画面稳定光线好
- process：铺装过程，工人铺装动作、裁切、找平
- detail：细节特写，地板拼接缝、收边、纹理、倒角
- finish：收尾，全屋整体效果、开门视角
- invalid：无效镜头，画面过暗、剧烈晃动、糊片、纯杂物

只输出 JSON，不要任何解释。字段：
{"shot_type":"hook|process|detail|finish|invalid","quality":0-100,"aesthetic":0-100,"description":"不超过20字的中文描述"}"""


class VLMShotAnalyzer(HeuristicAnalyzer):
    """先跑启发式拿到兜底分数，再用 VLM 覆盖语义判断。"""

    name = "vlm"

    def __init__(self, context: AnalyzeContext) -> None:
        super().__init__(context)
        self.client = build_client(context.config.llm) if context.config.llm.vision_enabled else None
        if self.client is None:
            self.context.warnings.append(
                "未启用多模态分析（需要配置 LLM Key 且开启 llm.vision_enabled），当前使用启发式通道"
            )
        self._call_count = 0

    # ------------------------------------------------------------------
    def analyze(self, material: Material, window: tuple[float, float]) -> Shot:
        shot = super().analyze(material, window)
        if self.client is None or material.probe_source == "mock":
            return shot
        path = Path(material.path)
        if not path.exists():
            return shot

        images = self._collect_images(path, window)
        if not images:
            return shot

        result = self._ask(images)
        if not result:
            return shot

        shot_type = str(result.get("shot_type", "")).strip().lower()
        if shot_type in ShotType.ALL:
            shot.shot_type = shot_type
        for key, attr in (("quality", "quality_score"), ("aesthetic", "aesthetic_score")):
            value = result.get(key)
            if isinstance(value, (int, float)):
                setattr(shot, attr, round(max(0.0, min(100.0, float(value))), 2))
        description = str(result.get("description", "")).strip()
        if description:
            shot.description = description
        shot.analyzer = "vlm"
        shot.score_detail["vlm"] = 1.0
        return shot

    # ------------------------------------------------------------------
    def _collect_images(self, path: Path, window: tuple[float, float]) -> list[str]:
        start, end = window
        span = max(0.2, end - start)
        count = max(1, self.config.analysis.frames_per_shot)
        images: list[str] = []
        for index in range(count):
            timestamp = start + span * (index + 0.5) / count
            data = frames_mod.extract_jpeg_bytes(path, timestamp, width=512)
            if data:
                images.append(base64.b64encode(data).decode("ascii"))
        return images

    def _ask(self, images: list[str]) -> dict | None:
        content: list[dict] = [{
            "type": "text",
            "text": f"这是同一个镜头按时间顺序抽取的 {len(images)} 帧，请判断镜头类型并打分。",
        }]
        for image in images:
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}})

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
        self._call_count += 1
        return self.client.chat_json(
            messages,
            model=self.config.llm.vision_model,
            temperature=0.2,
        )
