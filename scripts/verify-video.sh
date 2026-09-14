#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

usage() { printf 'Usage: %s CLEANED VERIFY_DIR [--source SOURCE] [--delivery-size WIDTHxHEIGHT]\nVERIFY_DIR must not already exist (artifacts publish atomically).\nWith --delivery-size, geometry is checked against that size instead of the source.\n' "$0" >&2; }
die() { printf 'verify-video: %s\n' "$*" >&2; exit 1; }
[[ $# -ge 2 ]] || { usage; exit 2; }
cleaned=$1; verify_dir=$2; source=''; delivery_size=''; shift 2
while [[ $# -gt 0 ]]; do
  case $1 in
    --source) [[ $# -ge 2 && -z $source ]] || { usage; exit 2; }; source=$2; shift 2 ;;
    --delivery-size) [[ $# -ge 2 && -z $delivery_size && $2 =~ ^[1-9][0-9]*x[1-9][0-9]*$ ]] || { usage; exit 2; }; delivery_size=$2; shift 2 ;;
    *) usage; exit 2 ;;
  esac
done
for tool in python3 ffmpeg ffprobe; do command -v "$tool" >/dev/null 2>&1 || die "required tool not found: $tool"; done
[[ ! -e "$verify_dir" && ! -L "$verify_dir" ]] || die "verification directory already exists; choose a new path"
parent=$(dirname "$verify_dir"); mkdir -p "$parent" || die "could not create verification parent"; [[ ! -L "$parent" ]] || die "verification parent must not be a symlink"
parent=$(cd "$parent" && pwd -P); verify_dir="$parent/$(basename "$verify_dir")"
stage=$(mktemp -d "$parent/.$(basename "$verify_dir").stage.XXXXXX"); chmod 700 "$stage"
cleanup() { [[ -z ${stage:-} ]] || rm -rf -- "$stage"; }; trap cleanup EXIT HUP INT TERM

publish() {
  python3 - "$stage" "$verify_dir" <<'PY' || die "verification output appeared concurrently; no artifacts were published"
import ctypes,os,sys
src,dst=map(os.fsencode,sys.argv[1:]);libc=ctypes.CDLL(None,use_errno=True)
result=libc.renamex_np(src,dst,0x00000004) if sys.platform=='darwin' else libc.renameat2(-100,src,-100,dst,1)
if result:
    code=ctypes.get_errno();raise OSError(code,os.strerror(code))
PY
  stage=''; trap - EXIT HUP INT TERM
}
fail_report() {
  local check=$1 message=$2
  rm -f -- "$stage/.source-probe.json"
  python3 - "$stage/report.json" "$check" "$message" <<'PY'
import json,sys
with open(sys.argv[1],'w',encoding='utf-8') as h:json.dump({'version':1,'pass':False,'checks':{sys.argv[2]:{'pass':False,'error':sys.argv[3]}}},h,indent=2,sort_keys=True);h.write('\n')
PY
  publish
  printf 'verify-video: verification failed; inspect %s/report.json\n' "$verify_dir" >&2
  exit 1
}
valid_path() { [[ -f "$1" && ! -L "$1" ]] && case ${1##*.} in [mM][pP]4|[mM][oO][vV]) return 0;; *) return 1;; esac; }
valid_path "$cleaned" || fail_report cleaned_path "cleaned must be a regular non-symlink MP4/MOV"
if [[ -n "$source" ]]; then valid_path "$source" || fail_report source_path "source must be a regular non-symlink MP4/MOV"; fi
if [[ -n "$source" ]] && python3 - "$cleaned" "$source" <<'PY'
import os,sys
a=os.stat(sys.argv[1]);b=os.stat(sys.argv[2]);raise SystemExit(0 if os.path.realpath(sys.argv[1])==os.path.realpath(sys.argv[2]) or (a.st_dev,a.st_ino)==(b.st_dev,b.st_ino) else 1)
PY
then fail_report source_alias "cleaned and source must be distinct files"; fi
source_identical=false
if [[ -n "$source" ]] && python3 - "$cleaned" "$source" <<'PY'
import hashlib,os,sys
def digest(p):
    h=hashlib.sha256()
    with open(p,'rb') as fh:
        for chunk in iter(lambda:fh.read(1024*1024),b''):h.update(chunk)
    return h.digest()
a,b=sys.argv[1:3]
raise SystemExit(0 if os.stat(a).st_size==os.stat(b).st_size and digest(a)==digest(b) else 1)
PY
then source_identical=true; fi

clean_probe="$stage/probe.json"; source_probe=''
ffprobe -v error -show_format -show_streams -of json "$cleaned" >"$clean_probe" 2>/dev/null || fail_report cleaned_probe "ffprobe could not read cleaned media"
if [[ -n "$source" ]]; then source_probe="$stage/.source-probe.json"; ffprobe -v error -show_format -show_streams -of json "$source" >"$source_probe" 2>/dev/null || fail_report source_probe "ffprobe could not read source media"; fi
decode_pass=true; ffmpeg -nostdin -v error -xerror -i "$cleaned" -f null - || decode_pass=false
render_pass=true
ffmpeg -nostdin -v error -i "$cleaned" -map 0:v:0 -vf "fps=1:start_time=0:round=up,scale=w='max(1,ceil(iw*sar))':h=ih:flags=lanczos,setsar=1,scale=240:135:force_original_aspect_ratio=decrease:flags=lanczos,pad=240:135:(ow-iw)/2:(oh-ih)/2:color=black,tile=6x8:nb_frames=48:padding=2:margin=2:color=white,setsar=1" -fps_mode passthrough -f image2 "$stage/sheet-%03d.png" || render_pass=false
sheets=("$stage"/sheet-*.png); [[ ${#sheets[@]} -gt 0 ]] || render_pass=false
sheet_count=${#sheets[@]}

result=$(python3 - "$clean_probe" "$source_probe" "$decode_pass" "$render_pass" "$sheet_count" "$source_identical" "$stage/report.json" "$delivery_size" <<'PY'
from fractions import Fraction
import json,math,sys
cp,sp,decode,render,count,identical,out,delivery=sys.argv[1:]
def load(p):
    with open(p,encoding='utf-8') as h:return json.load(h)
def ratio(v):
    try:
        r=Fraction(str(v));return r if r>0 else None
    except (ValueError,ZeroDivisionError,TypeError):return None
def facts(p):
    streams=p.get('streams',[]);v=next((x for x in streams if x.get('codec_type')=='video'),None)
    if not v:raise ValueError('no video stream')
    w=int(v.get('width',0));h=int(v.get('height',0));
    if w<=0 or h<=0:raise ValueError('invalid dimensions')
    duration=float(p.get('format',{}).get('duration') or v.get('duration'))
    if not math.isfinite(duration) or duration<=0:raise ValueError('invalid duration')
    fps=ratio(v.get('avg_frame_rate')) or ratio(v.get('r_frame_rate'))
    if fps is None:raise ValueError('invalid frame rate')
    sar=ratio(str(v.get('sample_aspect_ratio','1:1')).replace(':','/')) or Fraction(1)
    rotation=0
    for side in v.get('side_data_list',[]):
        if 'rotation' in side:rotation=int(round(float(side['rotation'])))%360
    if not rotation:
        try:rotation=int(v.get('tags',{}).get('rotate',0))%360
        except ValueError:rotation=0
    dw=Fraction(w)*sar;dh=Fraction(h)
    if rotation in (90,270):dw,dh=dh,dw
    return {'width':w,'height':h,'duration_seconds':duration,'fps':float(fps),'has_audio':any(x.get('codec_type')=='audio' for x in streams),'rotation':rotation,'sar':str(sar.numerator)+':'+str(sar.denominator),'display':[float(dw),float(dh)],'_display':(dw,dh)}
checks={'full_decode':{'pass':decode=='true','values':{'completed_without_error':decode=='true'}},'one_fps_contact_sheets':{'pass':render=='true','values':{'sheet_count':int(count)}}}
try:
    clean=facts(load(cp));public={k:v for k,v in clean.items() if not k.startswith('_')};checks['cleaned_video_metadata']={'pass':True,'values':public}
except Exception as exc:
    clean=None;checks['cleaned_video_metadata']={'pass':False,'error':str(exc)}
if delivery:
    dw,dh=map(int,delivery.split('x'))
    ok=clean is not None and (clean['width'],clean['height'])==(dw,dh) and clean['_display']==(Fraction(dw),Fraction(dh))
    checks['delivery_geometry']={'pass':ok,'values':{'expected':[dw,dh],'cleaned':list(map(float,clean['_display'])) if clean else None}}
if sp:
    checks['source_identity']={'pass':identical!='true','values':{'byte_identical_to_cleaned':identical=='true'},**({'error':'source is byte-identical to cleaned, so every source comparison below is vacuous; supply the media as it existed BEFORE subtitle removal'} if identical=='true' else {})}
    try:
        original=facts(load(sp));
        if clean is None:raise ValueError('cleaned metadata invalid')
        geometry=clean['_display']==original['_display'];audio=not original['has_audio'] or clean['has_audio'];allowed=max(.20,2/original['fps']);drift=abs(clean['duration_seconds']-original['duration_seconds']);duration=drift<=allowed+1e-9
        if not delivery:
            checks['source_display_geometry']={'pass':geometry,'values':{'source':list(map(float,original['_display'])),'cleaned':list(map(float,clean['_display']))}}
        checks['source_audio_preserved']={'pass':audio,'values':{'source_has_audio':original['has_audio'],'cleaned_has_audio':clean['has_audio']}}
        checks['source_duration_drift']={'pass':duration,'values':{'drift_seconds':drift,'allowed_seconds':allowed}}
    except Exception as exc:checks['source_comparison']={'pass':False,'error':str(exc)}
passed=all(x.get('pass') is True for x in checks.values());json.dump({'version':1,'pass':passed,'checks':checks},open(out,'w'),indent=2,sort_keys=True);open(out,'a').write('\n');print('true' if passed else 'false')
PY
) || fail_report report "could not build verification report"
[[ -z "$source_probe" ]] || rm -f -- "$source_probe"
publish
if [[ $result != true ]]; then printf 'verify-video: verification failed; inspect %s/report.json and sheets\n' "$verify_dir" >&2;exit 1;fi
printf 'Verification passed. Report: %s/report.json\nHuman visual inspection required: %s/sheet-*.png\n' "$verify_dir" "$verify_dir"
