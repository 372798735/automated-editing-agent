"""FFmpeg 渲染脚本导出（V2 兜底通道的 MVP 形态）。

位置说明：主链路是「生成剪映草稿 → 人工精修 → 导出」，
但草稿 schema 依赖剪映版本，某些场景下用户希望**不打开剪映也能出片**，
因此这里把时间线翻译成可审阅的 ffmpeg 命令（PowerShell / bash 两版），
以及一份结构化的 render_plan.json。

刻意只生成脚本而不自动执行：渲染是重资源操作，交给人决定何时跑。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..logging_setup import get_logger
from ..models import Timeline

logger = get_logger("render")

WINDOWS_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


@dataclass
class RenderPlan:
    project_id: str
    project_name: str
    width: int
    height: int
    fps: float
    duration_sec: float
    clips: list[dict] = field(default_factory=list)
    music: dict | None = None
    subtitles: list[dict] = field(default_factory=list)
    font_path: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "canvas": {"width": self.width, "height": self.height, "fps": self.fps},
            "duration_sec": self.duration_sec,
            "clips": self.clips,
            "music": self.music,
            "subtitles": self.subtitles,
            "font_path": self.font_path,
            "warnings": self.warnings,
        }


def find_font() -> str:
    for candidate in WINDOWS_FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return ""


def _escape_drawtext(text: str) -> str:
    """drawtext 需要转义的特殊字符（含中文标点不改，只转义语法字符）。"""
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\u2019")
        .replace("%", "\\%")
    )


def build_plan(timeline: Timeline, include_subtitles: bool = True) -> RenderPlan:
    plan = RenderPlan(
        project_id=timeline.project_id,
        project_name=timeline.project_name,
        width=timeline.width,
        height=timeline.height,
        fps=timeline.fps,
        duration_sec=timeline.total_duration_sec,
        font_path=find_font(),
    )

    for clip in timeline.clips:
        path = Path(clip.material_path)
        if not path.exists():
            plan.warnings.append(f"素材不存在，渲染会失败：{clip.material_path}")
        plan.clips.append({
            "index": clip.index,
            "source": str(path).replace("\\", "/"),
            "source_start_sec": clip.source_start_sec,
            "duration_sec": clip.duration_sec,
            "target_start_sec": clip.target_start_sec,
            "shot_type": clip.shot_type,
            "volume": clip.volume,
        })
        if include_subtitles and clip.subtitle:
            plan.subtitles.append({
                "text": clip.subtitle,
                "start_sec": clip.target_start_sec,
                "end_sec": round(clip.target_start_sec + clip.duration_sec, 3),
            })

    if timeline.music:
        plan.music = {
            "path": timeline.music.path.replace("\\", "/"),
            "start_sec": timeline.music_start_sec,
            "volume": timeline.music_volume,
        }
        if not Path(timeline.music.path).exists():
            plan.warnings.append(f"音乐文件不存在：{timeline.music.path}")

    if include_subtitles and not plan.font_path:
        plan.warnings.append("未找到可用字体文件，渲染脚本将不含字幕烧录（草稿中字幕不受影响）")
    return plan


def _clip_commands(plan: RenderPlan) -> tuple[list[str], list[str]]:
    """逐片段裁剪 + 归一到竖屏画布。"""
    ps_commands: list[str] = []
    sh_commands: list[str] = []
    for clip in plan.clips:
        vf = (
            f"scale={plan.width}:{plan.height}:force_original_aspect_ratio=increase,"
            f"crop={plan.width}:{plan.height},fps={plan.fps},setsar=1"
        )
        ps = (
            f'ffmpeg -hide_banner -loglevel error -y -ss {clip["source_start_sec"]} '
            f'-i "{clip["source"]}" -t {clip["duration_sec"]} -vf "{vf}" -an '
            f'-c:v libx264 -crf 20 -preset veryfast -pix_fmt yuv420p '
            f'"$workDir\\clip_{clip["index"]:03d}.mp4"'
        )
        sh = (
            f'ffmpeg -hide_banner -loglevel error -y -ss {clip["source_start_sec"]} '
            f'-i "{clip["source"]}" -t {clip["duration_sec"]} -vf "{vf}" -an '
            f'-c:v libx264 -crf 20 -preset veryfast -pix_fmt yuv420p '
            f'"$workDir/clip_{clip["index"]:03d}.mp4"'
        )
        ps_commands.append(ps)
        sh_commands.append(sh)
    return ps_commands, sh_commands


def _subtitle_filter(plan: RenderPlan) -> str:
    if not plan.subtitles or not plan.font_path:
        return ""
    font = plan.font_path.replace("\\", "/").replace(":", "\\:")
    parts = []
    for subtitle in plan.subtitles:
        text = _escape_drawtext(subtitle["text"])
        parts.append(
            f"drawtext=fontfile='{font}':text='{text}':fontcolor=white:fontsize={max(24, plan.width // 26)}:"
            f"borderw=3:bordercolor=black@0.7:x=(w-text_w)/2:y=h*0.78:"
            f"enable='between(t,{subtitle['start_sec']},{subtitle['end_sec']})'"
        )
    return ",".join(parts)


def write_scripts(plan: RenderPlan, output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ps_clips, sh_clips = _clip_commands(plan)
    concat_entries = "\n".join(
        f"file 'clip_{clip['index']:03d}.mp4'" for clip in plan.clips
    )
    subtitle_filter = _subtitle_filter(plan)

    if subtitle_filter:
        burn_ps = (
            f'ffmpeg -hide_banner -loglevel error -y -i "$workDir\\merged.mp4" '
            f'-vf "{subtitle_filter}" -c:v libx264 -crf 20 -preset veryfast -c:a copy '
            f'"$outDir\\{plan.project_name}_subtitled.mp4"'
        )
        burn_sh = (
            f'ffmpeg -hide_banner -loglevel error -y -i "$workDir/merged.mp4" '
            f'-vf "{subtitle_filter}" -c:v libx264 -crf 20 -preset veryfast -c:a copy '
            f'"$outDir/{plan.project_name}_subtitled.mp4"'
        )
    else:
        burn_ps = 'Write-Host "未包含字幕烧录步骤（缺少字体或无字幕）"'
        burn_sh = 'echo "未包含字幕烧录步骤（缺少字体或无字幕）"'

    music_step_ps = ""
    music_step_sh = ""
    if plan.music:
        music_step_ps = (
            f'ffmpeg -hide_banner -loglevel error -y -i "$workDir\\final.mp4" '
            f'-ss {plan.music["start_sec"]} -i "{plan.music["path"]}" '
            f'-map 0:v -map 1:a -shortest -c:v copy '
            f'-af "volume={plan.music["volume"]},afade=t=out:st={max(0, plan.duration_sec - 2)}:d=2" '
            f'"$outDir\\{plan.project_name}_final.mp4"'
        )
        music_step_sh = (
            f'ffmpeg -hide_banner -loglevel error -y -i "$workDir/final.mp4" '
            f'-ss {plan.music["start_sec"]} -i "{plan.music["path"]}" '
            f'-map 0:v -map 1:a -shortest -c:v copy '
            f'-af "volume={plan.music["volume"]},afade=t=out:st={max(0, plan.duration_sec - 2)}:d=2" '
            f'"$outDir/{plan.project_name}_final.mp4"'
        )

    ps_lines = [
        "# AutoCut 渲染脚本（PowerShell）",
        "# 用法：在本目录执行  powershell -ExecutionPolicy Bypass -File render.ps1",
        "$workDir = Join-Path $PSScriptRoot 'work'",
        "$outDir = Join-Path $PSScriptRoot 'out'",
        "New-Item -ItemType Directory -Force -Path $workDir | Out-Null",
        "New-Item -ItemType Directory -Force -Path $outDir | Out-Null",
        "",
        "# 1) 逐片段裁剪归一",
        *ps_clips,
        "",
        "# 2) 拼接",
        "Set-Content -Path (Join-Path $workDir 'concat.txt') -Value @'",
        concat_entries,
        "'@ -Encoding UTF8",
        f'ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$workDir\\concat.txt" -c copy "$workDir\\merged.mp4"',
        "",
        "# 3) 字幕烧录",
        burn_ps,
        "# 4) 对齐文件名（无字幕时直接复制）",
        f'if (Test-Path "$outDir\\{plan.project_name}_subtitled.mp4") {{ Move-Item -Force "$outDir\\{plan.project_name}_subtitled.mp4" "$workDir\\final.mp4" }} else {{ Copy-Item -Force "$workDir\\merged.mp4" "$workDir\\final.mp4" }}',
        "",
        "# 5) 混入背景音乐",
        music_step_ps or 'Write-Host "无背景音乐"',
        "",
        'Write-Host "渲染完成，产物在 $outDir"',
    ]

    sh_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'workDir="$(cd "$(dirname "$0")" && pwd)/work"',
        'outDir="$(cd "$(dirname "$0")" && pwd)/out"',
        'mkdir -p "$workDir" "$outDir"',
        "",
        "# 1) 逐片段裁剪归一",
        *sh_clips,
        "",
        "# 2) 拼接",
        f'cat > "$workDir/concat.txt" <<\'EOF\'',
        concat_entries,
        "EOF",
        'ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$workDir/concat.txt" -c copy "$workDir/merged.mp4"',
        "",
        "# 3) 字幕烧录",
        burn_sh,
        f'if [ -f "$outDir/{plan.project_name}_subtitled.mp4" ]; then mv -f "$outDir/{plan.project_name}_subtitled.mp4" "$workDir/final.mp4"; else cp -f "$workDir/merged.mp4" "$workDir/final.mp4"; fi',
        "",
        "# 4) 混入背景音乐",
        music_step_sh or 'echo "无背景音乐"',
        "",
        'echo "渲染完成，产物在 $outDir"',
    ]

    ps_path = output_dir / "render.ps1"
    sh_path = output_dir / "render.sh"
    plan_path = output_dir / "render_plan.json"
    ps_path.write_text("\n".join(ps_lines) + "\n", encoding="utf-8-sig")
    sh_path.write_text("\n".join(sh_lines) + "\n", encoding="utf-8")
    plan_path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("渲染脚本已导出: %s", output_dir)
    return {
        "powershell": str(ps_path),
        "bash": str(sh_path),
        "plan": str(plan_path),
    }
