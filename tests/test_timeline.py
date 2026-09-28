"""时间线编排单测：镜头顺序、时长约束、去重、字幕对齐。"""

from __future__ import annotations

import unittest

from helpers import make_shot

from autocut.config import AppConfig
from autocut.models import Copywriting, ShotType
from autocut.timeline import TimelineOrchestrator


def config(**overrides) -> AppConfig:
    return AppConfig.load(None, overrides or None)


class TimelineOrchestratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.orchestrator = TimelineOrchestrator(config())

    def _shots(self):
        return [
            make_shot("s-hook", "m1", "D:/mats/001.mp4", 0.0, 4.0, ShotType.HOOK, quality=88),
            make_shot("s-p1", "m1", "D:/mats/001.mp4", 6.0, 11.0, ShotType.PROCESS, quality=76),
            make_shot("s-p2", "m2", "D:/mats/002.mp4", 2.0, 8.0, ShotType.PROCESS, quality=72),
            make_shot("s-p3", "m3", "D:/mats/003.mp4", 3.0, 9.0, ShotType.PROCESS, quality=70),
            make_shot("s-p4", "m4", "D:/mats/004.mp4", 4.0, 10.0, ShotType.PROCESS, quality=68),
            make_shot("s-d1", "m5", "D:/mats/005.mp4", 1.0, 4.0, ShotType.DETAIL, quality=74),
            make_shot("s-d2", "m6", "D:/mats/006.mp4", 2.0, 5.0, ShotType.DETAIL, quality=71),
            make_shot("s-fin", "m7", "D:/mats/007.mp4", 8.0, 14.0, ShotType.FINISH, quality=82),
            make_shot("s-bad", "m8", "D:/mats/008.mp4", 0.0, 2.0, ShotType.INVALID, quality=10),
        ]

    def test_hook_first_finish_last_and_invalid_excluded(self) -> None:
        copywriting = Copywriting(project_id="p", title="测试标题", subtitles=[f"字幕{i}" for i in range(20)])
        timeline, report = self.orchestrator.build("p", "测试项目", self._shots(), copywriting=copywriting)

        self.assertTrue(timeline.clips)
        self.assertEqual(timeline.clips[0].shot_type, ShotType.HOOK)
        self.assertEqual(timeline.clips[-1].shot_type, ShotType.FINISH)
        self.assertNotIn(ShotType.INVALID, [clip.shot_type for clip in timeline.clips])
        self.assertNotIn("s-bad", timeline.selected_shot_ids)
        self.assertEqual(report.total_shots, 9)

    def test_clip_duration_within_limits(self) -> None:
        timeline, _ = self.orchestrator.build("p", "测试项目", self._shots())
        cfg = self.orchestrator.video_cfg
        for clip in timeline.clips:
            self.assertGreaterEqual(clip.duration_sec, cfg.clip_min_sec - 1e-6)
            self.assertLessEqual(clip.duration_sec, cfg.clip_max_sec + 1e-6)

    def test_total_duration_within_target_and_timeline_contiguous(self) -> None:
        timeline, _ = self.orchestrator.build("p", "测试项目", self._shots())
        cfg = self.orchestrator.video_cfg
        self.assertLessEqual(timeline.total_duration_sec, cfg.target_max_sec + 1e-6)
        cursor = 0.0
        for clip in timeline.clips:
            self.assertAlmostEqual(clip.target_start_sec, cursor, places=3)
            cursor += clip.duration_sec
        self.assertAlmostEqual(timeline.total_duration_sec, cursor, places=3)

    def test_max_clips_per_material_respected(self) -> None:
        timeline, _ = self.orchestrator.build("p", "测试项目", self._shots())
        counts: dict[str, int] = {}
        for clip in timeline.clips:
            counts[clip.material_id] = counts.get(clip.material_id, 0) + 1
        limit = self.orchestrator.sel_cfg.max_clips_per_material
        for material_id, count in counts.items():
            self.assertLessEqual(count, limit, f"素材 {material_id} 使用 {count} 次，超出上限 {limit}")

    def test_subtitles_align_with_clips_and_anchor_to_hook_and_finish(self) -> None:
        copywriting = Copywriting(project_id="p", title="标题", subtitles=["第一条", "第二条", "第三条"])
        timeline, _ = self.orchestrator.build("p", "测试项目", self._shots(), copywriting=copywriting)
        # 字幕数量与最终入选镜头数一致
        self.assertEqual(len(timeline.clips), len(copywriting.subtitles))
        self.assertTrue(all(clip.subtitle for clip in timeline.clips))
        # 开场镜头配第一条，收尾镜头配最后一条
        self.assertEqual(timeline.clips[0].subtitle, "第一条")
        self.assertEqual(timeline.clips[-1].subtitle, "第三条")

    def test_fallback_fill_when_shots_scarce(self) -> None:
        shots = [
            make_shot("s-hook", "m1", "D:/mats/001.mp4", 0.0, 4.0, ShotType.HOOK, quality=60),
            make_shot("s-p1", "m2", "D:/mats/002.mp4", 0.0, 5.0, ShotType.PROCESS, quality=50),
        ]
        timeline, report = self.orchestrator.build("p", "镜头不足项目", shots)
        self.assertTrue(timeline.clips)
        self.assertTrue(any("偏短" in note or "放宽" in note for note in report.notes))

    def test_empty_shots_produces_empty_timeline(self) -> None:
        timeline, report = self.orchestrator.build("p", "空项目", [])
        self.assertEqual(timeline.clips, [])
        self.assertEqual(timeline.total_duration_sec, 0.0)
        self.assertTrue(report.notes)


if __name__ == "__main__":
    unittest.main()
