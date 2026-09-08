"""verify-video.sh must not accept a vacuous source comparison."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify-video.sh"


def have(tool: str) -> bool:
    return subprocess.run(["command", "-v", tool], shell=False,
                          capture_output=True,
                          executable="/bin/bash").returncode == 0 or subprocess.run(
        f"command -v {tool}", shell=True, capture_output=True).returncode == 0


def synth(path: Path, width: int, height: int) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=30:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True, capture_output=True)


@unittest.skipUnless(have("ffmpeg") and have("ffprobe"), "ffmpeg/ffprobe required")
class VerifyVideoSourceIdentity(unittest.TestCase):
    def run_verify(self, cleaned: Path, source: Path, out: Path):
        proc = subprocess.run(
            [str(SCRIPT), str(cleaned), str(out), "--source", str(source)],
            capture_output=True, text=True)
        report = json.loads((out / "report.json").read_text()) if (out / "report.json").exists() else {}
        return proc, report

    def test_byte_identical_copy_is_rejected(self):
        """A copy of the delivered file is not evidence the geometry survived."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cleaned = root / "cleaned.mp4"
            synth(cleaned, 608, 1080)
            source = root / "source.mp4"
            source.write_bytes(cleaned.read_bytes())

            proc, report = self.run_verify(cleaned, source, root / "verify")

            self.assertNotEqual(proc.returncode, 0,
                                "identical content must not pass source comparison")
            self.assertIn("source_identity", report.get("checks", {}))
            self.assertFalse(report["checks"]["source_identity"]["pass"])

    def test_distinct_source_still_compares(self):
        """A genuine differing source keeps the existing geometry comparison."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cleaned = root / "cleaned.mp4"
            source = root / "source.mp4"
            synth(cleaned, 608, 1080)
            synth(source, 720, 1280)

            proc, report = self.run_verify(cleaned, source, root / "verify")

            self.assertNotEqual(proc.returncode, 0, "downscaled output must fail geometry")
            self.assertTrue(report["checks"]["source_identity"]["pass"])
            self.assertFalse(report["checks"]["source_display_geometry"]["pass"])


if __name__ == "__main__":
    unittest.main()
