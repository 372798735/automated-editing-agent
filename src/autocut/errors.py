"""AutoCut 统一异常体系。

所有可预期的业务错误都继承 AutoCutError，CLI 层捕获后输出友好提示并以非 0 退出码结束，
避免把 Python 栈回溯直接抛给运营人员。
"""

from __future__ import annotations


class AutoCutError(Exception):
    """所有 AutoCut 业务异常的基类。"""

    exit_code = 1


class ConfigError(AutoCutError):
    """配置文件缺失、格式错误或字段非法。"""


class SourceError(AutoCutError):
    """素材接入失败（目录不存在、云空间授权失效等）。"""


class CapabilityError(AutoCutError):
    """所需能力不可用（缺少 ffmpeg、缺少可选依赖等）。"""

    exit_code = 3


class MediaError(AutoCutError):
    """媒体探测或解析失败。"""


class AnalysisError(AutoCutError):
    """镜头分析失败。"""


class CreativeError(AutoCutError):
    """文案生成或配乐匹配失败。"""


class DraftBuildError(AutoCutError):
    """剪映草稿生成或校验失败。"""


class DraftValidationError(DraftBuildError):
    """草稿内部一致性校验未通过。"""
