#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/../.." && pwd -P)
for tool in ffmpeg ffprobe python3 unzip shasum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'synthetic integration: required tool not found: %s\n' "$tool" >&2
    exit 1
  }
done

work=$(mktemp -d "${TMPDIR:-/tmp}/video-hardsub-fcp-integration.XXXXXX")
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT HUP INT TERM

job="$work/private-job"
delivery="$work/delivery"
mkdir "$job" "$delivery"
source_media="$job/source.mp4"
cleaned_media="$job/cleaned.mp4"
cut_plan="$job/cut-plan.json"
review_dir="$job/review"
fcpxml="$job/project.fcpxml"
verify_dir="$job/verify"
manifest="$job/package-manifest.json"
archive="$delivery/deliverables.zip"
unpacked="$delivery/unpacked"

# Three one-second, 30 fps sections have both different fields and a moving
# box position.  Lossless H.264 keeps the two intended boundaries at frames 30
# and 60 while avoiding encoder-inserted scene-change keyframes as extra hints.
ffmpeg -nostdin -v error \
  -f lavfi -i "color=c=0x991111:s=320x180:r=30:d=1" \
  -f lavfi -i "color=c=0x114499:s=320x180:r=30:d=1" \
  -f lavfi -i "color=c=0x228833:s=320x180:r=30:d=1" \
  -f lavfi -i "anullsrc=r=48000:cl=stereo:d=3" \
  -filter_complex \
    "[0:v]drawbox=x=24:y=54:w=56:h=72:color=white:t=fill,format=yuv420p[v0];\
[1:v]drawbox=x=132:y=36:w=56:h=108:color=yellow:t=fill,format=yuv420p[v1];\
[2:v]drawbox=x=240:y=54:w=56:h=72:color=white:t=fill,format=yuv420p[v2];\
[v0][v1][v2]concat=n=3:v=1:a=0[v]" \
  -map '[v]' -map 3:a:0 -t 3 \
  -c:v libx264 -crf 0 -preset ultrafast -g 300 -keyint_min 300 -sc_threshold 0 \
  -c:a aac -b:a 128k -movflags +faststart "$source_media"

# The integration treats this distinct file as the already-cleaned result.
# A byte copy preserves frame timing while giving the packager a distinct inode.
cp "$source_media" "$cleaned_media"

python3 "$repo_root/scripts/analyze-cuts.py" "$cleaned_media" \
  --output "$cut_plan" \
  --review-dir "$review_dir" \
  --mode fine

python3 - "$cut_plan" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
plan = json.loads(path.read_text(encoding="utf-8"))
candidates = plan["candidates"]
frames = [int(candidate["frame"]) for candidate in candidates]
fps = float(plan["fps"])
boundaries = [round(fps), round(2 * fps)]

for boundary in boundaries:
    if not any(abs(frame - boundary) <= 2 for frame in frames):
        raise SystemExit(
            f"missing candidate within two frames of boundary {boundary}: {frames}"
        )

selected = {
    min(frames, key=lambda frame: (abs(frame - boundary), frame))
    for boundary in boundaries
}
plan["selected_frames"] = sorted(selected)
plan["rejected_candidates"] = [
    {"frame": frame, "reason": "no-visible-discontinuity"}
    for frame in frames
    if frame not in selected
]
path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")

decisions = plan["selected_frames"] + [
    item["frame"] for item in plan["rejected_candidates"]
]
if sorted(decisions) != sorted(frames) or len(decisions) != len(set(decisions)):
    raise SystemExit("candidate review is not a complete, exclusive partition")
PY

python3 "$repo_root/scripts/build-fcpxml.py" \
  "$cleaned_media" "$cut_plan" \
  --output "$fcpxml" \
  --event-name "Synthetic integration"

python3 - "$fcpxml" "$cleaned_media" <<'PY'
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction


def fcpx_time(value: str) -> Fraction:
    if not value.endswith("s"):
        raise ValueError(f"not an FCP time: {value}")
    return Fraction(value[:-1])


xml_path, media_path = sys.argv[1:]
root = ET.parse(xml_path).getroot()
clips = root.findall("./library/event/project/sequence/spine/asset-clip")
if len(clips) < 3:
    raise SystemExit(f"expected at least three timeline clips, found {len(clips)}")
clip_duration = sum((fcpx_time(clip.attrib["duration"]) for clip in clips), Fraction())

probe = subprocess.run(
    [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate:format=duration",
        "-of", "json", media_path,
    ],
    check=True,
    capture_output=True,
    text=True,
)
metadata = json.loads(probe.stdout)
media_duration = Fraction(metadata["format"]["duration"])
fps = Fraction(metadata["streams"][0]["avg_frame_rate"])
one_frame = Fraction(1, 1) / fps
if abs(clip_duration - media_duration) > one_frame:
    raise SystemExit(
        f"timeline duration {clip_duration} differs from media {media_duration} "
        f"by more than one frame ({one_frame})"
    )
PY

bash "$repo_root/scripts/verify-video.sh" \
  "$cleaned_media" "$verify_dir" --source "$source_media"

python3 - "$manifest" "$source_media" "$cleaned_media" "$fcpxml" \
  "$cut_plan" "$verify_dir/report.json" <<'PY'
import json
import sys
from pathlib import Path

manifest, source, cleaned, fcpxml, cuts, verification = map(Path, sys.argv[1:])
payload = {
    "version": 1,
    "entries": [
        {
            "source": str(source.resolve()),
            "status": "complete",
            "deliverables": {
                "cleaned": str(cleaned.resolve()),
                "fcpxml": str(fcpxml.resolve()),
                "cuts": str(cuts.resolve()),
                "verification": str(verification.resolve()),
            },
        }
    ],
}
manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

bash "$repo_root/scripts/package-deliverables.sh" "$manifest" "$archive"
unzip -tq "$archive" >/dev/null

# A delivery is only self-contained if it still resolves after all working
# artifacts have moved away from the paths embedded by the build tools.
unavailable="$work/private-job-unavailable"
mv "$job" "$unavailable"
mkdir "$unpacked"
unzip -q "$archive" -d "$unpacked"
(
  cd "$unpacked"
  shasum -a 256 -c SHA256SUMS >/dev/null
)
for expected in \
  manifest.json \
  SHA256SUMS \
  entry-001/cleaned.mp4 \
  entry-001/project.fcpxml \
  entry-001/cut-plan.json \
  entry-001/verification.json; do
  [[ -f "$unpacked/$expected" ]] || {
    printf 'synthetic integration: ZIP is missing %s\n' "$expected" >&2
    exit 1
  }
done

python3 - "$unpacked" "$work" <<'PY'
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit

root = Path(sys.argv[1]).resolve()
private_root = str(Path(sys.argv[2]).resolve())
xml_path = root / "entry-001/project.fcpxml"
media_rep = ET.parse(xml_path).find("./resources/asset/media-rep")
if media_rep is None:
    raise SystemExit("packaged FCPXML has no media-rep")
uri = media_rep.attrib.get("src", "")
parts = urlsplit(uri)
if parts.scheme or parts.netloc or Path(unquote(parts.path)).is_absolute():
    raise SystemExit(f"packaged FCPXML media URI is not relative: {uri!r}")
resolved = (xml_path.parent / unquote(parts.path)).resolve()
expected = (root / "entry-001/cleaned.mp4").resolve()
if resolved != expected or not resolved.is_file():
    raise SystemExit(f"packaged FCPXML resolves outside its entry: {resolved}")
subprocess.run(
    ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=index", str(resolved)],
    check=True,
    capture_output=True,
)

plan = json.loads((root / "entry-001/cut-plan.json").read_text(encoding="utf-8"))
if plan.get("source") != "cleaned.mp4":
    raise SystemExit("packaged cut plan does not use its archive media name")
if not plan.get("selected_frames"):
    raise SystemExit("packaged cut plan lost approved frame decisions")

for relative in (
    "manifest.json",
    "entry-001/project.fcpxml",
    "entry-001/cut-plan.json",
    "entry-001/verification.json",
):
    if private_root in (root / relative).read_text(encoding="utf-8"):
        raise SystemExit(f"packaged text leaks the private work path: {relative}")
PY

apple_dtd=$(find /Applications -path '*FCPXMLv1_10.dtd' -print -quit 2>/dev/null || true)
if [[ -n "$apple_dtd" ]] && command -v xmllint >/dev/null 2>&1; then
  xmllint --noout --dtdvalid "$apple_dtd" "$unpacked/entry-001/project.fcpxml"
fi

printf 'synthetic integration passed\n'
