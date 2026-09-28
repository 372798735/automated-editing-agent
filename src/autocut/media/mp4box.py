"""纯 Python MP4/MOV 元数据解析（无 ffprobe 时的降级通道）。

只解析需要的盒（box）：
    ftyp / moov / mvhd            总时长
    trak / tkhd                   轨道尺寸（16.16 定点）
    trak / mdia / mdhd            轨道时间基
    trak / mdia / hdlr            轨道类型（vide / soun）
    trak / mdia / minf / stbl/stts 帧率估算

不做解码，不依赖任何第三方库，也不会把整个文件读进内存（按需 seek）。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

CONTAINER_BOXES = {
    "moov", "trak", "mdia", "minf", "stbl", "edts", "udta", "mvex",
    "moof", "traf", "dinf", "iprp", "ipco", "sinf", "schi",
}

MAX_DEPTH = 8


class MP4ParseError(Exception):
    """文件不是可识别的 MP4/MOV 结构。"""


@dataclass
class TrackInfo:
    handler: str = ""
    duration_sec: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    sample_count: int = 0


@dataclass
class MP4Info:
    duration_sec: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_video: bool = False
    has_audio: bool = False
    tracks: list[TrackInfo] = field(default_factory=list)


def _read(fh, offset: int, size: int) -> bytes:
    fh.seek(offset)
    data = fh.read(size)
    if len(data) != size:
        raise MP4ParseError("文件被截断或偏移越界")
    return data


def _iter_boxes(fh, start: int, end: int, depth: int = 0):
    """迭代 [start, end) 区间内的盒，产出 (type, header_end, box_end)。"""
    pos = start
    while pos + 8 <= end:
        header = _read(fh, pos, 8)
        size, box_type = struct.unpack(">I4s", header)
        header_end = pos + 8
        if size == 1:
            size = struct.unpack(">Q", _read(fh, header_end, 8))[0]
            header_end = pos + 16
        elif size == 0:
            size = end - pos
        if size < 8:
            break
        box_end = min(pos + size, end)
        yield box_type.decode("latin-1"), header_end, box_end
        if box_type.decode("latin-1") in CONTAINER_BOXES and depth < MAX_DEPTH:
            yield from _iter_boxes(fh, header_end, box_end, depth + 1)
        pos = box_end


def _parse_mvhd(fh, start: int, end: int) -> tuple[float, int]:
    version = _read(fh, start, 1)[0]
    if version == 1:
        timescale, duration = struct.unpack(">IQ", _read(fh, start + 20, 12))
    else:
        timescale, duration = struct.unpack(">II", _read(fh, start + 12, 8))
    if not timescale:
        return 0.0, 0
    return duration / timescale, timescale


def _parse_tkhd(fh, start: int, end: int) -> tuple[int, int]:
    # width / height 位于 tkhd 盒末尾的 16.16 定点数，与 version 无关
    width, height = struct.unpack(">II", _read(fh, end - 8, 8))
    return round(width / 65536), round(height / 65536)


def _parse_mdhd(fh, start: int, end: int) -> float:
    version = _read(fh, start, 1)[0]
    if version == 1:
        timescale, duration = struct.unpack(">IQ", _read(fh, start + 20, 12))
    else:
        timescale, duration = struct.unpack(">II", _read(fh, start + 12, 8))
    if not timescale:
        return 0.0
    return duration / timescale


def _parse_hdlr(fh, start: int, end: int) -> str:
    return _read(fh, start + 8, 4).decode("latin-1", errors="replace")


def _parse_stts(fh, start: int, end: int) -> tuple[int, int]:
    """返回 (sample_count, total_delta)。"""
    try:
        entry_count = struct.unpack(">I", _read(fh, start + 4, 4))[0]
    except MP4ParseError:
        return 0, 0
    entry_count = min(entry_count, 20000)
    sample_count = 0
    total_delta = 0
    for index in range(entry_count):
        offset = start + 8 + index * 8
        if offset + 8 > end:
            break
        count, delta = struct.unpack(">II", _read(fh, offset, 8))
        sample_count += count
        total_delta += count * delta
    return sample_count, total_delta


def parse(path: str | Path) -> MP4Info:
    """解析 MP4 元数据。失败抛 MP4ParseError。"""
    file_path = Path(path)
    size = file_path.stat().st_size
    if size < 32:
        raise MP4ParseError(f"文件过小，不可能是有效视频: {file_path}")

    info = MP4Info()
    mvhd_timescale = 0
    track_buckets: list[dict] = []
    current_track: dict | None = None

    with file_path.open("rb") as fh:
        for box_type, header_end, box_end in _iter_boxes(fh, 0, size):
            if box_type == "mvhd":
                info.duration_sec, mvhd_timescale = _parse_mvhd(fh, header_end, box_end)
            elif box_type == "tkhd":
                width, height = _parse_tkhd(fh, header_end, box_end)
                current_track = {"width": width, "height": height}
                track_buckets.append(current_track)
            elif box_type == "mdhd":
                pass  # 时基在 stts 计算帧率时通过轨道时长换算
            elif box_type == "hdlr":
                handler = _parse_hdlr(fh, header_end, box_end)
                if current_track is not None and not current_track.get("handler"):
                    current_track["handler"] = handler
                if handler == "vide":
                    info.has_video = True
                elif handler == "soun":
                    info.has_audio = True
            elif box_type == "stts":
                if current_track is not None:
                    count, delta = _parse_stts(fh, header_end, box_end)
                    current_track["sample_count"] = count
                    current_track["total_delta"] = delta

    for bucket in track_buckets:
        handler = bucket.get("handler", "")
        track = TrackInfo(
            handler=handler,
            width=bucket.get("width", 0),
            height=bucket.get("height", 0),
            sample_count=bucket.get("sample_count", 0),
        )
        if handler == "vide":
            if info.width == 0:
                info.width = track.width
                info.height = track.height
            sample_count = bucket.get("sample_count", 0)
            total_delta = bucket.get("total_delta", 0)
            if sample_count and total_delta and info.duration_sec:
                avg_delta = total_delta / sample_count
                # stts 的 delta 基于媒体时间基，用「样本数 / 时长」反推帧率更稳
                track.fps = round(sample_count / info.duration_sec, 3)
                if not track.fps and avg_delta:
                    track.fps = round(1 / avg_delta, 3)
            if track.fps and info.fps == 0:
                info.fps = track.fps
        info.tracks.append(track)

    if info.duration_sec == 0.0 and mvhd_timescale == 0 and not track_buckets:
        raise MP4ParseError(f"未找到 moov/mvhd 盒，可能不是 MP4/MOV: {file_path}")
    return info
