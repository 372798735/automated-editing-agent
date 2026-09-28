"""一键演示：用 mock 素材跑通「素材 → 分析 → 文案 → 配乐 → 时间线 → 草稿 → 预览」。

    python scripts/demo_run.py            # 跑一个项目
    python scripts/demo_run.py --all      # 跑全部演示项目

在没有任何第三方依赖、没有 ffmpeg、没有剪映的机器上也能执行，
用途是让人 30 秒内看到完整产物长什么样。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autocut.config import AppConfig  # noqa: E402
from autocut.logging_setup import setup_logging  # noqa: E402
from autocut.pipeline import Pipeline  # noqa: E402
from autocut.sources.base import SourceOptions  # noqa: E402
from autocut.sources.mock import MockSource  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoCut mock 演示")
    parser.add_argument("--all", action="store_true", help="跑全部演示项目")
    parser.add_argument("--workspace", default="", help="工作区目录（默认 workspace）")
    args = parser.parse_args()

    overrides = {"workspace": args.workspace} if args.workspace else None
    config = AppConfig.load(None, overrides)
    setup_logging(config.logs_dir, verbose=False)

    source = MockSource(SourceOptions(), materials_root=config.materials_dir)
    projects = source.discover()
    if not args.all:
        projects = projects[:1]

    pipeline = Pipeline(config, source)
    print(f"\n演示项目：{[p.name for p in projects]}\n")

    results = []
    for project in projects:
        artifacts = pipeline.run_project(project)
        result = artifacts.result
        if result is None:
            continue
        results.append(result)
        print("-" * 70)
        print(f"项目：{result.project_name}   状态：{result.status}")
        print(f"  素材 {result.material_count} → 候选镜头 {result.shot_count} → 入选 {result.selected_count}"
              f" → 成片 {result.duration_sec:.1f}s / {result.clip_count} 个片段")
        print(f"  剪映草稿：{result.draft_path}")
        print(f"  分镜预览：{result.preview_path}")
        print(f"  时间线  ：{result.timeline_path}")
        print(f"  渲染脚本：{result.render_script_path}")

    # 同步刷新批量汇总，保证总览数据与本次运行一致
    if results:
        summary = {
            "total": len(results),
            "succeeded": len([r for r in results if r.status == "DRAFT_READY"]),
            "failed": len([r for r in results if r.status != "DRAFT_READY"]),
            "projects": [r.to_dict() for r in results],
        }
        config.workspace_path.mkdir(parents=True, exist_ok=True)
        (config.workspace_path / "batch_report.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print("-" * 70)
    print("演示完成。产物说明：")
    print("  · 剪映草稿目录含 draft_content.json / draft_meta_info.json，可直接放进剪映草稿文件夹打开")
    print("  · preview.html 用浏览器打开可看到完整分镜表（含每个镜头的选片理由）")
    print("  · mock 模式下素材是合成元数据，草稿能打开但需替换为真实素材；")
    print("    真实项目请用 --source local --path <素材目录>，或先用 scripts/make_sample_materials.py 造测试素材")
    return 0


if __name__ == "__main__":
    sys.exit(main())
