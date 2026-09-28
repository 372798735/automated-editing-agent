"""文案生成引擎：模板填槽 + 可选 LLM 润色。

流程：
1. **变量抽取**：从项目名（如「阳光花园-张先生-全屋通铺」）解析出小区、客户称呼、工艺等变量；
2. **模板填槽**：从模板库选一个模板（可指定 id），用变量渲染标题、逐镜头字幕、话题标签；
3. **LLM 润色**（可选）：把模板产物交给大模型改写，要求保持结构与长度约束；
4. **合规过滤**：绝对化用语（最好/第一/100% 等）强制替换或剔除，输出前必过。

即使没有配置任何大模型，第 2、4 步也能独立产出可用的文案。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import AppConfig
from ..errors import CreativeError
from ..llm import build_client
from ..logging_setup import get_logger
from ..models import Copywriting

logger = get_logger("creative.copywriter")

CRAFT_KEYWORDS = ("通铺", "人字拼", "鱼骨拼", "多层", "强化", "实木", "三层", "锁扣", "拼花", "自流平")
CUSTOMER_TOKENS = ("先生", "女士", "小姐", "老师", "总", "哥", "姐")


@dataclass
class CopyTemplate:
    id: str
    name: str
    style: str
    title: str                  # 含 {变量}
    subtitles: list[str]        # 含 {变量}
    hashtags: list[str]
    outro: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "CopyTemplate":
        missing = [key for key in ("id", "name", "title", "subtitles") if key not in data]
        if missing:
            raise CreativeError(f"文案模板缺少字段 {missing}: {data.get('id') or data.get('name')}")
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            style=data.get("style", ""),
            title=data["title"],
            subtitles=list(data.get("subtitles", [])),
            hashtags=list(data.get("hashtags", [])),
            outro=data.get("outro", ""),
        )


def load_templates(path: Path) -> list[CopyTemplate]:
    if not path.exists():
        raise CreativeError(f"文案模板文件不存在: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise CreativeError("读取 YAML 模板需要 PyYAML，请改用 .json 模板") from exc
        payload = yaml.safe_load(text)
    else:
        payload = json.loads(text)
    items = payload.get("templates") if isinstance(payload, dict) else payload
    if not items:
        raise CreativeError(f"模板文件为空: {path}")
    return [CopyTemplate.from_dict(item) for item in items]


def load_template_files(base_file: Path, extra_dir: Path | None = None) -> list[CopyTemplate]:
    """加载内置模板文件 + 自定义模板目录（config/templates/*.json）。

    自定义模板与内置 id 相同时**覆盖**内置（方便不改内置文件做个性化微调）；
    某个自定义文件坏了只跳过该文件，不影响其它模板。
    """
    merged: dict[str, CopyTemplate] = {}
    for template in load_templates(base_file):
        merged[template.id] = template
    if extra_dir is not None and extra_dir.is_dir():
        for file in sorted(extra_dir.glob("*.json")):
            try:
                for template in load_templates(file):
                    if template.id in merged:
                        logger.info("自定义模板「%s」覆盖内置同名模板", template.id)
                    merged[template.id] = template
            except (CreativeError, OSError, ValueError) as exc:
                logger.warning("忽略无法加载的自定义模板 %s: %s", file.name, exc)
    return list(merged.values())


# ---------------------------------------------------------------------------
# 项目名解析
# ---------------------------------------------------------------------------


def extract_variables(project_name: str, month: str = "", extra: dict[str, str] | None = None) -> dict[str, str]:
    """从项目名解析文案变量，解析不出来的一律给中性默认值（不编造事实）。"""
    parts = [p for p in re.split(r"[-_—/\s]+", project_name.strip()) if p]
    community = parts[0] if parts else "本项目"
    customer = ""
    craft = ""

    for part in parts[1:]:
        if not customer and any(token in part for token in CUSTOMER_TOKENS):
            customer = part
        if not craft and any(keyword in part for keyword in CRAFT_KEYWORDS):
            craft = part
    if not craft and parts:
        craft = parts[-1] if parts[-1] != community else "地板铺装"
    if not customer:
        customer = "业主"

    variables = {
        "project": project_name,
        "community": community,
        "customer": customer,
        "craft": craft,
        "month": month or "",
        "brand": "本店",
        "area": "",
        "advantage": "平整无空鼓、接缝细密",
    }
    if extra:
        variables.update({key: value for key, value in extra.items() if value})
    return variables


def _render(text: str, variables: dict[str, str]) -> str:
    """安全填槽：未提供的变量保持中性替换，不残留 {xxx}。"""
    def replace(match: re.Match) -> str:
        key = match.group(1)
        return variables.get(key, "")

    rendered = re.sub(r"\{(\w+)\}", replace, text)
    return re.sub(r"\s{2,}", " ", rendered).strip(" -—·") or text


# ---------------------------------------------------------------------------
# 生成器
# ---------------------------------------------------------------------------


class Copywriter:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.templates = load_template_files(
            config.resolve_path(config.creative.template_file),
            config.root / "config" / "templates",
        )
        self.client = build_client(config.llm) if config.llm.enabled else None

    # ------------------------------------------------------------------
    def pick_template(self, template_id: str | None = None) -> CopyTemplate:
        wanted = template_id or self.config.creative.default_template_id
        for template in self.templates:
            if template.id == wanted:
                return template
        logger.warning("未找到模板 %s，使用第一个模板 %s", wanted, self.templates[0].id)
        return self.templates[0]

    # ------------------------------------------------------------------
    def generate(
        self,
        project_id: str,
        project_name: str,
        clip_count: int = 0,
        month: str = "",
        extra_variables: dict[str, str] | None = None,
        template_id: str | None = None,
        use_llm: bool | None = None,
    ) -> Copywriting:
        """生成文案。

        注意：这里**不做**字幕数量的补齐/截断——最终镜头数只有时间线编排阶段才知道，
        补齐交给 `TimelineOrchestrator` 完成（它还会把首尾文案锚定到开场/收尾镜头）。
        clip_count 只作为 LLM 润色时的长度提示。
        """
        template = self.pick_template(template_id)
        variables = extract_variables(project_name, month, extra_variables)

        title = _render(template.title, variables)
        subtitles = [_render(item, variables) for item in template.subtitles]
        hashtags = [_render(item, variables) for item in template.hashtags]
        generator = "template"

        want_llm = self.config.llm.enabled if use_llm is None else use_llm
        if want_llm and self.client is not None:
            polished = self._polish(template, variables, title, subtitles, hashtags, clip_count)
            if polished:
                title = polished.get("title") or title
                polished_subtitles = polished.get("subtitles") or []
                if polished_subtitles:
                    subtitles = [str(item) for item in polished_subtitles]
                polished_tags = polished.get("hashtags") or []
                if polished_tags:
                    hashtags = [str(item) for item in polished_tags]
                generator = "template+llm"

        subtitles = [self._sanitize(item) for item in subtitles]
        title = self._sanitize(title)
        hashtags = [self._sanitize(tag) for tag in hashtags if tag][: self.config.creative.max_hashtags]

        return Copywriting(
            project_id=project_id,
            title=title,
            subtitles=subtitles,
            hashtags=hashtags,
            template_id=template.id,
            generator=generator,
            variables=variables,
        )

    # ------------------------------------------------------------------
    def _sanitize(self, text: str) -> str:
        cleaned = text
        for word in self.config.creative.banned_words:
            if word and word in cleaned:
                cleaned = cleaned.replace(word, "")
                logger.info("文案命中禁用词「%s」已剔除", word)
        return re.sub(r"\s{2,}", " ", cleaned).strip(" ，,、-—")

    # ------------------------------------------------------------------
    def _polish(
        self,
        template: CopyTemplate,
        variables: dict[str, str],
        title: str,
        subtitles: list[str],
        hashtags: list[str],
        clip_count: int = 0,
    ) -> dict | None:
        target_count = max(len(subtitles), clip_count or 0)
        prompt = f"""你在为地板铺装施工账号写短视频文案。请基于下面的模板初稿做润色。

项目信息：
- 小区/楼盘：{variables.get('community')}
- 客户称呼：{variables.get('customer')}
- 工艺类型：{variables.get('craft')}

模板初稿：
标题：{title}
字幕：{' | '.join(subtitles)}
话题：{' '.join(hashtags)}

要求：
1. 保持工程展示风格，客观描述施工内容，不要夸张承诺、不用绝对化用语（最好、第一、100%、永久等）；
2. 标题不超过 12 个汉字（竖屏一行最多容纳约 12 字，超宽会溢出屏幕）；
3. 字幕逐条对应镜头，共 {target_count} 条（第一条用于开场、最后一条用于收尾），每条不超过 12 个汉字；
4. 话题标签 3-5 个，不带 # 符号；
5. 只输出 JSON：{{"title":"...","subtitles":["..."],"hashtags":["..."]}}"""
        return self.client.chat_json(
            [
                {"role": "system", "content": "你是资深短视频编导，输出严格 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=self.config.llm.temperature,
        )
