"""剪映云空间素材源（Playwright 网页自动化）。

诚实说明：
    剪映云空间没有公开 API，页面 DOM 也会随版本迭代变化。因此本模块分两层：

    1. **校准层（capcut-record）**：打开已登录的浏览器，录制网络请求、页面快照与截图，
       把云空间的真实结构落到 workspace/debug/。这一步需要你在自己的机器上配合完成。
    2. **执行层（CapCutWebSource）**：读取校准产物 config/capcut_selectors.json 中的选择器
       与接口规则，完成「枚举月份/项目 → 下载素材」。未校准时给出明确指引而不是静默失败。

    这样做的原因是：任何写死的选择器都会在你机器上的剪映版本里失效，
    把「校准」做成一次性的显式动作，比藏一堆易碎的选择器更可靠。

合规边界：仅操作用户自己账号的常规页面交互，不绕过登录、限流或任何访问控制。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..errors import SourceError
from ..logging_setup import get_logger
from ..media import probe
from ..models import Material, Project
from .base import MaterialSource, SourceOptions

logger = get_logger("sources.capcut")

DEFAULT_START_URL = "https://www.jianying.com/web"
CLOUD_URL_HINT = "https://www.jianying.com/web"
SUPPORTED_SELECTOR_KEYS = ("month_item", "project_item", "material_item", "download_button")


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise SourceError(
            "剪映云空间同步需要 Playwright。请执行：\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        ) from exc
    return sync_playwright


class CapCutSession:
    """浏览器会话管理：登录态持久化 + 页面录制校准。"""

    def __init__(self, state_path: Path, debug_dir: Path, headed: bool = True) -> None:
        self.state_path = state_path
        self.debug_dir = debug_dir
        self.headed = headed

    # ------------------------------------------------------------------
    def _launch(self, playwright, record_network: bool = False):
        browser = playwright.chromium.launch(headless=not self.headed)
        context_kwargs: dict[str, Any] = {
            "viewport": {"width": 1440, "height": 900},
            "locale": "zh-CN",
        }
        if self.state_path.exists():
            context_kwargs["storage_state"] = str(self.state_path)
            logger.info("复用已保存的登录态: %s", self.state_path)
        context = browser.new_context(**context_kwargs)
        network_log: list[dict[str, Any]] = []
        if record_network:
            context.on("response", lambda response: network_log.append({
                "url": response.url,
                "status": response.status,
                "type": response.request.resource_type,
                "method": response.request.method,
                "at": datetime.now().isoformat(timespec="seconds"),
            }))
        return browser, context, network_log

    def _save_state(self, context) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(self.state_path))
        logger.info("登录态已保存: %s", self.state_path)

    @staticmethod
    def _try_open_login(page) -> None:
        """尽力自动点击「登录」入口；失败不阻塞（用户可手动点右上角「登录」）。"""
        for selector in ("text=登录", "a:has-text('登录')", "button:has-text('登录')"):
            try:
                page.click(selector, timeout=3000)
                logger.info("已自动点击登录入口: %s", selector)
                return
            except Exception:
                continue
        logger.info("未自动找到登录入口，请手动点击页面右上角「登录」")

    # ------------------------------------------------------------------
    def login(self, start_url: str = DEFAULT_START_URL, wait_sec: int = 180) -> Path:
        """打开有头浏览器等待用户扫码，随后保存登录态。"""
        sync_playwright = _require_playwright()
        with sync_playwright() as playwright:
            browser, context, _ = self._launch(playwright)
            page = context.new_page()
            page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
            self._try_open_login(page)
            print(
                "\n请在打开的浏览器窗口中完成剪映登录（扫码）：\n"
                f"  1. 若二维码未自动出现，请点击页面右上角「登录」再扫码\n"
                f"  2. 登录后无需关闭窗口，脚本会在检测到登录后自动保存登录态\n"
                f"  3. 最多等待 {wait_sec} 秒\n"
            )
            deadline = time.time() + wait_sec
            logged_in = False
            while time.time() < deadline:
                try:
                    cookies = context.cookies()
                except Exception:  # pragma: no cover - 浏览器意外关闭
                    raise SourceError("浏览器会话已关闭，登录流程中止")
                names = {c.get("name", "") for c in cookies}
                if names & {"sessionid", "sid_tt", "passport_csrf_token", "sid_guard", "uid_tt"}:
                    logged_in = True
                    break
                time.sleep(2)
            if not logged_in:
                browser.close()
                raise SourceError(f"等待 {wait_sec} 秒未检测到登录态，请重试")
            self._save_state(context)
            browser.close()
        return self.state_path

    # ------------------------------------------------------------------
    def record(
        self,
        url: str | None = None,
        seconds: int = 90,
        dump_snapshot: bool = True,
    ) -> dict[str, Any]:
        """录制云空间页面结构与网络请求，供选择器校准。"""
        sync_playwright = _require_playwright()
        target = url or CLOUD_URL_HINT
        self.debug_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            browser, context, network_log = self._launch(playwright, record_network=True)
            page = context.new_page()
            page.goto(target, wait_until="domcontentloaded", timeout=60000)
            print(
                f"\n已打开 {target}\n"
                f"请在浏览器里自己找到存放素材的页面（如「云空间/云盘」入口），点开一个月份和一个项目文件夹，让页面充分加载。\n"
                f"如果整个网页版都找不到云空间入口，停留在能看到素材/草稿列表的页面即可。\n"
                f"录制 {seconds} 秒后自动保存现场信息。\n"
            )
            time.sleep(seconds)

            artifacts: dict[str, Any] = {}
            if dump_snapshot:
                html_path = self.debug_dir / "capcut_page.html"
                html_path.write_text(page.content(), encoding="utf-8")
                artifacts["html"] = str(html_path)
                try:
                    shot_path = self.debug_dir / "capcut_page.png"
                    page.screenshot(path=str(shot_path), full_page=True)
                    artifacts["screenshot"] = str(shot_path)
                except Exception as exc:  # pragma: no cover
                    logger.warning("截图失败: %s", exc)
                try:
                    text_path = self.debug_dir / "capcut_page.txt"
                    text_path.write_text(page.inner_text("body")[:20000], encoding="utf-8")
                    artifacts["text"] = str(text_path)
                except Exception as exc:  # pragma: no cover
                    logger.warning("提取页面文本失败: %s", exc)

            self._save_state(context)
            browser.close()

        log_path = self.debug_dir / "capcut_network.json"
        interesting = [
            item for item in network_log
            if any(key in item["url"] for key in ("material", "file", "space", "cloud", "list", "download", "video", "media"))
        ]
        log_path.write_text(json.dumps({
            "recorded_url": target,
            "total_requests": len(network_log),
            "candidates": interesting[:400],
            "all": network_log[:2000],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["network"] = str(log_path)
        logger.info("校准产物已保存到 %s", self.debug_dir)
        return artifacts


class CapCutWebSource(MaterialSource):
    """读取校准配置，从剪映云空间同步素材。"""

    name = "capcut-web"

    def __init__(
        self,
        state_path: Path,
        debug_dir: Path,
        selectors_path: Path,
        materials_root: Path,
        options: SourceOptions | None = None,
        headed: bool = True,
        max_materials_per_project: int = 80,
    ) -> None:
        super().__init__(options)
        self.session = CapCutSession(state_path, debug_dir, headed=headed)
        self.selectors_path = selectors_path
        self.materials_root = materials_root
        self.max_materials_per_project = max_materials_per_project
        self._selectors: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    @property
    def selectors(self) -> dict[str, Any]:
        if self._selectors is None:
            if not self.selectors_path.exists():
                raise SourceError(
                    "尚未完成剪映云空间校准。请在图形操作台点「校准云空间页面」按钮：\n"
                    "  会打开浏览器，请依次点开 我的→云空间→某个月份→某个项目文件夹\n"
                    "  录制完成后回到对话里让助手根据录制结果生成校准配置\n"
                    f"配置将写入 {self.selectors_path}（模板见同名 .example 文件）"
                )
            self._selectors = json.loads(self.selectors_path.read_text(encoding="utf-8"))
            missing = [key for key in SUPPORTED_SELECTOR_KEYS if key not in self._selectors]
            if missing:
                logger.warning("校准文件缺少选择器: %s", missing)
        return self._selectors

    # ------------------------------------------------------------------
    def discover(self) -> list[Project]:
        sync_playwright = _require_playwright()
        selectors = self.selectors
        with sync_playwright() as playwright:
            browser, context, _ = self.session._launch(playwright)
            page = context.new_page()
            page.goto(selectors.get("cloud_url", CLOUD_URL_HINT), wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

            projects: list[Project] = []
            months = page.query_selector_all(selectors.get("month_item", "[data-autocut-month]"))
            for month_el in months:
                month_name = (month_el.get_attribute("data-name") or month_el.inner_text()).strip()
                month_el.click()
                page.wait_for_timeout(1500)
                items = page.query_selector_all(selectors.get("project_item", "[data-autocut-project]"))
                for item_el in items:
                    project_name = (item_el.get_attribute("data-name") or item_el.inner_text()).strip()
                    if not project_name:
                        continue
                    projects.append(Project(
                        id=f"{month_name}-{project_name}".replace(" ", ""),
                        name=project_name,
                        month=month_name,
                        source_path=f"capcut://cloud/{month_name}/{project_name}",
                        status="DISCOVERED",
                    ))
                # 返回上级目录
                page.go_back(wait_until="domcontentloaded")
                page.wait_for_timeout(1200)
            self.session._save_state(context)
            browser.close()

        logger.info("云空间发现 %s 个项目", len(projects))
        return self.filter_projects(projects)

    # ------------------------------------------------------------------
    def fetch(self, project: Project) -> list[Material]:
        sync_playwright = _require_playwright()
        selectors = self.selectors
        target_dir = self.materials_root / (project.month or "unknown") / project.name
        target_dir.mkdir(parents=True, exist_ok=True)

        downloaded: list[Path] = []
        with sync_playwright() as playwright:
            browser, context, _ = self.session._launch(playwright)
            page = context.new_page()
            page.goto(selectors.get("cloud_url", CLOUD_URL_HINT), wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)

            if not self._open_project(page, project, selectors):
                browser.close()
                raise SourceError(f"在云空间中未定位到项目文件夹: {project.month}/{project.name}")

            elements = page.query_selector_all(selectors.get("material_item", "[data-autocut-material]"))
            if len(elements) > self.max_materials_per_project:
                logger.warning("素材数量 %s 超过单项目上限 %s，仅处理前若干条",
                               len(elements), self.max_materials_per_project)
                elements = elements[: self.max_materials_per_project]

            for index, element in enumerate(elements, start=1):
                try:
                    with page.expect_download(timeout=120000) as download_info:
                        element.click()
                        page.wait_for_timeout(500)
                        button = page.query_selector(selectors.get("download_button", "[data-autocut-download]"))
                        if button is None:
                            raise SourceError("未找到下载按钮，请检查校准配置 download_button")
                        button.click()
                    download = download_info.value
                    suffix = Path(download.suggested_filename).suffix or ".mp4"
                    target = target_dir / f"{index:03d}{suffix}"
                    download.save_as(str(target))
                    downloaded.append(target)
                except Exception as exc:
                    logger.warning("第 %s 个素材下载失败: %s", index, exc)

            self.session._save_state(context)
            browser.close()

        materials: list[Material] = []
        for index, path in enumerate(downloaded, start=1):
            material = probe.probe_material(path, f"{project.id}-m{index:03d}", project.id)
            materials.append(material)
        logger.info("项目「%s」从云空间同步 %s 个素材", project.name, len(materials))
        return materials

    def _open_project(self, page, project: Project, selectors: dict[str, Any]) -> bool:
        month_selector = selectors.get("month_item", "[data-autocut-month]")
        for month_el in page.query_selector_all(month_selector):
            name = (month_el.get_attribute("data-name") or month_el.inner_text()).strip()
            if project.month and name != project.month:
                continue
            month_el.click()
            page.wait_for_timeout(1500)
            for item_el in page.query_selector_all(selectors.get("project_item", "[data-autocut-project]")):
                item_name = (item_el.get_attribute("data-name") or item_el.inner_text()).strip()
                if item_name == project.name:
                    item_el.click()
                    page.wait_for_timeout(2500)
                    return True
            page.go_back(wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
        return False
