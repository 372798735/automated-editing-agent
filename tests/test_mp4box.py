"""MP4 解析器单测：验证时长/分辨率/帧率/音轨识别与异常处理。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import write_minimal_mp4

from autocut.media import mp4box


class MP4BoxTest(unittest.TestCase):
    def test_parse_duration_resolution_and_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_minimal_mp4(Path(tmp) / "sample.mp4", duration_ms=12000, width=1080, height=1920)
            info = mp4box.parse(path)
            self.assertAlmostEqual(info.duration_sec, 12.0, places=2)
            self.assertEqual((info.width, info.height), (1080, 1920))
            self.assertTrue(info.has_video)
            self.assertTrue(info.has_audio)
            self.assertGreater(info.fps, 20)
            self.assertLess(info.fps, 40)

    def test_video_only_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_minimal_mp4(Path(tmp) / "silent.mp4", with_audio=False)
            info = mp4box.parse(path)
            self.assertTrue(info.has_video)
            self.assertFalse(info.has_audio)

    def test_rejects_non_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "garbage.mp4"
            path.write_bytes(b"not a video file at all, just text" * 4)
            with self.assertRaises(mp4box.MP4ParseError):
                mp4box.parse(path)

    def test_rejects_tiny_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny.mp4"
            path.write_bytes(b"\x00" * 8)
            with self.assertRaises(mp4box.MP4ParseError):
                mp4box.parse(path)


if __name__ == "__main__":
    unittest.main()
