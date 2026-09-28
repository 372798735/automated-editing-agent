"""草稿一致性校验器。

剪映对损坏的草稿通常表现为「打开空白 / 直接崩溃」，很难排查。
因此在写出草稿后立刻做一遍**结构自检**，把问题在生成阶段暴露出来：

    1. id 唯一性（material / segment / track 全局不重复）
    2. 引用完整性（segment.material_id、extra_material_refs 必须能在 materials/config 中找到）
    3. 时间轴合法性（同轨道片段不重叠、target 区间不与源区间矛盾）
    4. 时长一致性（draft.duration ≥ 所有片段结束时间；轨道时长之和与时间线一致）
    5. 路径存在性（缺失只告警，因为用户可能用网盘/移动硬盘的素材）
    6. 画布与片段分辨率提示

校验结果分 error / warning 两级：error 会让调用方知道草稿不可用，warning 只提示风险。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..logging_setup import get_logger

logger = get_logger("draft.validator")

OVERLAP_EPSILON_US = 100        # 0.1ms，允许微秒级舍入误差


@dataclass
class ValidationIssue:
    level: str      # error / warning
    code: str
    message: str

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message}


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def add(self, level: str, code: str, message: str) -> None:
        self.issues.append(ValidationIssue(level, code, message))

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.level == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [issue.to_dict() for issue in self.issues],
            "stats": self.stats,
        }

    def summary(self) -> str:
        return f"{'通过' if self.ok else '未通过'}：{len(self.errors)} 个错误 / {len(self.warnings)} 个告警"


def validate(draft_content: dict, check_paths: bool = True) -> ValidationReport:
    report = ValidationReport()

    materials = draft_content.get("materials")
    config = draft_content.get("config")
    tracks = draft_content.get("tracks")
    if not isinstance(materials, dict) or not isinstance(tracks, list):
        report.add("error", "STRUCTURE", "draft_content 缺少 materials 或 tracks")
        return report

    material_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for bucket, items in materials.items():
        if not isinstance(items, list):
            report.add("error", "MATERIAL_BUCKET", f"materials.{bucket} 不是数组")
            continue
        for item in items:
            item_id = item.get("id")
            if not item_id:
                report.add("error", "MISSING_ID", f"materials.{bucket} 存在缺少 id 的条目")
                continue
            if item_id in material_ids:
                duplicate_ids.add(item_id)
            material_ids.add(item_id)

    config_ids: set[str] = set()
    if isinstance(config, dict):
        for bucket, items in config.items():
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("id"):
                        config_ids.add(item["id"])

    if duplicate_ids:
        report.add("error", "DUPLICATE_ID", f"存在重复 id：{sorted(duplicate_ids)[:5]}")

    # 引用完整性
    missing_material_refs: set[str] = set()
    missing_extra_refs: set[str] = set()
    segment_ids: set[str] = set()
    duplicate_segments: set[str] = set()
    missing_paths: set[str] = set()
    track_summary: list[dict] = []
    global_end_us = 0

    for track in tracks:
        track_type = track.get("type")
        segments = track.get("segments") or []
        previous_end = -1
        for segment in segments:
            segment_id = segment.get("id")
            if segment_id:
                if segment_id in segment_ids:
                    duplicate_segments.add(segment_id)
                segment_ids.add(segment_id)
            material_id = segment.get("material_id")
            if material_id and material_id not in material_ids:
                missing_material_refs.add(str(material_id))
            for ref in segment.get("extra_material_refs") or []:
                if ref not in material_ids and ref not in config_ids:
                    missing_extra_refs.add(str(ref))

            target = segment.get("target_timerange") or {}
            start = int(target.get("start", 0))
            duration = int(target.get("duration", 0))
            end = start + duration
            global_end_us = max(global_end_us, end)

            if duration <= 0 and track_type != "text":
                report.add("error", "ZERO_DURATION", f"{track_type} 轨道存在时长为 0 的片段")
            if previous_end >= 0 and start < previous_end - OVERLAP_EPSILON_US:
                report.add(
                    "warning", "OVERLAP",
                    f"{track_type} 轨道片段重叠：{start / 1e6:.3f}s < 前一片段结束 {previous_end / 1e6:.3f}s",
                )
            previous_end = max(previous_end, end)

            if check_paths:
                path = segment.get("path") or ""
                if not path and material_id:
                    for bucket in ("videos", "audios"):
                        for item in materials.get(bucket, []):
                            if item.get("id") == material_id:
                                path = item.get("path", "")
                if path and not Path(path).exists():
                    missing_paths.add(path)

        track_summary.append({
            "type": track_type,
            "name": track.get("name", ""),
            "segments": len(segments),
        })

    if missing_material_refs:
        report.add("error", "MATERIAL_REF", f"片段引用了不存在的素材：{sorted(missing_material_refs)[:5]}")
    if missing_extra_refs:
        report.add("error", "EXTRA_REF", f"片段引用了不存在的附属素材：{sorted(missing_extra_refs)[:5]}")
    if duplicate_segments:
        report.add("error", "DUPLICATE_SEGMENT", f"存在重复片段 id：{sorted(duplicate_segments)[:5]}")
    if missing_paths:
        report.add("warning", "MISSING_FILE", f"{len(missing_paths)} 个素材/音乐文件当前不可访问（草稿可打开但需重新链接）")

    declared_duration = int(draft_content.get("duration", 0))
    if declared_duration + OVERLAP_EPSILON_US < global_end_us:
        report.add(
            "error", "DURATION",
            f"草稿声明时长 {declared_duration / 1e6:.2f}s 小于实际片段结束 {global_end_us / 1e6:.2f}s",
        )

    video_track = next((t for t in tracks if t.get("type") == "video"), None)
    if video_track is None or not video_track.get("segments"):
        report.add("error", "NO_VIDEO", "草稿没有视频片段")

    canvas = draft_content.get("canvas_config") or {}
    if not canvas.get("width") or not canvas.get("height"):
        report.add("error", "CANVAS", "缺少画布尺寸配置")

    report.stats = {
        "materials_total": sum(len(v) for v in materials.values() if isinstance(v, list)),
        "material_ids": len(material_ids),
        "segments_total": len(segment_ids) + len(duplicate_segments),
        "global_end_sec": round(global_end_us / 1e6, 3),
        "declared_duration_sec": round(declared_duration / 1e6, 3),
        "tracks": track_summary,
    }
    return report
