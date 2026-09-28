"""基于 FFmpeg 的帧级分析（可选增强通道）。

设计要点：**不依赖 numpy/PIL/OpenCV**。
- 通过 `-vf scale=W:H,format=gray -f rawvideo -` 把帧降采样成灰度原始字节，
  在纯 Python 里统计亮度均值、对比度、边缘能量（清晰度代理）与帧间差异（稳定度代理）。
- 场景切换点通过 `select='gt(scene,TH)' + showinfo` 解析时间戳获得。

ffmpeg 不可用时，本模块所有函数返回空结果，由上层退回「元数据启发式」分析。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..logging_setup import get_logger

logger = get_logger("media.frames")

ANALYZE_WIDTH = 160
ANALYZE_HEIGHT = 90
_TIMEOUT = 60
_SHOWINFO_PTS = re.compile(r"pts_time:([0-9]+\.?[0-9]*)")


@dataclass
class FrameStats:
    """单帧的轻量统计量。"""

    brightness: float = 0.0     # 0-255 灰度均值
    contrast: float = 0.0       # 灰度标准差
    sharpness: float = 0.0      # 相邻像素梯度均值（越高越清晰）
    black_ratio: float = 0.0    # 极暗像素占比
    available: bool = False

    def to_dict(self) -> dict[str, float]:
        return {
            "brightness": round(self.brightness, 2),
            "contrast": round(self.contrast, 2),
            "sharpness": round(self.sharpness, 2),
            "black_ratio": round(self.black_ratio, 4),
        }


@dataclass
class WindowAnalysis:
    """一个时间窗（候选镜头）的整体分析结果。"""

    start_sec: float
    end_sec: float
    frames: list[FrameStats] = field(default_factory=list)
    motion: float = 0.0          # 帧间平均绝对差，越大越晃/运动越剧烈
    source: str = "none"

    @property
    def duration_sec(self) -> float:
        return round(self.end_sec - self.start_sec, 3)

    @property
    def available(self) -> bool:
        return any(f.available for f in self.frames)

    def mean(self, attr: str) -> float:
        values = [getattr(f, attr) for f in self.frames if f.available]
        return sum(values) / len(values) if values else 0.0

    def to_dict(self) -> dict:
        return {
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "motion": round(self.motion, 2),
            "source": self.source,
            "frames": [f.to_dict() for f in self.frames],
        }


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _run(cmd: list[str], timeout: int = _TIMEOUT) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("ffmpeg 调用失败: %s", exc)
        return None


def extract_gray_frame(
    path: Path,
    timestamp: float,
    width: int = ANALYZE_WIDTH,
    height: int = ANALYZE_HEIGHT,
) -> bytes | None:
    """抽取指定时间点的灰度缩略帧（rawvideo，无依赖）。"""
    binary = shutil.which("ffmpeg")
    if not binary:
        return None
    cmd = [
        binary, "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, timestamp):.3f}", "-i", str(path),
        "-frames:v", "1",
        "-vf", f"scale={width}:{height},format=gray",
        "-f", "rawvideo", "-",
    ]
    proc = _run(cmd)
    expected = width * height
    if not proc or proc.returncode != 0 or len(proc.stdout) < expected:
        return None
    return proc.stdout[:expected]


def compute_frame_stats(data: bytes, width: int = ANALYZE_WIDTH, height: int = ANALYZE_HEIGHT) -> FrameStats:
    """纯 Python 灰度统计：亮度/对比度/边缘能量/暗像素占比。"""
    if not data:
        return FrameStats()
    total = len(data)
    mean = sum(data) / total
    variance = sum((value - mean) ** 2 for value in data) / total

    # 水平梯度均值作为清晰度代理
    gradient_sum = 0
    gradient_count = 0
    for row in range(height):
        base = row * width
        for col in range(1, width):
            gradient_sum += abs(data[base + col] - data[base + col - 1])
            gradient_count += 1
    sharpness = gradient_sum / gradient_count if gradient_count else 0.0
    black_ratio = sum(1 for value in data if value < 16) / total

    return FrameStats(
        brightness=mean,
        contrast=variance ** 0.5,
        sharpness=sharpness,
        black_ratio=black_ratio,
        available=True,
    )


def mean_abs_diff(a: bytes, b: bytes) -> float:
    if not a or not b:
        return 0.0
    length = min(len(a), len(b))
    if length == 0:
        return 0.0
    return sum(abs(a[i] - b[i]) for i in range(length)) / length


def detect_scene_cuts(path: Path, threshold: float = 0.35, max_scan_sec: float | None = None) -> list[float]:
    """返回场景切换时间点（升序，不含 0）。"""
    binary = shutil.which("ffmpeg")
    if not binary:
        return []
    cmd = [binary, "-hide_banner", "-nostats", "-loglevel", "info"]
    if max_scan_sec:
        cmd += ["-t", f"{max_scan_sec:.3f}"]
    cmd += [
        "-i", str(path),
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-an", "-f", "null", "-",
    ]
    proc = _run(cmd, timeout=180)
    if not proc:
        return []
    output = (proc.stderr or b"").decode("utf-8", errors="replace")
    cuts: list[float] = []
    for line in output.splitlines():
        if "showinfo" not in line:
            continue
        match = _SHOWINFO_PTS.search(line)
        if match:
            value = float(match.group(1))
            if value > 0.05:
                cuts.append(round(value, 3))
    return sorted(set(cuts))


def analyze_window(
    path: Path,
    start_sec: float,
    end_sec: float,
    sample_count: int = 3,
) -> WindowAnalysis:
    """对 [start, end) 采样若干帧，产出该候选镜头的画面统计。"""
    window = WindowAnalysis(start_sec=round(start_sec, 3), end_sec=round(end_sec, 3))
    if not ffmpeg_available() or end_sec - start_sec <= 0.2:
        return window

    span = end_sec - start_sec
    timestamps = [
        start_sec + span * (index + 0.5) / max(1, sample_count)
        for index in range(max(1, sample_count))
    ]
    frames: list[bytes] = []
    for timestamp in timestamps:
        data = extract_gray_frame(path, timestamp)
        if data is None:
            continue
        window.frames.append(compute_frame_stats(data))
        frames.append(data)

    if len(frames) >= 2:
        window.motion = sum(
            mean_abs_diff(frames[i], frames[i + 1]) for i in range(len(frames) - 1)
        ) / (len(frames) - 1)
    if window.frames:
        window.source = "ffmpeg"
    return window


def extract_thumbnail(path: Path, timestamp: float, out_path: Path, width: int = 360) -> bool:
    """导出缩略图（供 HTML 故事板预览使用）。"""
    binary = shutil.which("ffmpeg")
    if not binary:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        binary, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{max(0.0, timestamp):.3f}", "-i", str(path),
        "-frames:v", "1", "-vf", f"scale={width}:-2", str(out_path),
    ]
    proc = _run(cmd)
    return bool(proc and proc.returncode == 0 and out_path.exists())


def extract_jpeg_bytes(path: Path, timestamp: float, width: int = 512) -> bytes | None:
    """抽取 JPEG 字节（供多模态大模型调用，避免依赖 Pillow）。"""
    binary = shutil.which("ffmpeg")
    if not binary:
        return None
    cmd = [
        binary, "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, timestamp):.3f}", "-i", str(path),
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-c:v", "mjpeg", "-q:v", "4",
        "-f", "image2", "-",
    ]
    proc = _run(cmd)
    if not proc or proc.returncode != 0 or len(proc.stdout) < 512:
        return None
    return proc.stdout
