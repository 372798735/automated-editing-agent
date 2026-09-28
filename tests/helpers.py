"""测试公共工具：构造最小可解析的 MP4 文件、构造合成镜头。"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autocut.models import Material, Shot, ShotType  # noqa: E402


def _box(box_type: str, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, box_type.encode("ascii")) + payload


def _mvhd(timescale: int = 1000, duration: int = 12000) -> bytes:
    payload = struct.pack(">B3s", 0, b"\x00\x00\x00")
    payload += struct.pack(">IIII", 0, 0, timescale, duration)
    payload += struct.pack(">I", 0x00010000)      # rate
    payload += struct.pack(">H", 0x0100)          # volume
    payload += b"\x00" * 10
    payload += b"\x00" * 36                       # matrix
    payload += b"\x00" * 24                       # predefined
    payload += struct.pack(">I", 2)               # next track id
    return _box("mvhd", payload)


def _tkhd(width: int = 1080, height: int = 1920, duration: int = 12000) -> bytes:
    payload = struct.pack(">B3s", 0, b"\x00\x00\x07")
    payload += struct.pack(">IIII", 0, 0, 1, 0)
    payload += struct.pack(">I", duration)
    payload += b"\x00" * 8
    payload += struct.pack(">hhhh", 0, 0, 0, 0)
    payload += b"\x00" * 36
    payload += struct.pack(">II", width << 16, height << 16)
    return _box("tkhd", payload)


def _mdhd(timescale: int = 30000, duration: int = 360000) -> bytes:
    payload = struct.pack(">B3s", 0, b"\x00\x00\x00")
    payload += struct.pack(">IIII", 0, 0, timescale, duration)
    payload += struct.pack(">HH", 0x55C4, 0)
    return _box("mdhd", payload)


def _hdlr(handler: str) -> bytes:
    payload = struct.pack(">B3s", 0, b"\x00\x00\x00")
    payload += struct.pack(">I", 0)
    payload += handler.encode("ascii")
    payload += b"\x00" * 12
    payload += b"handler\x00"
    return _box("hdlr", payload)


def _stts(sample_count: int = 360, delta: int = 1000) -> bytes:
    payload = struct.pack(">B3s", 0, b"\x00\x00\x00")
    payload += struct.pack(">I", 1)
    payload += struct.pack(">II", sample_count, delta)
    return _box("stts", payload)


def write_minimal_mp4(
    path: Path,
    duration_ms: int = 12000,
    width: int = 1080,
    height: int = 1920,
    with_audio: bool = True,
) -> Path:
    """写一个仅含元数据、不含 mdat 的 MP4（足够验证解析器逻辑）。"""
    timescale = 1000
    duration = duration_ms
    video_trak = _box("trak", _tkhd(width, height, duration) + _box(
        "mdia", _mdhd(30000, duration * 30) + _hdlr("vide") + _box(
            "minf", _box("stbl", _stts(duration * 30 // 1000, 1000))
        )
    ))
    children = _mvhd(timescale, duration) + video_trak
    if with_audio:
        audio_hdlr = _hdlr("soun")
        children += _box("trak", _tkhd(0, 0, duration) + _box("mdia", _mdhd(44100, duration * 44) + audio_hdlr))
    data = _box("ftyp", b"isom\x00\x00\x02\x00isomiso2") + _box("moov", children)
    path.write_bytes(data)
    return path


def make_material(path: Path, project_id: str = "test-project", index: int = 1,
                  duration: float = 20.0, width: int = 1080, height: int = 1920) -> Material:
    return Material(
        id=f"{project_id}-m{index:03d}",
        project_id=project_id,
        name=path.name,
        path=str(path),
        file_type="video",
        duration_sec=duration,
        width=width,
        height=height,
        fps=30.0,
        size_bytes=path.stat().st_size if path.exists() else 1_000_000,
        checksum=f"checksum{index:03d}",
        probe_source="mp4box",
        has_audio=True,
    )


def make_shot(
    shot_id: str,
    material_id: str,
    material_path: str,
    start: float,
    end: float,
    shot_type: str = ShotType.PROCESS,
    quality: float = 70.0,
    aesthetic: float = 70.0,
) -> Shot:
    return Shot(
        id=shot_id,
        material_id=material_id,
        material_path=material_path,
        start_sec=start,
        end_sec=end,
        shot_type=shot_type,
        quality_score=quality,
        aesthetic_score=aesthetic,
        analyzer="heuristic",
    )
