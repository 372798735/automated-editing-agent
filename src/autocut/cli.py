"""AutoCut 命令行入口。

设计原则：命令少而清晰，默认值可用，任何失败都输出「人话 + 下一步该怎么办」。

    autocut doctor          环境体检（缺什么、影响什么）
    autocut run             跑单个项目（端到端）
    autocut batch           跑一个月份下的全部项目
    autocut list            只列出发现的项目，不执行（干跑）
    autocut capcut-login    剪映云空间扫码登录，保存登录态
    autocut capcut-record   录制云空间页面结构与网络请求（选择器校准）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .capabilities import detect, default_jianying_draft_dir
from .config import AppConfig, PROJECT_ROOT
from .errors import AutoCutError
from .logging_setup import get_logger, set_trace_id, setup_logging
from .pipeline import Pipeline, run_batch
from .sources import build_source
from .sources.base import SourceOptions

logger = get_logger("cli")


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autocut",
        description="AutoCut Agent —— 地板铺装短视频智能剪辑流水线（剪映云空间 → 剪映草稿）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"AutoCut {__version__}")
    parser.add_argument("--config", default="", help="配置文件路径（默认 config/default.json）")
    parser.add_argument("--workspace", default="", help="工作区目录（默认 workspace）")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="临时覆盖配置，例如 --set video.target_max_sec=40")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # doctor
    subparsers.add_parser("doctor", help="检查运行环境能力")

    # 通用流水线参数
    def add_pipeline_args(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--source", default="mock", choices=["mock", "local", "capcut-web"],
                         help="素材源：mock（演示）/ local（本地目录）/ capcut-web（剪映云空间）")
        sub.add_argument("--path", default="", help="local 源的根目录（月份/项目两级或直接项目目录）")
        sub.add_argument("--project", default="", help="只处理指定项目（目录名或 project id）")
        sub.add_argument("--month", action="append", default=[], help="只处理指定月份，可多次指定")
        sub.add_argument("--template", default="", help="文案模板 id（默认取配置里的 default_template_id）")
        sub.add_argument("--music", default="", help="指定音乐 id（默认自动匹配）")
        sub.add_argument("--copy-materials", action="store_true", help="把素材复制到工作区（云空间源默认复制）")
        llm_group = sub.add_mutually_exclusive_group()
        llm_group.add_argument("--llm", dest="llm", action="store_true", help="启用 LLM 润色文案")
        llm_group.add_argument("--no-llm", dest="llm", action="store_false", help="只用模板文案（默认）")
        sub.set_defaults(llm=None)

    run = subparsers.add_parser("run", help="跑单个（或筛选后的）项目")
    add_pipeline_args(run)

    batch = subparsers.add_parser("batch", help="批量处理发现到的全部项目")
    add_pipeline_args(batch)

    listing = subparsers.add_parser("list", help="只列出发现的项目，不执行")
    add_pipeline_args(listing)

    # 剪映云空间
    login = subparsers.add_parser("capcut-login", help="扫码登录剪映云空间并保存登录态")
    login.add_argument("--url", default="", help="登录入口 URL（默认剪映首页）")
    login.add_argument("--wait", type=int, default=180, help="等待扫码的秒数")

    record = subparsers.add_parser("capcut-record", help="录制云空间页面结构与网络请求")
    record.add_argument("--url", default="", help="云空间 URL")
    record.add_argument("--seconds", type=int, default=90, help="录制时长")
    record.add_argument("--headless", action="store_true", help="无头模式（不推荐，扫码需有头）")

    # 图形操作台
    serve_cmd = subparsers.add_parser("serve", help="启动图形操作台（浏览器里点选操作，推荐给非开发者）")
    serve_cmd.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    serve_cmd.add_argument("--port", type=int, default=8765, help="端口，默认 8765")
    serve_cmd.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    return parser


# ---------------------------------------------------------------------------
# 配置装配
# ---------------------------------------------------------------------------


def load_config(args: argparse.Namespace) -> AppConfig:
    overrides: dict = {}
    for item in args.set:
        if "=" not in item:
            raise AutoCutError(f"--set 参数格式应为 KEY=VALUE，收到: {item}")
        key, _, value = item.partition("=")
        _assign_override(overrides, key.strip(), _coerce(value.strip()))
    if args.workspace:
        overrides["workspace"] = args.workspace
    return AppConfig.load(args.config or None, overrides or None)


def _coerce(value: str):
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"none", "null", ""}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _assign_override(overrides: dict, dotted_key: str, value) -> None:
    parts = dotted_key.split(".")
    cursor = overrides
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


def build_source_from_args(args: argparse.Namespace, config: AppConfig):
    kind = args.source
    options = SourceOptions(
        copy_materials=args.copy_materials,
        months=list(args.month or []),
        projects=[args.project] if args.project else [],
    )
    if kind == "local":
        if not args.path:
            raise AutoCutError("--source local 需要同时指定 --path <素材目录>")
        return build_source("local", root=args.path, options=options, materials_root=config.materials_dir)
    if kind == "mock":
        return build_source("mock", options=options, materials_root=config.materials_dir)
    return build_source(
        "capcut-web",
        state_path=config.workspace_path / ".auth" / "capcut_state.json",
        debug_dir=config.workspace_path / "debug",
        selectors_path=PROJECT_ROOT / "config" / "capcut_selectors.json",
        materials_root=config.materials_dir,
        options=SourceOptions(copy_materials=True, months=list(args.month or []), projects=[args.project] if args.project else []),
        headed=True,
    )


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------


def cmd_doctor(config: AppConfig) -> int:
    report = detect(draft_root=config.draft.draft_root, llm_api_key_env=config.llm.api_key_env)
    print("\nAutoCut 环境体检")
    print("=" * 68)
    print(f"Python {report.python_version} · {report.platform}")
    print("-" * 68)
    for cap in report.capabilities:
        mark = "✓" if cap.available else ("✗" if cap.required else "○")
        print(f"{mark} {cap.name}")
        print(f"    {cap.detail}")
        if not cap.available:
            print(f"    影响：{cap.impact}")
    print("-" * 68)

    draft_root = Path(config.draft.draft_root) if config.draft.draft_root else default_jianying_draft_dir()
    print(f"草稿输出目录：{draft_root}")
    print(f"  该目录{'存在' if draft_root.exists() else '不存在'}，"
          f"{'草稿将直接出现在剪映草稿列表' if draft_root.exists() else '草稿将写入 workspace/draft_output，可手动拷贝到剪映目录'}")
    print(f"工作区：{config.workspace_path}")
    print("=" * 68)
    if report.missing_required:
        print("存在必需能力缺失，请先解决后再运行流水线。")
        return 2
    print("核心链路可用。缺失的可选能力只会降低效果，不会阻断流水线。")
    return 0


def cmd_list(args: argparse.Namespace, config: AppConfig) -> int:
    source = build_source_from_args(args, config)
    projects = source.discover()
    if not projects:
        print("没有发现项目。请检查 --path 下的目录结构是否为 <月份>/<项目>/<素材文件>。")
        return 1
    print(f"\n发现 {len(projects)} 个项目：")
    for project in projects:
        print(f"  [{project.month or '--'}] {project.name}")
        print(f"      id   : {project.id}")
        print(f"      路径 : {project.source_path}")
    source.close()
    return 0


def cmd_run(args: argparse.Namespace, config: AppConfig) -> int:
    source = build_source_from_args(args, config)
    projects = source.discover()
    if not projects:
        print("没有发现项目。")
        return 1
    if len(projects) > 1:
        logger.info("发现 %s 个项目，run 仅处理第一个，其余请用 batch 或 --project 指定", len(projects))
    target = projects[0]
    if args.project:
        matched = [p for p in projects if args.project in {p.name, p.id}]
        if not matched:
            print(f"未找到项目「{args.project}」，可用项目：")
            for project in projects:
                print(f"  - {project.name} ({project.id})")
            return 1
        target = matched[0]

    pipeline = Pipeline(
        config,
        source,
        template_id=args.template or None,
        use_llm=args.llm,
        music_id=args.music,
    )
    artifacts = pipeline.run_project(target)
    source.close()
    return _print_result(artifacts.result)  # type: ignore[arg-type]


def cmd_batch(args: argparse.Namespace, config: AppConfig) -> int:
    source = build_source_from_args(args, config)
    results = run_batch(
        config,
        source,
        template_id=args.template or None,
        use_llm=args.llm,
        music_id=args.music,
    )
    source.close()
    if not results:
        return 1
    failures = 0
    for result in results:
        code = _print_result(result)
        failures += 0 if code == 0 else 1
    print(f"\n汇总：{len(results) - failures} 成功 / {failures} 失败")
    print(f"批量报告：{config.workspace_path / 'batch_report.json'}")
    return 0 if failures == 0 else 1


def _print_result(result) -> int:
    print("\n" + "=" * 68)
    print(f"项目：{result.project_name}  状态：{result.status}")
    print(f"  素材 {result.material_count} · 候选镜头 {result.shot_count} · 入选 {result.selected_count} "
          f"· 成片 {result.duration_sec:.1f}s")
    if result.draft_path:
        print(f"  剪映草稿：{result.draft_path}")
    if result.preview_path:
        print(f"  分镜预览：{result.preview_path}")
    if result.render_script_path:
        print(f"  渲染脚本：{result.render_script_path}")
    if result.timeline_path:
        print(f"  时间线  ：{result.timeline_path}")
    for stage in result.stages:
        print(f"    · {stage['name']:<10} {stage['duration_ms']:>6} ms  {stage['detail']}")
    if result.warnings:
        print("  注意事项：")
        for warning in result.warnings[:8]:
            print(f"    - {warning}")
    if result.errors:
        print("  错误：")
        for error in result.errors:
            print(f"    - {error}")
    print("=" * 68)
    return 0 if result.status == "DRAFT_READY" else 1


def cmd_capcut_login(args: argparse.Namespace, config: AppConfig) -> int:
    from .sources.capcut_web import DEFAULT_START_URL, CapCutSession
    session = CapCutSession(
        state_path=config.workspace_path / ".auth" / "capcut_state.json",
        debug_dir=config.workspace_path / "debug",
        headed=True,
    )
    session.login(start_url=args.url or DEFAULT_START_URL, wait_sec=args.wait)
    print(f"登录态已保存：{session.state_path}")
    print("下一步：autocut capcut-record 录制云空间页面结构，用于生成选择器配置。")
    return 0


def cmd_capcut_record(args: argparse.Namespace, config: AppConfig) -> int:
    from .sources.capcut_web import CapCutSession
    session = CapCutSession(
        state_path=config.workspace_path / ".auth" / "capcut_state.json",
        debug_dir=config.workspace_path / "debug",
        headed=not args.headless,
    )
    if not session.state_path.exists():
        print("未找到登录态，请先执行：autocut capcut-login")
        return 1
    artifacts = session.record(url=args.url or None, seconds=args.seconds)
    print("\n校准产物：")
    for key, value in artifacts.items():
        print(f"  {key:<12} {value}")
    print("\n请把其中的页面结构与接口规律整理进 config/capcut_selectors.json（参考同目录 .example 文件）。")
    return 0


def cmd_serve(args: argparse.Namespace, config: AppConfig) -> int:
    """启动图形操作台。"""
    from .web import serve
    return serve(
        config,
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
    )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    set_trace_id("cli")

    try:
        config = load_config(args)
    except AutoCutError as exc:
        print(f"配置错误：{exc}")
        return 1

    setup_logging(config.logs_dir, verbose=args.verbose)

    handlers = {
        "doctor": lambda: cmd_doctor(config),
        "list": lambda: cmd_list(args, config),
        "run": lambda: cmd_run(args, config),
        "batch": lambda: cmd_batch(args, config),
        "capcut-login": lambda: cmd_capcut_login(args, config),
        "capcut-record": lambda: cmd_capcut_record(args, config),
        "serve": lambda: cmd_serve(args, config),
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    try:
        return handler()
    except AutoCutError as exc:
        print(f"\n执行失败：{exc}")
        return exc.exit_code
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
