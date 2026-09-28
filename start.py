"""AutoCut 一键启动：跑完流水线并自动打开预览页。

    python start.py                 # mock 演示（默认，无需任何素材）
    python start.py --open          # 跑完自动用浏览器打开总览页
    python start.py --source local --path "D:/素材" --open
    python start.py --serve         # 额外起一个本地 HTTP 服务，方便手机/局域网查看

这个脚本面向「我只想看看效果」的场景：它自己找 Python、自己设好路径、
跑完把结果路径打印出来，不需要你记住任何环境变量。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autocut.config import AppConfig  # noqa: E402
from autocut.logging_setup import setup_logging  # noqa: E402
from autocut.pipeline import Pipeline  # noqa: E402
from autocut.sources.base import SourceOptions  # noqa: E402


def build_source(kind: str, path: str, *, copy_materials: bool = False, months: list[str] | None = None):
    """按来源类型构造素材源。注意各源接收路径的方式不同：local 走位置参数。"""
    options = SourceOptions(
        copy_materials=copy_materials,
        months=list(months or []),
        recursive_scan=True,
    )
    if kind == "mock":
        from autocut.sources.mock import MockSource
        return MockSource(options, materials_root=PROJECT_ROOT / "workspace" / "materials")
    if kind == "local":
        from autocut.sources.local import LocalSource
        return LocalSource(path, options)
    if kind == "capcut-web":
        from autocut.sources.capcut_web import CapCutWebSource
        return CapCutWebSource(
            state_path=PROJECT_ROOT / "workspace" / ".auth" / "capcut_state.json",
            debug_dir=PROJECT_ROOT / "workspace" / "debug",
            selectors_path=PROJECT_ROOT / "config" / "capcut_selectors.json",
            materials_root=PROJECT_ROOT / "workspace" / "materials",
            options=options,
        )
    raise SystemExit(f"未知的素材来源：{kind}")


def open_path(path: Path) -> None:
    """用系统默认程序打开文件或目录（跨平台）。"""
    try:
        if sys.platform == "win32":
            import os
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as exc:  # pragma: no cover - 打开失败不影响主流程
        print(f"  （无法自动打开 {path}：{exc}）")


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoCut 一键启动")
    parser.add_argument("--source", default="mock", choices=["mock", "local", "capcut-web"],
                        help="素材来源（默认 mock 演示）")
    parser.add_argument("--path", default="", help="local/capcut-web 源的素材根目录")
    parser.add_argument("--month", action="append", default=[], help="只处理指定月份，可多次指定")
    parser.add_argument("--template", default="", help="文案模板 id")
    parser.add_argument("--music", default="", help="指定音乐 id")
    parser.add_argument("--copy-materials", action="store_true", help="把素材复制到工作区")
    parser.add_argument("--open", action="store_true", help="跑完自动打开总览页")
    parser.add_argument("--serve", action="store_true", help="跑完起本地 HTTP 服务预览")
    parser.add_argument("--port", type=int, default=8899, help="--serve 使用的端口")
    parser.add_argument("--ui", action="store_true",
                        help="直接启动图形操作台（浏览器里点选操作，不跑流水线）")
    parser.add_argument("--ui-port", type=int, default=8765, help="操作台端口")
    args = parser.parse_args()

    if args.ui:
        from autocut.web import serve as serve_ui
        config = AppConfig.load(None, None)
        return serve_ui(config, port=args.ui_port, open_browser=True)

    if args.source != "mock" and not args.path:
        print(f"使用 --source {args.source} 时必须提供 --path")
        return 2

    config = AppConfig.load(None, None)
    setup_logging(config.logs_dir, verbose=False)

    source = build_source(args.source, args.path,
                          copy_materials=args.copy_materials, months=args.month)
    projects = source.discover()
    if not projects:
        print("没有发现任何项目，请检查 --path 指向的目录结构（月份/项目两级）。")
        return 1

    print(f"\nAutoCut 启动 · 素材来源 {args.source} · 发现 {len(projects)} 个项目\n")

    pipeline = Pipeline(config, source,
                        template_id=args.template or None,
                        music_id=args.music)
    results = []
    for project in projects:
        print("=" * 68)
        try:
            artifacts = pipeline.run_project(project)
        except Exception as exc:
            print(f"项目「{project.name}」处理失败：{exc}")
            continue
        result = artifacts.result
        if result is None:
            continue
        results.append(result)
        print(f"项目：{result.project_name}    状态：{result.status}")
        print(f"  素材 {result.material_count} · 候选镜头 {result.shot_count}"
              f" · 入选 {result.selected_count} · 成片 {result.duration_sec:.1f}s")
        print(f"  草稿：{result.draft_path}")
        print(f"  预览：{result.preview_path}")

    if not results:
        print("\n没有任何项目产出结果。")
        return 1

    # 汇总
    summary = {
        "total": len(results),
        "succeeded": len([r for r in results if r.status == "DRAFT_READY"]),
        "failed": len([r for r in results if r.status != "DRAFT_READY"]),
        "projects": [r.to_dict() for r in results],
    }
    report_path = config.workspace_path / "batch_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 68)
    print(f"完成：成功 {summary['succeeded']} / 共 {summary['total']}")
    print(f"汇总报告：{report_path}")

    first_preview = Path(results[0].preview_path) if results[0].preview_path else None

    if args.serve:
        import functools
        import http.server
        import socketserver
        import threading

        root = str(config.workspace_path)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=root)
        try:
            httpd = socketserver.TCPServer(("127.0.0.1", args.port), handler)
        except OSError as exc:
            print(f"端口 {args.port} 不可用：{exc}")
            return 1
        url = f"http://127.0.0.1:{args.port}/"
        print(f"\n本地预览服务已启动：{url}")
        print("按 Ctrl+C 停止。")
        if args.open:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止。")
        finally:
            httpd.server_close()
        return 0

    if args.open and first_preview and first_preview.exists():
        open_path(first_preview)
    elif args.open:
        open_path(config.workspace_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
