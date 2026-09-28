"""用 ffmpeg 合成「可播放」的测试素材（有 ffmpeg 时使用）。

mock 源只生成元数据，用于验证决策链路；如果你想验证**完整链路**（抽帧分析、缩略图、
剪映能真正导入并播放），就用这个脚本造几条真实视频。

    python scripts/make_sample_materials.py --out "D:/素材/演示" --count 8

生成结构：
    <out>/2026-09/演示项目-全屋通铺/VID_001.mp4 ...
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SCENES = [
    ("开工找平", "color=c=0x3B3B3B:s=1080x1920:d=12", "process"),
    ("房间全景", "color=c=0x8A7C6A:s=1080x1920:d=10", "hook"),
    ("锁扣拼接", "color=c=0x6E5B45:s=1080x1920:d=14", "process"),
    ("缝隙特写", "color=c=0xA9967C:s=1080x1920:d=8", "detail"),
    ("收边处理", "color=c=0x4E4A45:s=1080x1920:d=9", "process"),
    ("踩踏测试", "color=c=0x7A6A55:s=1080x1920:d=11", "process"),
    ("纹理特写", "color=c=0xC0AC8E:s=1080x1920:d=7", "detail"),
    ("完工效果", "color=c=0x9C8A70:s=1080x1920:d=13", "finish"),
]


def build_filter(label: str, index: int) -> str:
    font = None
    for candidate in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
        if Path(candidate).exists():
            font = candidate
            break
    base = "noise=alls=8:allf=t"
    if not font:
        return base
    escaped = font.replace(":", "\\:")
    return (
        f"drawtext=fontfile='{escaped}':text='镜头{index:02d} {label}':fontcolor=white:fontsize=64:"
        f"x=(w-text_w)/2:y=h*0.45,{base}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="生成可播放的合成测试素材")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "workspace" / "demo_materials"),
                        help="输出根目录（默认 workspace/demo_materials）")
    parser.add_argument("--month", default="2026-09", help="月份文件夹名")
    parser.add_argument("--project", default="演示项目-全屋通铺", help="项目文件夹名")
    parser.add_argument("--count", type=int, default=8, help="生成素材数量（1-8）")
    args = parser.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("未检测到 ffmpeg，无法生成可播放素材。")
        print("安装方式（任选）：")
        print("  winget install Gyan.FFmpeg")
        print("  或到 https://www.gyan.dev/ffmpeg/builds/ 下载后将 bin 目录加入 PATH")
        print("\n如果只是想验证剪辑决策链路，可直接使用 mock 源：")
        print("  python -m autocut run --source mock")
        return 3

    target_dir = Path(args.out) / args.month / args.project
    target_dir.mkdir(parents=True, exist_ok=True)

    for index in range(1, min(args.count, len(SCENES)) + 1):
        label, source, _ = SCENES[index - 1]
        out_path = target_dir / f"VID_{index:03d}.mp4"
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", source,
            "-vf", build_filter(label, index),
            "-t", source.rsplit("d=", 1)[-1],
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-pix_fmt", "yuv420p", str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            print(f"生成失败 {out_path.name}: {(proc.stderr or '').strip()[:200]}")
            continue
        print(f"已生成 {out_path}  （{label}）")

    print(f"\n素材目录：{target_dir}")
    print("下一步：")
    print(f'  python -m autocut batch --source local --path "{Path(args.out)}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
