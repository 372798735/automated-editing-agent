"""AutoCut Agent —— 面向剪映云空间素材的智能剪辑流水线（MVP）。

分层：
    sources  素材接入（本地 / mock / 剪映云空间）
    media    媒体探测与镜头切分
    analysis 镜头理解与打分（启发式 / 多模态 VLM）
    creative 文案生成与配乐匹配
    timeline 时间线编排
    draft    剪映专业版草稿生成与校验
    render   FFmpeg 渲染脚本导出（V2 兜底通道）
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
