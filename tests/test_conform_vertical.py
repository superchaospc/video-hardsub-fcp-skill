"""conform-vertical.sh must deliver 1080x1920 without losing frames or audio."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_verify_video import have, synth

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "conform-vertical.sh"


def probe(path: Path) -> dict:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets", "-show_entries",
         "stream=codec_type,width,height,avg_frame_rate,nb_read_packets,sample_aspect_ratio",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True)
    streams = json.loads(completed.stdout)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    return {
        "size": (video["width"], video["height"]),
        "fps": video["avg_frame_rate"],
        "frames": video["nb_read_packets"],
        "audio": any(s["codec_type"] == "audio" for s in streams),
    }


@unittest.skipUnless(have("ffmpeg") and have("ffprobe"), "ffmpeg/ffprobe required")
class ConformVertical(unittest.TestCase):
    def conform(self, source: Path, output: Path):
        return subprocess.run([str(SCRIPT), str(source), str(output)],
                              capture_output=True, text=True)

    def test_any_resolution_becomes_1080x1920_with_every_frame(self):
        for width, height in ((720, 1280), (608, 1080), (1920, 1080)):
            with self.subTest(size=f"{width}x{height}"), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "cleaned.mp4"
                output = Path(tmp) / "delivery.mp4"
                synth(source, width, height)
                proc = self.conform(source, output)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                before, after = probe(source), probe(output)
                self.assertEqual(after["size"], (1080, 1920))
                self.assertEqual(after["frames"], before["frames"])
                self.assertEqual(after["fps"], before["fps"])
                self.assertTrue(after["audio"])

    def test_landscape_is_letterboxed_not_stretched(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "cleaned.mp4"
            output = Path(tmp) / "delivery.mp4"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                 "color=c=white:size=1920x1080:rate=30:duration=1",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                check=True, capture_output=True)
            self.assertEqual(self.conform(source, output).returncode, 0)
            rows = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-i", str(output), "-frames:v", "1",
                 "-vf", "scale=1:1920,format=gray", "-f", "rawvideo", "-"],
                check=True, capture_output=True).stdout
            self.assertLess(rows[100], 30, "top band must be black padding")
            self.assertGreater(rows[960], 220, "centre must be the picture")
            self.assertLess(rows[1820], 30, "bottom band must be black padding")

    def test_existing_1080x1920_is_stream_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "cleaned.mp4"
            output = Path(tmp) / "delivery.mp4"
            synth(source, 1080, 1920)
            proc = self.conform(source, output)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Conformed copy", proc.stdout)
            self.assertEqual(probe(output)["frames"], probe(source)["frames"])

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "cleaned.mp4"
            output = Path(tmp) / "delivery.mp4"
            synth(source, 720, 1280)
            output.write_bytes(b"keep")
            proc = self.conform(source, output)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(output.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
