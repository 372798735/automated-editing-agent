"""草稿生成器 + 校验器单测。

这是 MVP 里最容易「悄悄坏掉」的一环：草稿 JSON 结构错一个字段，剪映就会打开空白。
所以这里既验证结构，也验证校验器能抓住人为破坏。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import make_shot, write_minimal_mp4

from autocut.config import AppConfig
from autocut.creative.brand import BrandKit, TextStyle
from autocut.draft.builder import DraftBuilder
from autocut.draft.validator import validate
from autocut.models import Copywriting, MusicAsset, ShotType
from autocut.timeline import TimelineOrchestrator


class DraftBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.materials = []
        for index in range(1, 6):
            path = write_minimal_mp4(self.root / f"{index:03d}.mp4", duration_ms=20000)
            self.materials.append(path)

        brand = BrandKit(
            title_style=TextStyle(font_size=12, position_y=-0.55),
            subtitle_style=TextStyle(font_size=8, position_y=0.62),
            outro_text="专业地板铺装｜免费上门量尺",
        )
        self.builder = DraftBuilder(self.root / "drafts", brand_kit=brand)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _timeline(self):
        shots = [
            make_shot("s-hook", "m1", str(self.materials[0]), 0.0, 4.0, ShotType.HOOK, 88),
            make_shot("s-p1", "m2", str(self.materials[1]), 2.0, 8.0, ShotType.PROCESS, 75),
            make_shot("s-d1", "m3", str(self.materials[2]), 1.0, 4.0, ShotType.DETAIL, 70),
            make_shot("s-p2", "m4", str(self.materials[3]), 3.0, 9.0, ShotType.PROCESS, 68),
            make_shot("s-fin", "m5", str(self.materials[4]), 6.0, 12.0, ShotType.FINISH, 80),
        ]
        copywriting = Copywriting(
            project_id="demo",
            title="阳光花园全屋通铺施工现场记录",
            subtitles=["开工找平", "锁扣拼接", "缝隙细节", "收边处理", "完工效果"],
            hashtags=["地板铺装", "装修实拍"],
        )
        music = MusicAsset(id="bgm-1", name="clean_01.mp3", path=str(self.materials[0]), duration_sec=60.0, bpm=110, mood="clean")
        orchestrator = TimelineOrchestrator(AppConfig.load(None, None))
        timeline, _ = orchestrator.build(
            project_id="demo",
            project_name="阳光花园-张先生-全屋通铺",
            shots=shots,
            copywriting=copywriting,
            music=music,
            outro_text="专业地板铺装｜免费上门量尺",
            title=copywriting.title,
        )
        return timeline

    # ------------------------------------------------------------------
    def test_build_creates_valid_draft(self) -> None:
        timeline = self._timeline()
        result = self.builder.build(timeline, material_durations={str(p): 20.0 for p in self.materials})

        self.assertTrue(result.content_path.exists())
        self.assertTrue(result.meta_path.exists())
        content = json.loads(result.content_path.read_text(encoding="utf-8"))
        report = validate(content)
        self.assertTrue(report.ok, msg=json.dumps(report.to_dict(), ensure_ascii=False))
        self.assertEqual(result.video_count, len(timeline.clips))
        self.assertGreater(result.text_count, 0)
        self.assertEqual(result.audio_count, 1)

    def test_draft_structure_fields(self) -> None:
        timeline = self._timeline()
        result = self.builder.build(timeline)
        content = json.loads(result.content_path.read_text(encoding="utf-8"))

        self.assertIn("canvas_config", content)
        self.assertEqual(content["canvas_config"]["width"], timeline.width)
        self.assertEqual(content["canvas_config"]["height"], timeline.height)
        self.assertEqual(len(content["tracks"]), 4)   # 视频 + 字幕 + 标题 + 音频
        track_types = [track["type"] for track in content["tracks"]]
        self.assertEqual(track_types.count("video"), 1)
        self.assertEqual(track_types.count("text"), 2)
        self.assertEqual(track_types.count("audio"), 1)

        video_track = content["tracks"][0]
        self.assertEqual(len(video_track["segments"]), len(timeline.clips))
        for segment, clip in zip(video_track["segments"], timeline.clips):
            self.assertAlmostEqual(segment["target_timerange"]["start"] / 1_000_000, clip.target_start_sec, places=3)
            self.assertAlmostEqual(segment["source_timerange"]["start"] / 1_000_000, clip.source_start_sec, places=3)

    def test_duration_covers_all_segments(self) -> None:
        timeline = self._timeline()
        result = self.builder.build(timeline)
        content = json.loads(result.content_path.read_text(encoding="utf-8"))
        global_end = max(
            segment["target_timerange"]["start"] + segment["target_timerange"]["duration"]
            for track in content["tracks"] for segment in track["segments"]
        )
        self.assertGreaterEqual(content["duration"], global_end)

    def test_meta_info_contains_draft_files(self) -> None:
        timeline = self._timeline()
        result = self.builder.build(timeline)
        meta = json.loads(result.meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["draft_name"], result.draft_name)
        self.assertTrue(meta["draft_json_file"].endswith("draft_content.json"))
        self.assertGreater(len(meta["draft_materials"]), 0)

    def test_validator_catches_broken_reference(self) -> None:
        timeline = self._timeline()
        result = self.builder.build(timeline)
        content = json.loads(result.content_path.read_text(encoding="utf-8"))
        content["tracks"][0]["segments"][0]["material_id"] = "NOT-EXISTING-ID"
        report = validate(content)
        self.assertFalse(report.ok)
        self.assertTrue(any(issue.code == "MATERIAL_REF" for issue in report.errors))

    def test_validator_catches_duration_shortfall(self) -> None:
        timeline = self._timeline()
        result = self.builder.build(timeline)
        content = json.loads(result.content_path.read_text(encoding="utf-8"))
        content["duration"] = 1000
        report = validate(content)
        self.assertFalse(report.ok)
        self.assertTrue(any(issue.code == "DURATION" for issue in report.errors))

    def test_validator_reports_missing_files_as_warning(self) -> None:
        timeline = self._timeline()
        for clip in timeline.clips:
            clip.material_path = str(self.root / "not-exists.mp4")
        result = self.builder.build(timeline)
        content = json.loads(result.content_path.read_text(encoding="utf-8"))
        report = validate(content)
        self.assertTrue(any(issue.code == "MISSING_FILE" for issue in report.warnings))
        self.assertTrue(result.warnings)

    def test_rejects_empty_timeline(self) -> None:
        from autocut.errors import DraftBuildError
        from autocut.models import Timeline

        with self.assertRaises(DraftBuildError):
            self.builder.build(Timeline(project_id="x", project_name="空"))


if __name__ == "__main__":
    unittest.main()
