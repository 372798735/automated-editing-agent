"""素材探测：优先 ffprobe，降级到纯 Python MP4 解析。

统一产出 Material 记录，并记录 probe_source 便于排查（ffprobe / mp4box / mock / unknown）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from ..errors import MediaError
from ..logging_setup import get_logger
from ..models import Material
from . import mp4box

logger = get_logger("media.probe")

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".flv", ".wmv", ".webm", ".ts", ".mpg", ".mpeg"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic"}

_FFPROBE_TIMEOUT = 30


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_SUFFIXES


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES


def is_media(path: Path) -> bool:
    return path.is_file() and (is_video(path) or is_image(path))


def file_checksum(path: Path, chunk_size: int = 1 << 20) -> str:
    """采样哈希：首尾 1MB + 文件大小，避免大视频全量读取。"""
    size = path.stat().st_size
    digest = hashlib.sha256()
    digest.update(str(size).encode())
    with path.open("rb") as fh:
        digest.update(fh.read(chunk_size))
        if size > chunk_size * 2:
            fh.seek(-chunk_size, 2)
            digest.update(fh.read(chunk_size))
    return digest.hexdigest()[:32]


def _probe_with_ffprobe(path: Path) -> dict | None:
    binary = shutil.which("ffprobe")
    if not binary:
        return None
    cmd = [
        binary, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_FFPROBE_TIMEOUT,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ffprobe 执行失败 %s: %s", path.name, exc)
        return None
    if proc.returncode != 0:
        logger.warning("ffprobe 返回错误码 %s: %s", proc.returncode, (proc.stderr or "").strip()[:200])
        return None
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None

    duration = float(payload.get("format", {}).get("duration", 0) or 0)
    width = height = 0
    fps = 0.0
    has_audio = False
    for stream in payload.get("streams", []):
        codec_type = stream.get("codec_type")
        if codec_type == "video" and width == 0:
            width = int(stream.get("width", 0) or 0)
            height = int(stream.get("height", 0) or 0)
            fps = _parse_fraction(stream.get("r_frame_rate") or stream.get("avg_frame_rate"))
            if duration == 0:
                duration = float(stream.get("duration", 0) or 0)
        elif codec_type == "audio":
            has_audio = True
    return {"duration_sec": duration, "width": width, "height": height, "fps": fps, "has_audio": has_audio}


def _parse_fraction(value: str | None) -> float:
    if not value or "/" not in value:
        try:
            return float(value or 0)
        except ValueError:
            return 0.0
    numerator, _, denominator = value.partition("/")
    try:
        den = float(denominator)
        return round(float(numerator) / den, 3) if den else 0.0
    except ValueError:
        return 0.0


def probe_material(path: Path, material_id: str, project_id: str, use_ffprobe: bool = True) -> Material:
    """探测单个素材文件，任何失败都降级而不是中断流水线。"""
    stat = path.stat()
    material = Material(
        id=material_id,
        project_id=project_id,
        name=path.name,
        path=str(path),
        file_type="video" if is_video(path) else "image",
        size_bytes=stat.st_size,
        checksum=file_checksum(path),
    )

    if material.file_type == "image":
        material.probe_source = "image-default"
        material.duration_sec = 3.0   # 图片素材按 3s 静帧处理
        return material

    info = _probe_with_ffprobe(path) if use_ffprobe else None
    if info:
        material.probe_source = "ffprobe"
        material.duration_sec = round(info["duration_sec"], 3)
        material.width = info["width"]
        material.height = info["height"]
        material.fps = info["fps"]
        material.has_audio = info["has_audio"]
        if material.duration_sec > 0 and material.width > 0:
            return material
        logger.warning("ffprobe 结果不完整，尝试内置解析: %s", path.name)

    try:
        parsed = mp4box.parse(path)
        material.probe_source = "mp4box"
        material.duration_sec = round(parsed.duration_sec, 3) or material.duration_sec
        material.width = parsed.width or material.width
        material.height = parsed.height or material.height
        material.fps = parsed.fps or material.fps
        material.has_audio = parsed.has_audio
    except mp4box.MP4ParseError as exc:
        logger.warning("内置 MP4 解析失败 %s: %s", path.name, exc)
        material.probe_source = "unknown"
        if material.duration_sec <= 0:
            # 最后兜底：用码率粗估时长，保证流水线不中断
            material.duration_sec = 5.0
            material.probe_source = "fallback-estimate"

    if material.width == 0:
        material.width, material.height = 1080, 1920
    return material


def probe_or_raise(path: Path, material_id: str, project_id: str) -> Material:
    if not path.exists():
        raise MediaError(f"素材文件不存在: {path}")
    material = probe_material(path, material_id, project_id)
    if material.duration_sec <= 0:
        raise MediaError(f"无法获取素材时长: {path}")
    return material
