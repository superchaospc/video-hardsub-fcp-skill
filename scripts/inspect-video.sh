#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s INPUT_VIDEO OUTPUT_DIR\nOUTPUT_DIR must not already exist (artifacts publish atomically).\n' "$0" >&2
}

die() {
  printf 'inspect-video: %s\n' "$*" >&2
  exit 1
}

[[ $# -eq 2 ]] || { usage; exit 2; }
for tool in python3 ffmpeg ffprobe; do
  command -v "$tool" >/dev/null 2>&1 || die "required tool not found: $tool"
done

input=$1
output_dir=$2
[[ -f "$input" && ! -L "$input" ]] || die "input must be a regular, non-symlink file"
case ${input##*.} in
  [mM][pP]4|[mM][oO][vV]) ;;
  *) die "input must have an .mp4 or .mov extension" ;;
esac

path_info=$(python3 - "$input" "$output_dir" <<'PY'
import os
import sys

source = os.path.realpath(sys.argv[1])
output = os.path.abspath(sys.argv[2])
if os.path.lexists(output) and os.path.islink(output):
    raise SystemExit("output directory must not be a symlink")
output_real = os.path.realpath(output)
if source == output_real:
    raise SystemExit("output directory aliases the input file")
if output_real.startswith(source + os.sep):
    raise SystemExit("output directory cannot be nested beneath the input file")
print(source)
print(output)
PY
) || die "unsafe input/output path relationship"
input_real=${path_info%%$'\n'*}
output_abs=${path_info#*$'\n'}

[[ ! -e "$output_abs" && ! -L "$output_abs" ]] || die "output directory already exists; choose a new path"
output_parent=$(dirname "$output_abs")
mkdir -p "$output_parent" || die "could not create output parent directory"
[[ ! -L "$output_parent" ]] || die "output parent must not be a symlink"
output_parent=$(cd "$output_parent" && pwd -P)
output_abs="$output_parent/$(basename "$output_abs")"
stage=$(mktemp -d "$output_parent/.$(basename "$output_abs").stage.XXXXXX")
chmod 700 "$stage"
probe_tmp="$stage/probe.json"
full_tmp="$stage/contact-full.png"
band_tmp="$stage/contact-subtitle-band.png"
cleanup() { [[ -z ${stage:-} ]] || rm -rf -- "$stage"; }
trap cleanup EXIT HUP INT TERM

if ! ffprobe -v error -show_format -show_streams -of json "$input_real" >"$probe_tmp"; then
  die "ffprobe could not read the input video"
fi
duration=$(python3 - "$probe_tmp" <<'PY'
import json
import math
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    probe = json.load(handle)
videos = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
if not videos:
    raise SystemExit("no video stream")
duration = probe.get("format", {}).get("duration") or videos[0].get("duration")
try:
    duration = float(duration)
except (TypeError, ValueError):
    raise SystemExit("missing duration")
if not math.isfinite(duration) or duration <= 0:
    raise SystemExit("invalid duration")
print(f"{duration:.9f}")
PY
) || die "input has no valid video stream or positive duration"

# Upsampling to 16 evenly-spaced timestamps is intentional: it also produces a
# complete sheet for clips shorter than one second, while the last sample stays
# within one sixteenth of the end of the media.
sample_filter="fps=fps=16/${duration}:start_time=0:round=up"
full_filter="$sample_filter,scale=w='max(1,ceil(iw*sar))':h=ih:flags=lanczos,setsar=1,scale=320:180:force_original_aspect_ratio=decrease:flags=lanczos,pad=320:180:(ow-iw)/2:(oh-ih)/2:color=black,tile=4x4:padding=2:margin=2:color=white,setsar=1"
band_filter="format=yuv444p,crop=iw:ceil(ih*0.35):0:ih-ceil(ih*0.35),$sample_filter,scale=w='max(1,ceil(iw*sar))':h=ih:flags=lanczos,setsar=1,scale=320:62:force_original_aspect_ratio=decrease:flags=lanczos,pad=320:62:(ow-iw)/2:(oh-ih)/2:color=black,tile=4x4:padding=2:margin=2:color=white,setsar=1"

ffmpeg -nostdin -y -v error -i "$input_real" -map 0:v:0 -vf "$full_filter" \
  -frames:v 1 -f image2 "$full_tmp" || die "failed to render full-frame contact sheet"
ffmpeg -nostdin -y -v error -i "$input_real" -map 0:v:0 -vf "$band_filter" \
  -frames:v 1 -f image2 "$band_tmp" || die "failed to render subtitle-band contact sheet"

python3 - "$stage" "$output_abs" <<'PY' || die "output appeared concurrently; no artifacts were published"
import ctypes, os, sys
source, destination = map(os.fsencode, sys.argv[1:])
libc = ctypes.CDLL(None, use_errno=True)
if sys.platform == "darwin":
    result = libc.renamex_np(source, destination, 0x00000004)
else:
    result = libc.renameat2(-100, source, -100, destination, 1)
if result:
    code = ctypes.get_errno()
    raise OSError(code, os.strerror(code))
PY
stage=''
trap - EXIT HUP INT TERM

printf 'Probe: %s\nFull-frame sheet: %s\nSubtitle-band sheet: %s\n' \
  "$output_abs/probe.json" \
  "$output_abs/contact-full.png" \
  "$output_abs/contact-subtitle-band.png"
