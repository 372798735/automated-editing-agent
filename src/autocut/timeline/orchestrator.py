"""时间线编排器：把镜头集合变成一条可剪辑的成片时间线。

这是整个系统的「决策大脑」，也是唯一可以完全脱离 AI/剪映单测的模块。
编排规则（对应 PRD 3.2）：

    1. 开场：hook 类最高分镜头，且画面稳定（不超过 opening_sec）
    2. 主体：process 按素材拍摄顺序排列，detail 按比例穿插（保持节奏变化）
    3. 收尾：finish 类镜头 + 品牌落版
    4. 总时长裁剪到 [target_min, target_max]，优先保留高分镜头
    5. 同一素材默认最多使用 max_clips_per_material 个镜头，避免重复机位
    6. 镜头不足时按「逐级放宽阈值补足」，保证产出长度可用

所有决策都写进 timeline.selection_report（由上层落盘），便于复核。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import AppConfig
from ..logging_setup import get_logger
from ..models import Copywriting, MusicAsset, Shot, ShotType, Timeline, TimelineClip

logger = get_logger("timeline")

_NUM_PREFIX = re.compile(r"^(\d+)")


@dataclass
class SelectionReport:
    total_shots: int = 0
    usable_shots: int = 0
    selected: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    relax_level: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_shots": self.total_shots,
            "usable_shots": self.usable_shots,
            "selected_count": len(self.selected),
            "relax_level": self.relax_level,
            "notes": self.notes,
            "selected": self.selected,
            "dropped": self.dropped[:80],
        }


def _sort_key(material_path: str) -> tuple:
    """按文件名中的数字前缀排序，尽量还原拍摄顺序。"""
    name = material_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    match = _NUM_PREFIX.match(name)
    return (0, int(match.group(1))) if match else (1, name.lower())


def _trim_window(start: float, end: float, target: float) -> tuple[float, float]:
    """从镜头窗口里裁出目标时长（居中裁剪，尽量保留动作完整性）。"""
    length = end - start
    if length <= target:
        return start, end
    offset = (length - target) / 2
    return round(start + offset, 3), round(start + offset + target, 3)


class TimelineOrchestrator:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.video_cfg = config.video
        self.sel_cfg = config.selection

    # ------------------------------------------------------------------
    def build(
        self,
        project_id: str,
        project_name: str,
        shots: list[Shot],
        copywriting: Copywriting | None = None,
        music: MusicAsset | None = None,
        outro_text: str = "",
        title: str = "",
        title_duration_sec: float = 2.5,
        outro_duration_sec: float = 2.5,
        preferred_music_start: float = 0.0,
    ) -> tuple[Timeline, SelectionReport]:
        report = SelectionReport(total_shots=len(shots))
        usable = [shot for shot in shots if shot.shot_type != ShotType.INVALID]
        report.usable_shots = len(usable)

        hook = self._pick(usable, ShotType.HOOK, limit=1)
        finish = self._pick(usable, ShotType.FINISH, limit=1)
        detail_pool = self._pick(usable, ShotType.DETAIL, limit=6)
        process_pool = self._pick(usable, ShotType.PROCESS, limit=20)

        # 主体：过程镜头按拍摄顺序，特写按比例穿插
        process_pool.sort(key=lambda shot: (_sort_key(shot.material_path), shot.start_sec))
        body: list[Shot] = []
        detail_target = int(len(process_pool) * self.sel_cfg.detail_insert_ratio)
        detail_queue = list(detail_pool)
        for shot in process_pool:
            body.append(shot)
            if detail_queue and len([s for s in body if s.shot_type == ShotType.DETAIL]) < detail_target:
                body.append(detail_queue.pop(0))
        body.extend(detail_queue[:2])

        ordered: list[Shot] = []
        # 先放 hook / finish，保证开场与收尾镜头不会被同素材的过程镜头挤出名额
        ordered.extend(finish)
        ordered.extend(hook)
        ordered.extend(body)

        selected = self._fit_duration(ordered, usable, report)
        # 入选后重排：开场 → 主体 → 收尾
        selected = self._reorder(selected)
        if not selected:
            report.notes.append("没有可用镜头，无法生成时间线")

        clips, total = self._to_clips(selected, copywriting, has_music=music is not None)

        title_text = title or (copywriting.title if copywriting else "")
        timeline = Timeline(
            project_id=project_id,
            project_name=project_name,
            width=self.video_cfg.width,
            height=self.video_cfg.height,
            fps=self.video_cfg.fps,
            total_duration_sec=round(total, 3),
            clips=clips,
            music=music,
            music_start_sec=preferred_music_start,
            title=title_text,
            title_duration_sec=round(title_duration_sec, 3),
            outro_text=outro_text,
            outro_duration_sec=round(outro_duration_sec, 3),
            copywriting=copywriting,
            selected_shot_ids=[shot.id for shot in selected],
        )
        report.selected = [self._describe_shot(shot) for shot in selected]
        report.dropped = [
            self._describe_shot(shot) for shot in shots if shot.id not in timeline.selected_shot_ids
        ]
        logger.info(
            "时间线编排完成：选中 %s 个镜头，总时长 %.1fs", len(clips), timeline.total_duration_sec
        )
        return timeline, report

    # ------------------------------------------------------------------
    def _pick(self, shots: list[Shot], shot_type: str, limit: int) -> list[Shot]:
        pool = [shot for shot in shots if shot.shot_type == shot_type]
        pool.sort(key=lambda shot: (-shot.final_score, shot.material_path, shot.start_sec))
        return pool[:limit]

    # ------------------------------------------------------------------
    @staticmethod
    def _reorder(selected: list[Shot]) -> list[Shot]:
        """把入选镜头排成「开场 → 主体 → 收尾」的叙事顺序。"""
        hooks = [shot for shot in selected if shot.shot_type == ShotType.HOOK]
        finishes = [shot for shot in selected if shot.shot_type == ShotType.FINISH]
        middle = [shot for shot in selected if shot.shot_type not in (ShotType.HOOK, ShotType.FINISH)]
        return hooks[:1] + middle + finishes[:1]

    def _assign_subtitles(self, shots: list[Shot], copywriting: Copywriting | None) -> list[str]:
        """按镜头语义分配字幕：开场用第一条、收尾用最后一条，中间顺次填充。

        纯按位置贴字幕会出现「完工镜头配细节处理文案」这种错位，
        这里把首尾两条文案锚定到 hook / finish 镜头上，错位概率明显下降。
        """
        count = len(shots)
        if count == 0:
            return []
        if copywriting is None or not copywriting.subtitles:
            return [""] * count
        source = list(copywriting.subtitles)

        result: list[str] = [""] * count
        hook_index = next((i for i, shot in enumerate(shots) if shot.shot_type == ShotType.HOOK), None)
        finish_index = next(
            (i for i in range(count - 1, -1, -1) if shots[i].shot_type == ShotType.FINISH), None
        )
        if hook_index is not None:
            result[hook_index] = source[0]
        if finish_index is not None and finish_index != hook_index:
            result[finish_index] = source[-1]

        middle_pool = source[1:-1] if (hook_index is not None and finish_index is not None) else source
        if not middle_pool:
            middle_pool = source
        cursor = 0
        for index in range(count):
            if result[index]:
                continue
            result[index] = middle_pool[cursor % len(middle_pool)]
            cursor += 1

        copywriting.subtitles = result
        return result

    def _fit_duration(
        self,
        ordered: list[Shot],
        usable: list[Shot],
        report: SelectionReport,
    ) -> list[Shot]:
        """按目标时长裁剪；不足时逐级放宽阈值补足。"""
        cfg = self.video_cfg
        selected: list[Shot] = []
        per_material: dict[str, int] = {}
        used: set[str] = set()
        total = 0.0

        def try_add(shot: Shot) -> bool:
            nonlocal total
            if shot.id in used:
                return False
            count = per_material.get(shot.material_id, 0)
            if count >= self.sel_cfg.max_clips_per_material:
                return False
            clip_len = min(shot.duration_sec, cfg.clip_max_sec)
            if clip_len < cfg.clip_min_sec:
                return False
            if len(selected) >= cfg.max_clips:
                return False
            if total + clip_len > cfg.target_max_sec and len(selected) >= cfg.min_clips:
                return False
            selected.append(shot)
            per_material[shot.material_id] = count + 1
            used.add(shot.id)
            total += clip_len
            return True

        for shot in ordered:
            try_add(shot)

        # 逐级放宽：降低最低分要求，从候选池里补足到 min_clips
        relax = 0
        while len(selected) < cfg.min_clips and self.sel_cfg.fallback_fill and relax < 3:
            relax += 1
            threshold = self.sel_cfg.min_quality_score * (1 - 0.2 * relax)
            extras = [
                shot for shot in usable
                if shot.id not in used and shot.final_score >= threshold
            ]
            extras.sort(key=lambda shot: -shot.final_score)
            for shot in extras:
                if len(selected) >= cfg.min_clips:
                    break
                try_add(shot)
            report.relax_level = relax
        if relax:
            report.notes.append(f"镜头不足，已放宽最低分阈值 {relax} 级补足到 {len(selected)} 个镜头")

        if len(selected) < cfg.min_clips:
            report.notes.append(
                f"可用镜头仅 {len(selected)} 个（少于建议的 {cfg.min_clips} 个），成片偏短"
            )
        return selected

    def _to_clips(
        self,
        shots: list[Shot],
        copywriting: Copywriting | None,
        has_music: bool = False,
    ) -> tuple[list[TimelineClip], float]:
        clips: list[TimelineClip] = []
        cursor = 0.0
        subtitles = self._assign_subtitles(shots, copywriting)
        for index, shot in enumerate(shots):
            clip_len = max(self.video_cfg.clip_min_sec, min(shot.duration_sec, self.video_cfg.clip_max_sec))
            start, end = _trim_window(shot.start_sec, shot.end_sec, clip_len)
            subtitle = subtitles[index] if index < len(subtitles) else ""
            # 配了音乐时压低原声（保留一点施工现场感），没配乐则保留原声
            if not has_music:
                volume = 1.0
            else:
                volume = 0.15 if shot.shot_type == ShotType.PROCESS else 0.0
            clips.append(TimelineClip(
                index=index,
                material_id=shot.material_id,
                material_path=shot.material_path,
                source_start_sec=round(start, 3),
                duration_sec=round(end - start, 3),
                target_start_sec=round(cursor, 3),
                shot_type=shot.shot_type,
                subtitle=subtitle,
                transition=self.video_cfg.transition if index > 0 else "",
                volume=volume,
            ))
            cursor += end - start
        return clips, cursor

    @staticmethod
    def _describe_shot(shot: Shot) -> dict:
        return {
            "shot_id": shot.id,
            "material": shot.material_path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1],
            "range": f"{shot.start_sec:.1f}-{shot.end_sec:.1f}s",
            "type": shot.shot_type,
            "quality": shot.quality_score,
            "aesthetic": shot.aesthetic_score,
            "final_score": shot.final_score,
            "description": shot.description,
            "analyzer": shot.analyzer,
        }
