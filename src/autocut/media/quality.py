"""镜头画质打分。

两套评分：
1. `score_window`：有 ffmpeg 帧统计时，按亮度/对比度/清晰度/稳定度打分；
2. `score_from_metadata`：无 ffmpeg 时，用分辨率、码率、时长做启发式打分，
   并基于 checksum 生成稳定的确定性扰动，保证同一素材每次运行结果一致（可复现）。

评分口径统一为 0-100，越高越适合入选成片。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..models import Material
from .frames import WindowAnalysis

IDEAL_BRIGHTNESS_LOW = 65.0
IDEAL_BRIGHTNESS_HIGH = 175.0
SHARPNESS_REFERENCE = 22.0     # 经验值：160x90 灰度图的水平梯度均值上限参考
MOTION_COMFORT = 6.0           # 低于该值视为稳定镜头


@dataclass
class Score:
    quality: float
    aesthetic: float
    detail: dict[str, float]

    def to_dict(self) -> dict:
        return {"quality": round(self.quality, 2), "aesthetic": round(self.aesthetic, 2), "detail": self.detail}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _brightness_score(brightness: float) -> float:
    if IDEAL_BRIGHTNESS_LOW <= brightness <= IDEAL_BRIGHTNESS_HIGH:
        return 100.0
    if brightness < IDEAL_BRIGHTNESS_LOW:
        return _clamp(brightness / IDEAL_BRIGHTNESS_LOW * 100)
    return _clamp(100 - (brightness - IDEAL_BRIGHTNESS_HIGH) / (255 - IDEAL_BRIGHTNESS_HIGH) * 100)


def _sharpness_score(sharpness: float) -> float:
    return _clamp(sharpness / SHARPNESS_REFERENCE * 100)


def _stability_score(motion: float) -> float:
    if motion <= MOTION_COMFORT:
        return 100.0
    return _clamp(100 - (motion - MOTION_COMFORT) * 4.0)


def score_window(window: WindowAnalysis) -> Score:
    """基于帧统计打分。window 不可用时返回低分并标记原因。"""
    if not window.available:
        return Score(quality=0.0, aesthetic=0.0, detail={"reason": 1.0})

    brightness = window.mean("brightness")
    contrast = window.mean("contrast")
    sharpness = window.mean("sharpness")
    black_ratio = window.mean("black_ratio")

    brightness_score = _brightness_score(brightness)
    sharpness_score = _sharpness_score(sharpness)
    stability_score = _stability_score(window.motion)
    contrast_score = _clamp(contrast / 60.0 * 100)

    quality = (
        brightness_score * 0.35
        + sharpness_score * 0.35
        + stability_score * 0.20
        + contrast_score * 0.10
    )
    if black_ratio > 0.5:
        quality *= 0.3
    elif black_ratio > 0.2:
        quality *= 0.7

    # 观感分：更看重亮度舒适度与清晰度，弱化稳定度
    aesthetic = brightness_score * 0.4 + sharpness_score * 0.4 + contrast_score * 0.2

    return Score(
        quality=round(_clamp(quality), 2),
        aesthetic=round(_clamp(aesthetic), 2),
        detail={
            "brightness": round(brightness, 2),
            "contrast": round(contrast, 2),
            "sharpness": round(sharpness, 2),
            "black_ratio": round(black_ratio, 4),
            "motion": round(window.motion, 2),
            "brightness_score": round(brightness_score, 2),
            "sharpness_score": round(sharpness_score, 2),
            "stability_score": round(stability_score, 2),
        },
    )


def _deterministic_unit(seed: str, salt: str = "") -> float:
    """由字符串派生 [0,1) 的稳定伪随机数（用于无语义信息时的确定性扰动）。"""
    digest = hashlib.sha256(f"{seed}|{salt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


def score_from_metadata(material: Material, position_ratio: float = 0.5) -> Score:
    """无 ffmpeg 时的元数据启发式打分。

    依据：像素码率（每像素每秒比特数）反映画面信息量，分辨率反映拍摄规格，
    位置比例用于近似「结尾常为完工全景」这一业务经验。
    """
    duration = max(0.1, material.duration_sec)
    pixels = max(1, material.width * material.height)
    bitrate = material.size_bytes * 8 / duration
    bitrate_per_pixel = bitrate / pixels

    # 手机拍摄经验区间：0.02-0.35 bit/pixel/s
    bitrate_score = _clamp(bitrate_per_pixel / 0.25 * 100)
    resolution_score = _clamp((material.width * material.height) / (1080 * 1920) * 100)
    # 尾部素材略加分（完工全景），过短素材扣分
    position_bonus = 6.0 if position_ratio > 0.75 else 0.0
    duration_penalty = 12.0 if duration < 3.0 else 0.0

    jitter = (_deterministic_unit(material.checksum or material.name, "quality") - 0.5) * 8

    quality = _clamp(
        bitrate_score * 0.5 + resolution_score * 0.35 + 15 + position_bonus + jitter - duration_penalty
    )
    aesthetic = _clamp(quality * 0.9 + (_deterministic_unit(material.checksum or material.name, "aes") - 0.5) * 10)

    return Score(
        quality=round(quality, 2),
        aesthetic=round(aesthetic, 2),
        detail={
            "bitrate_per_pixel": round(bitrate_per_pixel, 5),
            "bitrate_score": round(bitrate_score, 2),
            "resolution_score": round(resolution_score, 2),
            "position_bonus": position_bonus,
            "source": 0.0,
        },
    )
