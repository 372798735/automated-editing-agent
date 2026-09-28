"""HTML 故事板预览：把时间线渲染成一页可读的「分镜表」。

用途：
- 运营在打开剪映之前就能判断这条片子对不对（镜头顺序、字幕、配乐、时长）；
- 出问题时是排查依据（每个镜头喂了哪个素材、哪一段、多少分、谁判的）；
- 有 ffmpeg 时自动抽缩略图，没有时用占位块，页面同样可读。
"""

from __future__ import annotations

import html
from pathlib import Path

from .logging_setup import get_logger
from .media import frames as frames_mod
from .models import ShotType, Timeline

logger = get_logger("preview")

SHOT_LABELS = {
    ShotType.HOOK: "开场钩子",
    ShotType.PROCESS: "铺装过程",
    ShotType.DETAIL: "细节特写",
    ShotType.FINISH: "完工效果",
    ShotType.INVALID: "无效镜头",
}

SHOT_COLORS = {
    ShotType.HOOK: "#185FA5",
    ShotType.PROCESS: "#0F6E56",
    ShotType.DETAIL: "#BA7517",
    ShotType.FINISH: "#534AB7",
    ShotType.INVALID: "#888780",
}

SHOT_BG = {
    ShotType.HOOK: "#E6F1FB",
    ShotType.PROCESS: "#E1F5EE",
    ShotType.DETAIL: "#FAEEDA",
    ShotType.FINISH: "#EEEDFE",
    ShotType.INVALID: "#F1EFE8",
}


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _fmt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    return f"{int(seconds // 60):02d}:{seconds % 60:04.1f}"


def render(
    timeline: Timeline,
    output_path: Path,
    selection_report: dict | None = None,
    warnings: list[str] | None = None,
    with_thumbnails: bool = True,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    thumb_dir = output_path.parent / (output_path.stem + "_thumbs")
    warnings = list(warnings or [])

    clips_html: list[str] = []
    bar_html: list[str] = []
    for clip in timeline.clips:
        color = SHOT_COLORS.get(clip.shot_type, "#888780")
        background = SHOT_BG.get(clip.shot_type, "#F1EFE8")
        label = SHOT_LABELS.get(clip.shot_type, clip.shot_type)
        thumb_html = '<div class="thumb placeholder">无缩略图</div>'
        if with_thumbnails and frames_mod.ffmpeg_available() and Path(clip.material_path).exists():
            thumb_path = thumb_dir / f"clip_{clip.index:03d}.jpg"
            mid = clip.source_start_sec + clip.duration_sec / 2
            if frames_mod.extract_thumbnail(Path(clip.material_path), mid, thumb_path, width=320):
                thumb_html = f'<img class="thumb" src="{_esc(thumb_dir.name + "/" + thumb_path.name)}" alt="clip {clip.index}">'
        duration_pct = (clip.duration_sec / timeline.total_duration_sec * 100) if timeline.total_duration_sec else 0
        bar_html.append(
            f'<div class="bar-seg" style="flex:{max(clip.duration_sec, 0.1):.3f};background:{background};'
            f'border-left:2px solid {color}" title="#{clip.index} {label} {clip.duration_sec:.1f}s">{clip.index + 1}</div>'
        )
        clips_html.append(f"""
        <div class="card">
          {thumb_html}
          <div class="card-body">
            <div class="card-head">
              <span class="badge" style="background:{background};color:{color};border-color:{color}">{_esc(label)}</span>
              <span class="mono">#{clip.index + 1}</span>
              <span class="muted">{_esc(_fmt_time(clip.target_start_sec))} · {clip.duration_sec:.1f}s</span>
              <span class="muted">占比 {duration_pct:.0f}%</span>
              {'<span class="badge" style="background:#F1EFE8;color:#6B6A66;border-color:#D6D4CE">换场 ' + _esc(clip.transition) + '</span>' if clip.transition else ''}
            </div>
            <div class="subtitle">{_esc(clip.subtitle) or '<span class="muted">（本镜头无字幕）</span>'}</div>
            <div class="meta muted mono">
              源：{_esc(Path(clip.material_path).name)} · 取 {clip.source_start_sec:.1f}s 起 {clip.duration_sec:.1f}s
              · 原声音量 {clip.volume:.2f}{' · 转场 ' + _esc(clip.transition) if clip.transition else ''}
            </div>
          </div>
        </div>""")

    music_html = '<div class="muted">未配乐（草稿仅含视频与字幕）</div>'
    if timeline.music:
        music_html = (
            f'<div><strong>{_esc(timeline.music.name)}</strong></div>'
            f'<div class="muted mono">{_esc(timeline.music.path)}</div>'
            f'<div class="muted">起始 {timeline.music_start_sec:.1f}s · 音量 {timeline.music_volume:.2f}'
            f' · BPM {timeline.music.bpm or "-"} · 情绪 {_esc(timeline.music.mood) or "-"}</div>'
        )

    tags_html = " ".join(f'<span class="tag">#{_esc(tag)}</span>' for tag in (timeline.copywriting.hashtags if timeline.copywriting else []))
    transition_count = len([clip for clip in timeline.clips if clip.transition])

    selected = (selection_report or {}).get("selected", [])
    rows = "".join(
        f"<tr><td class='mono'>{_esc(item.get('shot_id'))}</td>"
        f"<td>{_esc(SHOT_LABELS.get(item.get('type'), item.get('type')))}</td>"
        f"<td class='mono'>{_esc(item.get('range'))}</td>"
        f"<td class='num'>{item.get('quality', 0):.1f}</td>"
        f"<td class='num'>{item.get('final_score', 0):.1f}</td>"
        f"<td class='muted'>{_esc(item.get('analyzer'))}</td></tr>"
        for item in selected
    )

    warn_html = ""
    if warnings:
        items = "".join(f"<li>{_esc(item)}</li>" for item in warnings)
        warn_html = f'<section class="warn"><h2>注意事项</h2><ul>{items}</ul></section>'

    page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>分镜预览 · {_esc(timeline.project_name)}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 28px 32px 56px; background: #FBFAF8; color: #2C2C2A;
         font-family: "Microsoft YaHei", "PingFang SC", -apple-system, "Segoe UI", sans-serif; font-size: 14px; line-height: 1.6; }}
  h1 {{ font-size: 22px; font-weight: 600; margin: 0 0 6px; }}
  h2 {{ font-size: 15px; font-weight: 600; margin: 26px 0 10px; }}
  .muted {{ color: #6B6A66; }}
  .mono {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: 12px; }}
  header {{ border-bottom: 1px solid #E5E3DE; padding-bottom: 16px; }}
  .summary {{ display: flex; gap: 26px; flex-wrap: wrap; margin-top: 12px; }}
  .metric {{ min-width: 96px; }}
  .metric .value {{ font-size: 20px; font-weight: 600; }}
  .metric .label {{ font-size: 12px; color: #6B6A66; }}
  .tag {{ display: inline-block; background: #EEEDFE; color: #534AB7; border-radius: 6px; padding: 2px 8px; margin: 2px 4px 2px 0; font-size: 12px; }}
  .bar {{ display: flex; height: 34px; border: 1px solid #E5E3DE; border-radius: 8px; overflow: hidden; margin-top: 12px; background: #fff; }}
  .bar-seg {{ display: flex; align-items: center; justify-content: center; font-size: 11px; color: #2C2C2A; }}
  .card {{ display: flex; gap: 16px; background: #fff; border: 1px solid #E5E3DE; border-radius: 10px; padding: 14px; margin-bottom: 10px; }}
  .thumb {{ width: 148px; height: 84px; object-fit: cover; border-radius: 6px; border: 1px solid #E5E3DE; flex: 0 0 auto; }}
  .thumb.placeholder {{ display: flex; align-items: center; justify-content: center; color: #9A9892; font-size: 12px; background: #F5F4F0; }}
  .card-body {{ flex: 1; min-width: 0; }}
  .card-head {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .badge {{ border: 1px solid; border-radius: 999px; padding: 1px 10px; font-size: 12px; }}
  .subtitle {{ margin: 6px 0 4px; font-size: 15px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
  .panel {{ background: #fff; border: 1px solid #E5E3DE; border-radius: 10px; padding: 14px 16px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; font-weight: 600; color: #6B6A66; border-bottom: 1px solid #E5E3DE; padding: 6px 8px; }}
  td {{ border-bottom: 1px solid #F1EFE8; padding: 5px 8px; }}
  td.num {{ text-align: right; font-family: "Cascadia Mono", Consolas, monospace; }}
  .warn {{ background: #FAEEDA; border: 1px solid #EF9F27; border-radius: 10px; padding: 12px 18px; margin-top: 24px; }}
  .warn ul {{ margin: 6px 0 0; padding-left: 20px; }}
  .warn h2 {{ margin: 0; color: #633806; font-size: 14px; }}
  footer {{ margin-top: 30px; color: #9A9892; font-size: 12px; }}
</style>
</head>
<body>
<header>
  <h1>{_esc(timeline.project_name)}</h1>
  <div class="mono muted">{_esc(timeline.project_id)} · 生成时间线 {_esc(_fmt_time(timeline.total_duration_sec))}</div>
  <div class="summary">
    <div class="metric"><div class="value">{len(timeline.clips)}</div><div class="label">镜头数</div></div>
    <div class="metric"><div class="value">{timeline.total_duration_sec:.1f}s</div><div class="label">成片时长</div></div>
    <div class="metric"><div class="value">{timeline.width}×{timeline.height}</div><div class="label">画布</div></div>
    <div class="metric"><div class="value">{timeline.fps:g}</div><div class="label">帧率</div></div>
    <div class="metric"><div class="value">{len(selected)}</div><div class="label">入选镜头</div></div>
    <div class="metric"><div class="value">{transition_count}</div><div class="label">换场转场</div></div>
    <div class="metric"><div class="value">{timeline.transition_duration_sec:g}s</div><div class="label">转场时长</div></div>
    <div class="metric"><div class="value">{'开' if timeline.ken_burns else '关'}</div><div class="label">镜头推拉</div></div>
  </div>
  <div class="bar">{''.join(bar_html)}</div>
</header>

<section class="grid">
  <div class="panel">
    <h2>片头文案</h2>
    <div style="font-size:16px">{_esc(timeline.title) or '<span class="muted">（未设置）</span>'}</div>
    <div style="margin-top:8px">{tags_html}</div>
  </div>
  <div class="panel">
    <h2>背景音乐</h2>
    {music_html}
    <div class="muted" style="margin-top:8px">落版：{_esc(timeline.outro_text) or '（未设置）'}</div>
  </div>
</section>

<h2>分镜表</h2>
{''.join(clips_html) or '<div class="muted">没有镜头</div>'}

<h2>选片明细</h2>
<div class="panel">
<table>
  <thead><tr><th>镜头</th><th>类型</th><th>区间</th><th style="text-align:right">画质</th><th style="text-align:right">综合</th><th>判定来源</th></tr></thead>
  <tbody>{rows or '<tr><td colspan="6" class="muted">无</td></tr>'}</tbody>
</table>
</div>

{warn_html}

<footer>AutoCut Agent · MVP 故事板预览 · 该页面由时间线自动生成，可直接改配置后重新生成</footer>
</body>
</html>
"""
    output_path.write_text(page, encoding="utf-8")
    logger.info("故事板预览已生成: %s", output_path)
    return output_path
