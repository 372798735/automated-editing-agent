"""素材接入层。"""

from .base import MaterialSource, SourceOptions
from .mock import MockSource

__all__ = ["MaterialSource", "SourceOptions", "MockSource", "build_source"]


def build_source(kind: str, **kwargs) -> MaterialSource:
    """按名称构造素材源，避免上层直接依赖具体实现。"""
    normalized = kind.strip().lower()
    if normalized in {"local", "dir", "folder"}:
        from .local import LocalSource
        return LocalSource(**kwargs)
    if normalized in {"mock", "demo"}:
        return MockSource(**kwargs)
    if normalized in {"capcut", "capcut-web", "cloud"}:
        from .capcut_web import CapCutWebSource
        return CapCutWebSource(**kwargs)
    raise ValueError(f"未知素材源类型: {kind}（可选：local / mock / capcut-web）")
