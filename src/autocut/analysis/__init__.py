"""镜头分析层。"""

from .base import AnalyzeContext, ShotAnalyzer
from .heuristic import HeuristicAnalyzer

__all__ = ["AnalyzeContext", "ShotAnalyzer", "HeuristicAnalyzer", "build_analyzer"]


def build_analyzer(context: AnalyzeContext) -> ShotAnalyzer:
    """按配置选择分析器；vlm 不可用时自动退化为启发式。"""
    provider = (context.config.analysis.provider or "heuristic").strip().lower()
    if provider in {"vlm", "auto"} and context.config.llm.vision_enabled:
        from .vlm import VLMShotAnalyzer
        if context.config.llm.resolve_api_key():
            return VLMShotAnalyzer(context)
        context.warnings.append("配置要求使用 VLM，但未配置 API Key，已回退到启发式分析")
    if provider == "vlm":
        context.warnings.append("配置要求使用 VLM，但 vision_enabled=false，已回退到启发式分析")
    return HeuristicAnalyzer(context)
