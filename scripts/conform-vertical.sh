#!/usr/bin/env bash
set -euo pipefail

# Conform cleaned media to the vertical 1080x1920 delivery format.
# The picture is scaled to fit and centred on black; it is never cropped or
# stretched. Audio is copied, and every video frame is kept with its timestamp.

DELIVERY_WIDTH=1080
DELIVERY_HEIGHT=1920

usage() { printf 'Usage: %s CLEANED OUTPUT\nOUTPUT must not exist and must use the same MP4/MOV extension as CLEANED.\n' "$0" >&2; }
die() { printf 'conform-vertical: %s\n' "$*" >&2; exit 1; }
[[ $# -eq 2 ]] || { usage; exit 2; }
for tool in python3 ffmpeg ffprobe; do command -v "$tool" >/dev/null 2>&1 || die "required tool not found: $tool"; done
input=$1; output=$2

extension() { printf '%s' "${1##*.}" | tr '[:upper:]' '[:lower:]'; }
[[ -f "$input" && ! -L "$input" ]] || die "cleaned media must be a regular non-symlink file"
ext=$(extension "$input")
[[ $ext == mp4 || $ext == mov ]] || die "cleaned media must be MP4/MOV"
[[ $(extension "$output") == "$ext" ]] || die "output must keep the .$ext extension so audio can be copied"
[[ ! -e "$output" && ! -L "$output" ]] || die "output already exists (refusing to overwrite): $output"
parent=$(dirname "$output"); [[ -d "$parent" && ! -L "$parent" ]] || die "output directory must exist and not be a symlink"
parent=$(cd "$parent" && pwd -P); output="$parent/$(basename "$output")"

stage_dir=$(mktemp -d "$parent/.$(basename "$output").stage.XXXXXX")
cleanup() { [[ -z ${stage_dir:-} ]] || rm -rf -- "$stage_dir"; }
trap cleanup EXIT HUP INT TERM
stage="$stage_dir/conformed.$ext"

# Prints "copy" when the media is already a square-pixel, unrotated 1080x1920 picture.
mode=$(ffprobe -v error -select_streams v:0 -show_streams -of json "$input" | python3 -c '
import json,sys
w,h=int(sys.argv[1]),int(sys.argv[2])
streams=json.load(sys.stdin).get("streams",[])
if not streams: raise SystemExit("no video stream")
v=streams[0]
sar=str(v.get("sample_aspect_ratio","1:1"))
rotation=any("rotation" in s and round(float(s["rotation"]))%360 for s in v.get("side_data_list",[]))
rotation=rotation or str(v.get("tags",{}).get("rotate","0")) not in ("0","")
square=sar in ("1:1","0:1","N/A")
print("copy" if (int(v["width"]),int(v["height"]))==(w,h) and square and not rotation else "scale")
' "$DELIVERY_WIDTH" "$DELIVERY_HEIGHT") || die "ffprobe could not read cleaned media"

if [[ $mode == copy ]]; then
  ffmpeg -nostdin -v error -i "$input" -map 0:v:0 -map '0:a?' -c copy -map_metadata 0 \
    -movflags +faststart "$stage" || die "stream copy failed"
else
  filter="scale=w='ceil(iw*sar/2)*2':h=ih,setsar=1"
  filter+=",scale=${DELIVERY_WIDTH}:${DELIVERY_HEIGHT}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos"
  filter+=",pad=${DELIVERY_WIDTH}:${DELIVERY_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p"
  ffmpeg -nostdin -v error -i "$input" -map 0:v:0 -map '0:a?' -vf "$filter" -fps_mode passthrough \
    -c:v libx264 -preset slow -crf 16 -c:a copy -map_metadata 0 -movflags +faststart "$stage" \
    || die "re-encode to ${DELIVERY_WIDTH}x${DELIVERY_HEIGHT} failed"
fi

count_frames() {
  ffprobe -v error -select_streams v:0 -count_packets -show_entries stream=nb_read_packets -of csv=p=0 "$1"
}
input_frames=$(count_frames "$input") || die "could not count cleaned media frames"
output_frames=$(count_frames "$stage") || die "could not count conformed frames"
[[ $input_frames == "$output_frames" ]] || die "frame count changed while conforming: $input_frames -> $output_frames"
out_size=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0:s=x "$stage") || die "could not probe conformed media"
[[ $out_size == "${DELIVERY_WIDTH}x${DELIVERY_HEIGHT}" ]] || die "conformed media is $out_size, not ${DELIVERY_WIDTH}x${DELIVERY_HEIGHT}"

python3 - "$stage" "$output" <<'PY' || die "output appeared concurrently; nothing was published"
import ctypes,os,sys
src,dst=map(os.fsencode,sys.argv[1:]);libc=ctypes.CDLL(None,use_errno=True)
result=libc.renamex_np(src,dst,0x00000004) if sys.platform=='darwin' else libc.renameat2(-100,src,-100,dst,1)
if result:
    code=ctypes.get_errno();raise OSError(code,os.strerror(code))
PY
printf 'Conformed %s (%s, %s frames) -> %s\n' "$mode" "${DELIVERY_WIDTH}x${DELIVERY_HEIGHT}" "$output_frames" "$output"
