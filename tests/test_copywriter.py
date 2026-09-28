"""文案引擎单测：变量抽取、禁用词过滤、字幕对齐、模板选择。"""

from __future__ import annotations

import unittest

from autocut.config import AppConfig
from autocut.creative.copywriter import Copywriter, extract_variables


class VariableExtractionTest(unittest.TestCase):
    def test_typical_floor_project_name(self) -> None:
        variables = extract_variables("阳光花园-张先生-全屋通铺", month="2026-09")
        self.assertEqual(variables["community"], "阳光花园")
        self.assertEqual(variables["customer"], "张先生")
        self.assertEqual(variables["craft"], "全屋通铺")
        self.assertEqual(variables["month"], "2026-09")

    def test_name_without_customer(self) -> None:
        variables = extract_variables("星河湾-人字拼", month="2026-09")
        self.assertEqual(variables["community"], "星河湾")
        self.assertEqual(variables["customer"], "业主")
        self.assertEqual(variables["craft"], "人字拼")

    def test_name_with_spaces_and_underscores(self) -> None:
        variables = extract_variables("翠湖天地 李女士 实木多层")
        self.assertEqual(variables["community"], "翠湖天地")
        self.assertEqual(variables["customer"], "李女士")
        self.assertEqual(variables["craft"], "实木多层")

    def test_extra_variables_override(self) -> None:
        variables = extract_variables("A-B", extra={"brand": "木言地板", "area": "120平"})
        self.assertEqual(variables["brand"], "木言地板")
        self.assertEqual(variables["area"], "120平")


class CopywriterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.writer = Copywriter(AppConfig.load(None, None))

    def test_generate_fills_all_placeholders(self) -> None:
        copywriting = self.writer.generate(
            project_id="p1",
            project_name="阳光花园-张先生-全屋通铺",
            clip_count=6,
            month="2026-09",
        )
        self.assertNotIn("{", copywriting.title)
        self.assertTrue(copywriting.subtitles)
        for subtitle in copywriting.subtitles:
            self.assertNotIn("{", subtitle)
        self.assertTrue(copywriting.title)
        self.assertEqual(copywriting.generator, "template")
        self.assertLessEqual(len(copywriting.hashtags), self.writer.config.creative.max_hashtags)

    def test_subtitles_count_matches_template(self) -> None:
        """文案引擎保持模板原样，字幕数量对齐由时间线编排器负责。"""
        copywriting = self.writer.generate("p1", "阳光花园-张先生-通铺", clip_count=20)
        template = self.writer.pick_template(None)
        self.assertEqual(len(copywriting.subtitles), len(template.subtitles))
        self.assertTrue(all(copywriting.subtitles))

    def test_clip_count_does_not_truncate_subtitles(self) -> None:
        copywriting = self.writer.generate("p1", "阳光花园-张先生-通铺", clip_count=2)
        template = self.writer.pick_template(None)
        self.assertEqual(len(copywriting.subtitles), len(template.subtitles))

    def test_banned_words_removed(self) -> None:
        writer = Copywriter(AppConfig.load(None, {"creative": {"banned_words": ["通铺"]}}))
        copywriting = writer.generate("p1", "阳光花园-张先生-全屋通铺", clip_count=3)
        joined = copywriting.title + "".join(copywriting.subtitles)
        self.assertNotIn("通铺", joined)

    def test_template_selection_by_id(self) -> None:
        copywriting = self.writer.generate("p1", "阳光花园-张先生-通铺", clip_count=4, template_id="floor_customer")
        self.assertEqual(copywriting.template_id, "floor_customer")

    def test_unknown_template_falls_back(self) -> None:
        copywriting = self.writer.generate("p1", "阳光花园-张先生-通铺", clip_count=4, template_id="not-exist")
        self.assertEqual(copywriting.template_id, self.writer.templates[0].id)

    def test_llm_disabled_by_default(self) -> None:
        copywriting = self.writer.generate("p1", "阳光花园-张先生-通铺", clip_count=4)
        self.assertEqual(copywriting.generator, "template")


if __name__ == "__main__":
    unittest.main()
