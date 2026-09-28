"""生成占位背景音乐（仅标准库，无需 ffmpeg）。

用途：让「配乐」这条链路在没有真实音乐素材时也能端到端验证。
生成的是简单和弦琶音，只是为了让草稿音频轨有真实可读的文件，
正式发布请替换成你自己有商用授权的音乐。

    python scripts/make_placeholder_music.py
"""

from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "assets" / "music"

SAMPLE_RATE = 22050


def _adsr(index: int, total: int, attack: float = 0.02, release: float = 0.35) -> float:
    """简单包络，避免爆音。"""
    position = index / max(1, total - 1)
    if position < attack:
        return position / attack
    if position > 1 - release:
        return max(0.0, (1 - position) / release)
    return 1.0


def render_chord_progression(path: Path, chords: list[list[float]], note_sec: float = 1.2,
                             loops: int = 12, volume: float = 0.32) -> Path:
    frames = bytearray()
    for loop in range(loops):
        for chord_index, chord in enumerate(chords):
            total_samples = int(SAMPLE_RATE * note_sec)
            for index in range(total_samples):
                time = index / SAMPLE_RATE
                envelope = _adsr(index, total_samples)
                value = 0.0
                for octave, frequency in enumerate(chord):
                    amplitude = 1.0 / (octave + 1)
                    value += amplitude * math.sin(2 * math.pi * frequency * time)
                    # 轻微颤音，听起来不至于太机械
                    value += 0.08 * amplitude * math.sin(2 * math.pi * frequency * 2 * time)
                value = value / max(1, len(chord)) * envelope * volume
                frames += struct.pack("<h", int(max(-1.0, min(1.0, value)) * 32767))

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(bytes(frames))
    return path


# 干净利落：C - G - Am - F，中速
CLEAN_CHORDS = [
    [261.63, 392.00],
    [196.00, 392.00],
    [220.00, 329.63],
    [174.61, 349.23],
]

# 温暖生活：F - Am - Dm - C，偏低音区
WARM_CHORDS = [
    [174.61, 261.63],
    [220.00, 329.63],
    [146.83, 293.66],
    [261.63, 392.00],
]


def main() -> int:
    clean = render_chord_progression(OUT_DIR / "clean_01.wav", CLEAN_CHORDS, note_sec=1.1, loops=14)
    warm = render_chord_progression(OUT_DIR / "warm_01.wav", WARM_CHORDS, note_sec=1.3, loops=12)
    for path in (clean, warm):
        size_mb = path.stat().st_size / 1024 / 1024
        with wave.open(str(path)) as handle:
            duration = handle.getnframes() / handle.getframerate()
        print(f"已生成 {path.name}  时长 {duration:.1f}s  大小 {size_mb:.2f}MB")
    print("\n提示：assets/music/ 下的生成文件仅用于功能验证，正式使用请替换为有商用授权的音乐，")
    print("      并把 config/music_library.json 里的 path 改成你的文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
