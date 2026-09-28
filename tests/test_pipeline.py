"""配置与端到端流水线（集成）测试。

集成测试用 mock 素材源，验证「素材接入 → 分析 → 文案 → 配乐 → 时间线 → 草稿 → 预览 → 渲染脚本」
全链路在没有任何第三方依赖、没有 ffmpeg、没有剪映的机器上也能跑通并产出可校验的产物。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import write_minimal_mp4

from autocut.config import AppConfig
from autocut.errors import ConfigError
from autocut.pipeline import Pipeline
from autocut.sources.base import SourceOptions
from autocut.sources.local import LocalSource
from autocut.sources.mock import MockSource


class ConfigTest(unittest.TestCase):
    def test_default_config_loads(self) -> None:
        config = AppConfig.load(None, None)
        self.assertEqual(config.video.width, 1080)
        self.assertEqual(config.video.height, 1920)
        self.assertEqual(config.draft.schema_profile, "jianying_5_9")

    def test_unknown_field_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            AppConfig.load(None, {"video": {"not_a_field": 1}})

    def test_invalid_duration_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            AppConfig.load(None, {"video": {"target_min_sec": 60, "target_max_sec": 30}})

    def test_overrides_merge(self) -> None:
        config = AppConfig.load(None, {"video": {"target_max_sec": 30}, "draft": {"draft_root": "X:/drafts"}})
        self.assertEqual(config.video.target_max_sec, 30)
        self.assertEqual(config.video.target_min_sec, 25.0)
        self.assertEqual(config.draft.draft_root, "X:/drafts")


class PipelineMockTest(unittest.TestCase):
    """用 mock 源验证全链路产物完备。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = AppConfig.load(None, {
            "workspace": str(self.root / "workspace"),
            "draft": {"draft_root": str(self.root / "drafts")},
        })

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_end_to_end_with_mock_source(self) -> None:
        source = MockSource(SourceOptions(), materials_root=self.config.materials_dir)
        pipeline = Pipeline(self.config, source)
        project = source.discover()[0]
        artifacts = pipeline.run_project(project)

        result = artifacts.result
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "DRAFT_READY", msg=f"错误: {result.errors} {result.warnings}")
        self.assertGreater(result.clip_count, 0)
        self.assertGreaterEqual(result.duration_sec, 1.0)

        # 产物齐备
        expected = ["materials.json", "shots.json", "copywriting.json", "timeline.json",
                    "selection_report.json", "report.json"]
        for name in expected:
            self.assertTrue((artifacts.project_dir / name).exists(), f"缺少产物 {name}")
        self.assertTrue(Path(result.draft_path).exists())
        self.assertTrue(Path(result.preview_path).exists())
        self.assertTrue(Path(result.render_script_path).exists())

        # 草稿内容自洽
        content = json.loads((Path(result.draft_path) / "draft_content.json").read_text(encoding="utf-8"))
        self.assertEqual(len(content["tracks"]), 4)
        validation = json.loads((artifacts.project_dir / "draft_validation.json").read_text(encoding="utf-8"))
        self.assertTrue(validation["ok"], msg=json.dumps(validation, ensure_ascii=False))

        # 时间线可回放
        timeline = json.loads((artifacts.project_dir / "timeline.json").read_text(encoding="utf-8"))
        self.assertEqual(len(timeline["clips"]), result.clip_count)
        self.assertTrue(timeline["copywriting"]["title"])

    def test_batch_processes_all_mock_projects(self) -> None:
        source = MockSource(SourceOptions(), materials_root=self.config.materials_dir)
        pipeline = Pipeline(self.config, source)
        statuses = []
        for project in source.discover():
            artifacts = pipeline.run_project(project)
            statuses.append(artifacts.result.status if artifacts.result else "FAILED")
        self.assertTrue(all(status == "DRAFT_READY" for status in statuses), msg=statuses)
        self.assertGreaterEqual(len(statuses), 1)

    def test_render_script_is_generated_with_expected_steps(self) -> None:
        source = MockSource(SourceOptions(), materials_root=self.config.materials_dir)
        pipeline = Pipeline(self.config, source)
        artifacts = pipeline.run_project(source.discover()[0])
        script = Path(artifacts.result.render_script_path).read_text(encoding="utf-8")
        self.assertIn("ffmpeg", script)
        self.assertIn("concat", script)


class PipelineLocalSourceTest(unittest.TestCase):
    """用真实的 MP4 元数据文件验证本地源 + 探测 + 分析链路。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.materials_root = self.root / "2026-09" / "翠湖天地-李女士-人字拼"
        self.materials_root.mkdir(parents=True)
        for index in range(1, 7):
            write_minimal_mp4(self.materials_root / f"VID_{index:03d}.mp4", duration_ms=15000 + index * 1000)
        (self.root / "2026-09" / "说明.txt").write_text("非素材文件，应被忽略", encoding="utf-8")
        self.config = AppConfig.load(None, {
            "workspace": str(self.root / "workspace"),
            "draft": {"draft_root": str(self.root / "drafts")},
        })

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_discover_and_run(self) -> None:
        source = LocalSource(self.root, SourceOptions(), materials_root=self.config.materials_dir)
        projects = source.discover()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].name, "翠湖天地-李女士-人字拼")
        self.assertEqual(projects[0].month, "2026-09")

        pipeline = Pipeline(self.config, source)
        artifacts = pipeline.run_project(projects[0])
        result = artifacts.result
        assert result is not None
        self.assertEqual(result.status, "DRAFT_READY", msg=f"{result.errors} {result.warnings}")
        self.assertEqual(result.material_count, 6)
        self.assertEqual(artifacts.materials[0].probe_source, "mp4box")

    def test_month_filter(self) -> None:
        source = LocalSource(self.root, SourceOptions(months=["2026-08"]), materials_root=self.config.materials_dir)
        self.assertEqual(source.discover(), [])

    def test_missing_root_raises(self) -> None:
        from autocut.errors import SourceError
        with self.assertRaises(SourceError):
            LocalSource(self.root / "not-exist", SourceOptions())


if __name__ == "__main__":
    unittest.main()
