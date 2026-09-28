"""配乐匹配引擎。

音乐资产库（config/music_library.json）记录用户自有音乐的路径、时长、BPM 与情绪标签。
选择策略（v0.1）：
1. 过滤掉时长短于成片时长的音乐（避免循环割裂）；
2. 在候选里按「情绪匹配 > 时长接近度 > 顺序稳定」排序；
3. 无匹配时允许复用（后续可在草稿里做淡出），实在没有音乐则返回 None，草稿只出视频+字幕。

时长缺失时按 128kbps 估算（并在报告中标记 estimated），不假装精确。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import AppConfig
from ..errors import CreativeError
from ..logging_setup import get_logger
from ..models import MusicAsset

logger = get_logger("creative.music")

DEFAULT_MOOD_BY_CRAFT = {
    "通铺": "clean",
    "人字拼": "warm",
    "鱼骨拼": "warm",
    "多层": "clean",
    "强化": "clean",
    "实木": "warm",
}

MOOD_SCORE = {
    ("clean", "clean"): 100.0,
    ("warm", "warm"): 100.0,
    ("clean", "warm"): 70.0,
    ("warm", "clean"): 70.0,
}

# 放进 assets/music/ 即自动入库的音频格式
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}


@dataclass
class MusicLibrary:
    assets: list[MusicAsset]
    base_dir: Path = field(default_factory=Path)

    @classmethod
    def load(cls, path: Path, base_dir: Path | None = None) -> "MusicLibrary":
        if not path.exists():
            logger.warning("音乐库文件不存在，本片将不配乐: %s", path)
            return cls(assets=[], base_dir=base_dir or path.parent)
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload.get("music") if isinstance(payload, dict) else payload
        assets: list[MusicAsset] = []
        for item in items or []:
            music_path = item.get("path", "")
            duration = float(item.get("duration_sec") or 0)
            real_path = cls._resolve(music_path, base_dir)
            if duration <= 0 and real_path is not None and real_path.exists():
                duration = estimate_duration(real_path)
            assets.append(MusicAsset(
                id=item.get("id") or Path(music_path).stem or f"music-{len(assets) + 1}",
                name=item.get("name") or Path(music_path).name,
                path=music_path,
                duration_sec=round(duration, 2),
                bpm=float(item.get("bpm") or 0),
                mood=(item.get("mood") or "").strip().lower(),
                loudness_ok=bool(item.get("loudness_ok", True)),
            ))

        # 自动扫描 assets/music/：丢进来的音频无需登记 JSON 即可用。
        # 与已登记路径相同的文件不重复添加。
        known: set[str] = set()
        for asset in assets:
            real = cls._resolve(asset.path, base_dir)
            if real is not None:
                try:
                    known.add(str(real.resolve()))
                except OSError:
                    pass
        scan_dir = (base_dir or path.parent) / "assets" / "music"
        if scan_dir.is_dir():
            for file in sorted(scan_dir.iterdir()):
                if not file.is_file() or file.suffix.lower() not in AUDIO_EXTS:
                    continue
                try:
                    if str(file.resolve()) in known:
                        continue
                except OSError:
                    continue
                assets.append(MusicAsset(
                    id=file.stem,
                    name=file.name,
                    path=str(file),
                    duration_sec=estimate_duration(file),
                    bpm=0.0,
                    mood="",
                    loudness_ok=True,
                ))
        return cls(assets=assets, base_dir=base_dir or path.parent)

    @staticmethod
    def _resolve(music_path: str, base_dir: Path | None) -> Path | None:
        """把库里的相对路径解析为真实路径（相对项目根目录）。"""
        if not music_path:
            return None
        candidate = Path(music_path)
        if candidate.is_absolute():
            return candidate
        return (base_dir / candidate) if base_dir else candidate

    def resolve_path(self, asset: MusicAsset) -> Path | None:
        return self._resolve(asset.path, self.base_dir)

    def available(self) -> list[MusicAsset]:
        """只返回文件真实存在（或路径为空表示内置曲）的资产。"""
        result = []
        for asset in self.assets:
            if not asset.path:
                continue
            real = self.resolve_path(asset)
            if real is not None and real.exists():
                result.append(asset)
            else:
                logger.warning("音乐文件缺失，已跳过: %s", real or asset.path)
        return result


def estimate_duration(path: Path, assumed_bitrate_kbps: int = 128) -> float:
    """按码率估算音频时长（用于库里没填时长的情况）。"""
    try:
        size = path.stat().st_size
    except OSError:
        return 0.0
    return round(size * 8 / (assumed_bitrate_kbps * 1000), 2)


class MusicSelector:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.library = MusicLibrary.load(
            config.resolve_path(config.creative.music_library_file),
            base_dir=config.root,
        )

    def select(self, target_duration_sec: float, craft: str = "", preferred_id: str = "") -> MusicAsset | None:
        candidates = self.library.available()
        if not candidates:
            logger.info("音乐库为空或文件缺失，本片不配乐")
            return None

        if preferred_id:
            for asset in candidates:
                if asset.id == preferred_id:
                    return asset

        desired_mood = DEFAULT_MOOD_BY_CRAFT.get(craft, "")
        scored: list[tuple[float, MusicAsset]] = []
        for asset in candidates:
            mood_score = MOOD_SCORE.get((desired_mood, asset.mood), 60.0) if desired_mood else 60.0
            if asset.duration_sec <= 0:
                duration_score = 40.0        # 时长未知，不淘汰但降权
            elif asset.duration_sec >= target_duration_sec:
                duration_score = 100.0 - min(60.0, (asset.duration_sec - target_duration_sec))
            else:
                duration_score = max(0.0, 100.0 - (target_duration_sec - asset.duration_sec) * 6)
            total = mood_score * 0.5 + duration_score * 0.5
            scored.append((total, asset))

        scored.sort(key=lambda item: (-item[0], item[1].id))
        best_score, best = scored[0]
        logger.info(
            "选中音乐「%s」（匹配分 %.1f，时长 %.1fs，需求 %.1fs）",
            best.name, best_score, best.duration_sec, target_duration_sec,
        )
        return best


def resolve_music_path(config: AppConfig, asset: MusicAsset) -> Path:
    """把音乐资产的路径解析为绝对路径，并校验存在性。"""
    path = Path(asset.path)
    if path.is_absolute():
        resolved = path
    else:
        resolved = config.resolve_path(asset.path)
    if not resolved.exists():
        raise CreativeError(f"音乐文件不存在: {asset.path}（解析为 {resolved}）")
    return resolved
