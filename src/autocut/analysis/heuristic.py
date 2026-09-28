"""启发式镜头分析器（默认通道，零外部依赖）。

分两步：
1. **切分**：有 ffmpeg 时用场景切换点 + 时长约束切分；否则按固定窗口切分。
2. **打分与分类**：有帧统计时用画质模型打分；否则用「元数据 + 位置」启发式，
   并依据镜头在素材中的位置、时长、画质分布推断镜头类型。

设计上刻意保持「可解释」：每个分数都带 score_detail，方便运营理解为什么这样选片。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..logging_setup import get_logger
from ..media import frames as frames_mod
from ..media import quality
from ..models import Material, Shot, ShotType
from .base import AnalyzeContext, ShotAnalyzer

logger = get_logger("analysis.heuristic")

# 位置启发式：素材开头易出「大场景钩子」，结尾易出「完工全景」
HOOK_ZONE = 0.18
FINISH_ZONE = 0.78
DETAIL_MAX_DURATION = 4.5


class HeuristicAnalyzer(ShotAnalyzer):
    name = "heuristic"

    def __init__(self, context: AnalyzeContext) -> None:
        super().__init__(context)
        self._scene_cache: dict[str, list[float]] = {}
        self._ffmpeg = frames_mod.ffmpeg_available()
        if not self._ffmpeg:
            self.context.warnings.append(
                "未检测到 ffmpeg：镜头分类与打分使用「元数据启发式」模式，"
                "建议安装 ffmpeg 以获得抽帧级画面分析（winget install Gyan.FFmpeg）"
            )
        self.context.warnings = list(dict.fromkeys(self.context.warnings))

    # ------------------------------------------------------------------
    # 切分
    # ------------------------------------------------------------------
    def segment(self, material: Material) -> list[tuple[float, float]]:
        duration = max(1.0, material.duration_sec)
        min_len = max(1.0, self.config.analysis.min_shot_sec)
        max_len = max(min_len + 0.5, self.config.analysis.max_shot_sec)

        cuts: list[float] = []
        if self._ffmpeg and material.probe_source != "mock" and Path(material.path).exists():
            cuts = self._scene_cuts(material)
            if cuts:
                self.context.warnings = list(dict.fromkeys(self.context.warnings))

        boundaries = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
        windows: list[tuple[float, float]] = []
        for start, end in zip(boundaries, boundaries[1:]):
            span = end - start
            if span < min_len:
                continue
            if span <= max_len:
                windows.append((round(start, 3), round(end, 3)))
                continue
            # 过长片段按 max_len 均分
            pieces = int(span // max_len) + (1 if span % max_len > min_len else 0)
            piece_len = span / max(1, pieces)
            for index in range(pieces):
                piece_start = start + index * piece_len
                piece_end = min(end, piece_start + piece_len)
                if piece_end - piece_start >= min_len:
                    windows.append((round(piece_start, 3), round(piece_end, 3)))

        if not windows:
            windows = self._fixed_windows(duration, min_len, max_len)
        return windows

    def _fixed_windows(self, duration: float, min_len: float, max_len: float) -> list[tuple[float, float]]:
        step = min(max_len, max(min_len, 5.0))
        windows: list[tuple[float, float]] = []
        start = 0.0
        while start < duration - 0.5:
            end = min(duration, start + step)
            if end - start >= min_len:
                windows.append((round(start, 3), round(end, 3)))
            start = end
        return windows or [(0.0, round(duration, 3))]

    def _scene_cuts(self, material: Material) -> list[float]:
        if material.path in self._scene_cache:
            return self._scene_cache[material.path]
        if not Path(material.path).exists():
            self._scene_cache[material.path] = []
            return []
        cuts = frames_mod.detect_scene_cuts(Path(material.path), threshold=0.35)
        self._scene_cache[material.path] = cuts
        return cuts

    # ------------------------------------------------------------------
    # 打分与分类
    # ------------------------------------------------------------------
    def analyze(self, material: Material, window: tuple[float, float]) -> Shot:
        start, end = window
        duration = max(0.1, material.duration_sec)
        position_ratio = start / duration
        has_real_file = material.probe_source != "mock" and Path(material.path).exists()
        use_frames = self._ffmpeg and has_real_file

        if use_frames:
            window_analysis = frames_mod.analyze_window(
                Path(material.path), start, end, sample_count=self.config.analysis.frames_per_shot
            )
            if window_analysis.available:
                score = quality.score_window(window_analysis)
                sharpness = window_analysis.mean("sharpness")
                brightness = window_analysis.mean("brightness")
                motion = window_analysis.motion
                analyzer = "heuristic"
                detail = dict(score.detail)
            else:
                score = quality.score_from_metadata(material, position_ratio)
                sharpness = brightness = motion = 0.0
                analyzer = "heuristic-metadata"
                detail = dict(score.detail)
        else:
            score = quality.score_from_metadata(material, position_ratio)
            sharpness = brightness = motion = 0.0
            analyzer = "heuristic-metadata"
            detail = dict(score.detail)

        shot_type = self._classify(
            position_ratio=position_ratio,
            shot_duration=end - start,
            score_quality=score.quality,
            sharpness=sharpness,
            brightness=brightness,
            motion=motion,
            detail=detail,
            is_first_window=start <= 0.01,
            is_last_window=end >= duration - 0.01,
            material=material,
        )

        detail.update({
            "position_ratio": round(position_ratio, 3),
            "shot_duration": round(end - start, 3),
        })

        return Shot(
            id="",
            material_id=material.id,
            material_path=material.path,
            start_sec=start,
            end_sec=end,
            shot_type=shot_type,
            quality_score=score.quality,
            aesthetic_score=score.aesthetic,
            description=self._describe(shot_type, position_ratio, end - start),
            analyzer=analyzer,
            score_detail=detail,
        )

    # ------------------------------------------------------------------
    def _classify(
        self,
        position_ratio: float,
        shot_duration: float,
        score_quality: float,
        sharpness: float,
        brightness: float,
        motion: float,
        detail: dict[str, float],
        is_first_window: bool = False,
        is_last_window: bool = False,
        material: Material | None = None,
    ) -> str:
        if self.config.selection.min_shot_sec and shot_duration < self.config.selection.min_shot_sec:
            return ShotType.INVALID
        if score_quality < self.config.selection.min_quality_score * 0.55:
            return ShotType.INVALID
        if brightness and brightness < 25:
            return ShotType.INVALID
        if detail.get("black_ratio", 0.0) > 0.6:
            return ShotType.INVALID
        if motion and motion > 22:
            return ShotType.INVALID

        frame_based = bool(sharpness)
        if frame_based:
            # 有帧统计：按位置 + 清晰度判定
            if position_ratio <= HOOK_ZONE and score_quality >= 55:
                return ShotType.HOOK
            if position_ratio >= FINISH_ZONE and score_quality >= 45:
                return ShotType.FINISH
            if shot_duration <= DETAIL_MAX_DURATION and sharpness >= 18:
                return ShotType.DETAIL
            return ShotType.PROCESS

        # 无帧统计（元数据启发式 / mock）：按「片段在素材中的位置 + 时长」判定
        # 依据经验：素材开头常是场景交代，结尾常是完工展示，短片段多为细节特写
        if is_first_window and score_quality >= 55:
            return ShotType.HOOK
        if is_last_window and score_quality >= 45:
            return ShotType.FINISH
        if shot_duration <= DETAIL_MAX_DURATION:
            return ShotType.DETAIL
        if material is not None and self._pseudo_unit(material, position_ratio) < 0.25:
            return ShotType.DETAIL
        return ShotType.PROCESS

    @staticmethod
    def _pseudo_unit(material: Material, position_ratio: float) -> float:
        """由素材指纹 + 位置派生的确定性伪随机数，保证多次运行结果一致。"""
        seed = f"{material.checksum or material.name}|{position_ratio:.3f}"
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF

    @staticmethod
    def _describe(shot_type: str, position_ratio: float, shot_duration: float) -> str:
        mapping = {
            ShotType.HOOK: "开场大场景（素材起始段）",
            ShotType.PROCESS: "铺装过程片段",
            ShotType.DETAIL: "铺装细节特写",
            ShotType.FINISH: "完工整体效果",
            ShotType.INVALID: "无效镜头（过短/过暗/晃动）",
        }
        base = mapping.get(shot_type, "镜头片段")
        return f"{base}｜位置 {position_ratio:.0%}｜时长 {shot_duration:.1f}s"
