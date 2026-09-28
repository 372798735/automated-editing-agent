"""端到端流水线。

一次 run 的完整链路：

    discover → fetch(素材接入)
      → analyze(切分 + 打分 + 分类)
      → copywrite(模板 + 可选 LLM)
      → music(配乐匹配)
      → timeline(选片编排)
      → draft(剪映草稿) + validate
      → preview(HTML 故事板) + render script

产物统一落在 workspace/projects/<project_id>/：
    materials.json        素材清单
    shots.json            镜头清单（含打分依据）
    copywriting.json      文案
    timeline.json         时间线（可回放、可复现）
    selection_report.json 选片明细（为什么选它 / 为什么淘汰）
    draft/                剪映草稿目录（实际草稿在剪映草稿根目录）
    preview.html          分镜预览
    render/               ffmpeg 渲染脚本
    report.json           本次运行总报告
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .analysis import AnalyzeContext, build_analyzer
from .config import AppConfig
from .creative.brand import BrandKit
from .creative.copywriter import Copywriter
from .creative.music import MusicSelector, resolve_music_path
from .draft.builder import DraftBuilder, DraftResult
from .draft.validator import ValidationReport, validate
from .errors import AutoCutError
from .logging_setup import get_logger, set_trace_id
from .models import Material, PipelineResult, Project, Shot, Timeline
from .preview import render as render_preview
from .render.ffmpeg_script import build_plan, write_scripts
from .sources.base import MaterialSource
from .timeline import TimelineOrchestrator

logger = get_logger("pipeline")


@dataclass
class StageRecord:
    name: str
    status: str
    duration_ms: int
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "duration_ms": self.duration_ms, "detail": self.detail}


@dataclass
class RunArtifacts:
    project_dir: Path
    materials: list[Material] = field(default_factory=list)
    shots: list[Shot] = field(default_factory=list)
    timeline: Timeline | None = None
    draft: DraftResult | None = None
    validation: ValidationReport | None = None
    result: PipelineResult | None = None


class Pipeline:
    def __init__(
        self,
        config: AppConfig,
        source: MaterialSource,
        brand_kit: BrandKit | None = None,
        template_id: str | None = None,
        use_llm: bool | None = None,
        music_id: str = "",
    ) -> None:
        self.config = config
        self.source = source
        self.brand_kit = brand_kit or self._load_brand_kit(config)
        self.template_id = template_id
        self.use_llm = use_llm
        self.music_id = music_id

    @staticmethod
    def _load_brand_kit(config: AppConfig) -> BrandKit:
        path = config.resolve_path(config.creative.brand_kit_file)
        if path.exists():
            return BrandKit.load(path)
        logger.warning("未找到品牌套件 %s，使用内置默认样式", path)
        return BrandKit()

    # ------------------------------------------------------------------
    def run_project(self, project: Project) -> RunArtifacts:
        set_trace_id(project.id)
        stages: list[StageRecord] = []
        warnings: list[str] = []
        artifacts = RunArtifacts(project_dir=self.config.projects_dir / project.id)
        artifacts.project_dir.mkdir(parents=True, exist_ok=True)

        result = PipelineResult(
            project_id=project.id,
            project_name=project.name,
            status="RUNNING",
        )

        try:
            # 1) 素材接入
            stage_start = time.time()
            materials = self.source.fetch(project)
            stages.append(self._stage("ingest", stage_start, f"{len(materials)} 个素材"))
            artifacts.materials = materials
            self._write_json(artifacts.project_dir / "materials.json", [m.to_dict() for m in materials])
            if not materials:
                raise AutoCutError(f"项目「{project.name}」没有可用素材")

            # 2) 镜头分析
            stage_start = time.time()
            context = AnalyzeContext(
                config=self.config,
                project_id=project.id,
                project_name=project.name,
                variables=project.variables,
            )
            analyzer = build_analyzer(context)
            shots = analyzer.analyze_all(materials)
            stages.append(self._stage("analyze", stage_start, f"{len(shots)} 个候选镜头，引擎 {analyzer.name}"))
            warnings.extend(context.warnings)
            artifacts.shots = shots
            self._write_json(artifacts.project_dir / "shots.json", [s.to_dict() for s in shots])
            if not any(shot.shot_type != "invalid" for shot in shots):
                raise AutoCutError("所有镜头都被判定为无效，请检查素材质量或放宽 selection 阈值")

            # 3) 文案
            stage_start = time.time()
            copywriter = Copywriter(self.config)
            usable_count = len([s for s in shots if s.shot_type != "invalid"])
            copywriting = copywriter.generate(
                project_id=project.id,
                project_name=project.name,
                clip_count=min(usable_count, self.config.video.max_clips),
                month=project.month,
                extra_variables=project.variables,
                template_id=self.template_id,
                use_llm=self.use_llm,
            )
            stages.append(self._stage("copywrite", stage_start, f"生成器 {copywriting.generator}"))
            self._write_json(artifacts.project_dir / "copywriting.json", copywriting.to_dict())

            # 4) 配乐
            stage_start = time.time()
            selector = MusicSelector(self.config)
            music = selector.select(
                target_duration_sec=self.config.video.target_max_sec,
                craft=copywriting.variables.get("craft", ""),
                preferred_id=self.music_id,
            )
            if music is not None:
                # 解析成绝对路径：剪映草稿里必须写绝对路径才能找到音频文件
                music.path = str(resolve_music_path(self.config, music))
            stages.append(self._stage("music", stage_start, music.name if music else "未匹配到音乐"))

            # 5) 时间线编排
            stage_start = time.time()
            orchestrator = TimelineOrchestrator(self.config)
            outro_text = self.brand_kit.render_outro(copywriting.variables)
            timeline, selection_report = orchestrator.build(
                project_id=project.id,
                project_name=project.name,
                shots=shots,
                copywriting=copywriting,
                music=music,
                outro_text=outro_text,
                title=copywriting.title,
                title_duration_sec=self.brand_kit.title_duration_sec,
                outro_duration_sec=self.brand_kit.outro_duration_sec,
            )
            if not timeline.clips:
                raise AutoCutError("时间线为空")
            stages.append(self._stage("timeline", stage_start, f"{len(timeline.clips)} 个片段 / {timeline.total_duration_sec:.1f}s"))
            warnings.extend(selection_report.notes)
            artifacts.timeline = timeline
            self._write_json(artifacts.project_dir / "timeline.json", timeline.to_dict())
            self._write_json(artifacts.project_dir / "selection_report.json", selection_report.to_dict())

            # 6) 草稿生成 + 校验
            stage_start = time.time()
            builder = DraftBuilder(
                output_root=self.config.resolved_draft_root,
                brand_kit=self.brand_kit,
                profile_key=self.config.draft.schema_profile,
            )
            material_durations = {m.path: m.duration_sec for m in materials}
            draft = builder.build(timeline, overwrite=True, material_durations=material_durations)
            warnings.extend(draft.warnings)
            content = json.loads(draft.content_path.read_text(encoding="utf-8"))
            validation = validate(content)
            artifacts.draft = draft
            artifacts.validation = validation
            self._write_json(artifacts.project_dir / "draft_validation.json", validation.to_dict())
            stages.append(self._stage("draft", stage_start, validation.summary()))
            if not validation.ok:
                raise AutoCutError(
                    "草稿校验未通过：" + "；".join(issue.message for issue in validation.errors[:3])
                )

            # 7) 预览与渲染脚本
            stage_start = time.time()
            preview_path = self.config.projects_dir / project.id / "preview.html"
            if self.config.draft.write_preview_html:
                render_preview(
                    timeline,
                    preview_path,
                    selection_report=selection_report.to_dict(),
                    warnings=warnings,
                )
            render_script_path = ""
            if self.config.draft.emit_render_script:
                plan = build_plan(timeline)
                scripts = write_scripts(plan, self.config.projects_dir / project.id / "render")
                render_script_path = scripts["powershell"]
                warnings.extend(plan.warnings)
            stages.append(self._stage("export", stage_start, "预览与渲染脚本"))

            result.status = "DRAFT_READY"
            result.duration_sec = timeline.total_duration_sec
            result.clip_count = len(timeline.clips)
            result.material_count = len(materials)
            result.shot_count = len(shots)
            result.selected_count = len(timeline.selected_shot_ids)
            result.draft_path = str(draft.draft_dir)
            result.preview_path = str(preview_path)
            result.render_script_path = render_script_path
            result.timeline_path = str(artifacts.project_dir / "timeline.json")

        except AutoCutError as exc:
            logger.error("项目「%s」处理失败: %s", project.name, exc)
            result.status = "FAILED"
            result.errors.append(str(exc))
        except Exception as exc:  # pragma: no cover - 兜底
            logger.exception("项目「%s」出现未预期错误", project.name)
            result.status = "FAILED"
            result.errors.append(f"未预期错误: {exc}")

        result.stages = [stage.to_dict() for stage in stages]
        result.warnings = collapse_warnings(list(dict.fromkeys(warnings)))
        artifacts.result = result
        self._write_json(artifacts.project_dir / "report.json", result.to_dict())
        return artifacts

    # ------------------------------------------------------------------
    @staticmethod
    def _stage(name: str, started: float, detail: str) -> StageRecord:
        elapsed = int((time.time() - started) * 1000)
        logger.info("阶段 %s 完成（%s ms）：%s", name, elapsed, detail)
        return StageRecord(name=name, status="ok", duration_ms=elapsed, detail=detail)

    @staticmethod
    def _write_json(path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# 同类告警达到该数量时折叠为一条汇总，避免刷屏埋掉真正重要的问题
WARNING_COLLAPSE_THRESHOLD = 3

# 每组：(归组用的前缀, 折叠后的汇总文案模板, 折叠后的行动建议)
# 汇总文案里说明「有多少条、是什么、怎么办」，比单纯丢一条计数有用得多。
_WARNING_GROUPS = (
    (
        "素材文件不存在，草稿中需手动重新链接",
        "草稿引用了 {count} 个当前不可访问的素材文件",
        "剪映打开草稿后会提示素材丢失，需手动重新链接；mock 演示模式下属正常现象",
    ),
    (
        "素材不存在，渲染会失败",
        "{count} 个素材当前不可访问，渲染脚本尚不能执行",
        "等真实素材就位后再跑一次，或改用 --source local 指向实际素材目录",
    ),
)


def collapse_warnings(warnings: list[str]) -> list[str]:
    """把重复度高的告警折叠成「计数 + 原因 + 怎么办」，其余告警保留原文。

    只保留一条示例路径，既避免刷屏，又不至于让用户无从排查。
    """
    result: list[str] = []
    for marker, template, hint in _WARNING_GROUPS:
        matched = [w for w in warnings if w.startswith(marker)]
        if len(matched) >= WARNING_COLLAPSE_THRESHOLD:
            result.append(f"{template.format(count=len(matched))} —— {hint}")
            result.append(f"（示例路径）{matched[0]}")
            warnings = [w for w in warnings if not w.startswith(marker)]
    return result + warnings


def run_batch(
    config: AppConfig,
    source: MaterialSource,
    template_id: str | None = None,
    use_llm: bool | None = None,
    music_id: str = "",
) -> list[PipelineResult]:
    pipeline = Pipeline(config, source, template_id=template_id, use_llm=use_llm, music_id=music_id)
    projects = source.discover()
    if not projects:
        logger.warning("没有发现任何项目")
        return []

    results: list[PipelineResult] = []
    for project in projects:
        artifacts = pipeline.run_project(project)
        if artifacts.result:
            results.append(artifacts.result)
    summary = {
        "total": len(results),
        "succeeded": len([r for r in results if r.status == "DRAFT_READY"]),
        "failed": len([r for r in results if r.status != "DRAFT_READY"]),
        "projects": [r.to_dict() for r in results],
    }
    path = config.workspace_path / "batch_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "批量完成：成功 %s / 失败 %s，汇总报告 %s",
        summary["succeeded"], summary["failed"], path,
    )
    return results
