#!/usr/bin/env bash
set +x
set -euo pipefail

usage() {
  printf 'Usage: %s [--wait] OUTPUT_FILE\nHITPAW_LOG_FILE may select one log; relative paths resolve beneath HITPAW_LOG_DIR, and absolute paths must remain inside it.\n' "$0" >&2
}
die() { printf 'fetch-hitpaw-result: %s\n' "$*" >&2; exit 1; }

wait_mode=0
if [[ ${1:-} == --wait ]]; then wait_mode=1; shift; fi
[[ $# -eq 1 ]] || { usage; exit 2; }
for tool in python3 ffprobe; do command -v "$tool" >/dev/null 2>&1 || die "required tool not found: $tool"; done
output=$1
log_dir=${HITPAW_LOG_DIR:-"$HOME/Library/Caches/HitPaw Edimakor/HitpawEdimakor"}
case ${output##*.} in [mM][pP]4|[mM][oO][vV]) ;; *) die "output must have an .mp4 or .mov extension" ;; esac
[[ ! -L "$output" ]] || die "output must not be a symlink"
if python3 - "$output" "$log_dir" <<'PY'
import os, sys
out=os.path.realpath(os.path.abspath(sys.argv[1])); logs=os.path.realpath(os.path.abspath(sys.argv[2]))
raise SystemExit(0 if out==logs or out.startswith(logs+os.sep) else 1)
PY
then die "output must not be inside the HitPaw log directory"; fi
if [[ -e "$output" ]]; then
  [[ -f "$output" ]] || die "output exists and is not a regular file"
  [[ $(python3 -c 'import os,sys;print(os.stat(sys.argv[1],follow_symlinks=False).st_nlink)' "$output") -eq 1 ]] || die "output has multiple hard links"
  existing=$(ffprobe -v error -select_streams v:0 -show_entries stream=index -of csv=p=0 "$output" 2>/dev/null || true)
  [[ -n "$existing" ]] && { printf 'Existing output is already a valid video: %s\n' "$output"; exit 0; }
  die "output exists but is not a valid video; refusing to overwrite it"
fi
command -v curl >/dev/null 2>&1 || die "required tool not found: curl"
[[ -d "$log_dir" && ! -L "$log_dir" ]] || die "HitPaw log directory is unavailable or unsafe"
wait_seconds=${HITPAW_WAIT_SECONDS:-1800}; [[ $wait_seconds =~ ^[0-9]+$ ]] || die "HITPAW_WAIT_SECONDS must be a non-negative integer"
parent=$(dirname "$output"); mkdir -p "$parent" || die "could not create output directory"; [[ ! -L "$parent" ]] || die "output directory must not be a symlink"
parent=$(cd "$parent" && pwd -P); output="$parent/$(basename "$output")"
stage=$(mktemp -d "$parent/.$(basename "$output").download.XXXXXX"); chmod 700 "$stage"
partial="$stage/result.partial"; state="$stage/log-state.json"
cleanup() { [[ -z ${stage:-} ]] || rm -rf -- "$stage"; }; trap cleanup EXIT HUP INT TERM

scan_logs() {
  local mode=$1 budget=$2
  python3 - "$log_dir" "$mode" "$state" "$budget" "${HITPAW_LOG_FILE:-}" <<'PY'
import datetime as dt, hashlib, json, os, re, sys, time
root,mode,state_path,budget,selected=sys.argv[1:]; end=time.monotonic()+max(0.0,float(budget))
MARK=re.compile(rb"removeWatermark result url:\s*[\"']?(https://[^\s\"'<>]+)")
STAMP=re.compile(rb'(\d{4}-\d{2}-\d{2}[T ][0-2]\d:[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?)')
CHECK=4096; CHUNK=65536; OVERLAP=65536
def stamp(window,marker_start):
    line_start=window.rfind(b'\n',0,marker_start)+1
    before=window[line_start:marker_start]
    matches=list(STAMP.finditer(before))
    if not matches:return None
    m=matches[-1]
    try:
        value=m.group(1).decode().replace(' ','T').replace('Z','+00:00'); parsed=dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=dt.timezone.utc)
        return int(parsed.timestamp()*1_000_000_000)
    except (ValueError,OverflowError):return None
paths=[]
if selected:
    base=os.path.realpath(root)
    unresolved=selected if os.path.isabs(selected) else os.path.join(base,selected)
    candidate=os.path.realpath(os.path.abspath(unresolved))
    if not (candidate==base or candidate.startswith(base+os.sep)):
        print('SELECTED_UNSAFE')
        raise SystemExit(0)
    paths=[candidate]
else:
  for base,dirs,files in os.walk(root,followlinks=False):
    if time.monotonic()>=end:break
    dirs[:]=[d for d in dirs if not os.path.islink(os.path.join(base,d))]
    for name in files:paths.append(os.path.join(base,name))
checked=[]
for path in paths:
        if time.monotonic()>=end:break
        try:
            st=os.lstat(path)
            if os.path.isfile(path) and not os.path.islink(path):checked.append((path,st))
        except OSError:pass
paths=checked;old={}
if mode=='poll':
    try:
        with open(state_path,encoding='utf-8') as h:old=json.load(h).get('files',{})
    except (OSError,ValueError,TypeError):old={}
new={}; candidates=[]
for path,st in paths:
    if time.monotonic()>=end:break
    real=os.path.realpath(path);previous=old.get(real);read_start=0
    check_start=max(0,st.st_size-CHECK)
    try:
        with open(path,'rb') as h:h.seek(check_start);checkpoint=h.read(CHECK)
    except OSError:continue
    threshold=0
    if mode=='poll' and previous:
        appended=False
        if (previous.get('dev'),previous.get('ino'))==(st.st_dev,st.st_ino) and st.st_size>previous.get('size',-1):
            try:
                with open(path,'rb') as h:h.seek(previous['check_start']);prior=h.read(previous['size']-previous['check_start'])
                appended=hashlib.sha256(prior).hexdigest()==previous.get('checkpoint')
            except (OSError,KeyError,TypeError):pass
        if appended:read_start=max(0,int(previous.get('resume',previous['size'])));threshold=int(previous['size'])
        elif (st.st_size,st.st_mtime_ns,st.st_ctime_ns)==(previous.get('size'),previous.get('mtime'),previous.get('ctime')):
            new[real]=previous
            continue
    carry=b'';position=read_start;seen_offsets=set();last_newline=-1
    try:
      with open(path,'rb') as h:
        h.seek(read_start)
        while time.monotonic()<end:
            chunk=h.read(CHUNK)
            if not chunk:break
            nl=chunk.rfind(b'\n')
            if nl>=0:last_newline=position+nl
            window=carry+chunk;base_offset=position-len(carry)
            for match in MARK.finditer(window):
                offset=base_offset+match.start()
                if offset in seen_offsets or (mode=='poll' and match.end()+base_offset<=threshold):continue
                seen_offsets.add(offset)
                try:url=match.group(1).rstrip(b',.;)]}').decode('ascii')
                except UnicodeDecodeError:continue
                ident=hashlib.sha256(f'{real}\0{st.st_dev}\0{st.st_ino}\0{st.st_size}\0{st.st_mtime_ns}\0{st.st_ctime_ns}\0{offset}'.encode()).hexdigest()
                candidates.append({'timestamp':stamp(window,match.start()),'offset':offset,'identity':ident,'url':url,'path':real})
            carry=window[-OVERLAP:];position+=len(chunk)
    except OSError:continue
    resume=last_newline+1 if last_newline>=read_start else read_start
    if st.st_size==0 or (last_newline==st.st_size-1):resume=st.st_size
    new[real]={'dev':st.st_dev,'ino':st.st_ino,'size':st.st_size,'mtime':st.st_mtime_ns,'ctime':st.st_ctime_ns,'check_start':check_start,'checkpoint':hashlib.sha256(checkpoint).hexdigest(),'resume':resume}
with open(state_path+'.tmp','w',encoding='utf-8') as h:json.dump({'files':new},h,separators=(',',':'))
os.replace(state_path+'.tmp',state_path)
if mode!='init' and candidates:
    by_path={}
    for item in candidates:
        prior=by_path.get(item['path'])
        if prior is None or item['offset']>prior['offset']:by_path[item['path']]=item
    choices=list(by_path.values())
    if len(choices)>1:
        if any(item['timestamp'] is None for item in choices):print('AMBIGUOUS');raise SystemExit(0)
        top=max(item['timestamp'] for item in choices)
        choices=[item for item in choices if item['timestamp']==top]
        if len(choices)!=1:print('AMBIGUOUS');raise SystemExit(0)
    item=choices[0];print(item['identity'],item['url'],sep='\t')
PY
}

deadline=$(python3 -c 'import sys,time;print(time.monotonic()+float(sys.argv[1]))' "$wait_seconds")
remaining() { python3 -c 'import sys,time;print(max(0.0,float(sys.argv[1])-time.monotonic()))' "$deadline"; }
if [[ $wait_mode -eq 1 ]]; then
  snapshot=$(scan_logs init "$(remaining)") || die "could not snapshot HitPaw logs"
  [[ "$snapshot" != SELECTED_UNSAFE ]] || die "HITPAW_LOG_FILE must resolve to a log inside HITPAW_LOG_DIR"
  record=''
  while [[ -z "$record" ]]; do
    left=$(remaining); python3 -c 'import sys;raise SystemExit(0 if float(sys.argv[1])>0 else 1)' "$left" || die "timed out waiting for a newer completed HitPaw result"
    record=$(scan_logs poll "$left" || true); [[ -n "$record" ]] && break
    left=$(remaining); nap=$(python3 -c 'import sys;left=max(0.0,float(sys.argv[1]));print(min(5.0,max(0.0,left-min(.25,left/2))))' "$left")
    python3 -c 'import sys;raise SystemExit(0 if float(sys.argv[1])>0 else 1)' "$nap" || die "timed out waiting for a newer completed HitPaw result"; sleep "$nap"
  done
else
  record=$(scan_logs current "$(remaining)" || true); [[ -n "$record" ]] || die "no completed HTTPS HitPaw result was found"
fi
[[ "$record" != AMBIGUOUS ]] || die "multiple untimestamped HitPaw results are ambiguous; set HITPAW_LOG_FILE to one log"
[[ "$record" != SELECTED_UNSAFE ]] || die "HITPAW_LOG_FILE must resolve to a log inside HITPAW_LOG_DIR"
url=${record#*$'\t'}
urls=$(python3 - "$url" <<'PY'
from urllib.parse import urlsplit,urlunsplit
import sys
try:p=urlsplit(sys.argv[1]);port=p.port
except ValueError:raise SystemExit(1)
if p.scheme!='https' or not p.hostname or p.username is not None or p.password is not None or any(ord(c)<33 for c in sys.argv[1]):raise SystemExit(1)
host=p.hostname.lower();netloc=host if port is None else f'{host}:{port}';origin=urlunsplit(('https',netloc,p.path,p.query,p.fragment));accelerated=origin
if host=='edimakorpc-us-prod.oss-us-east-1.aliyuncs.com':
    host='edimakorpc-us-prod.oss-accelerate.aliyuncs.com';accelerated=urlunsplit(('https',host if port is None else f'{host}:{port}',p.path,p.query,p.fragment))
print(origin,accelerated,sep='\t')
PY
) || die "HitPaw result URL is invalid"
origin=${urls%%$'\t'*};accelerated=${urls#*$'\t'}

download() { local candidate=$1 retries=$2; curl --fail --location --silent --proto '=https' --proto-redir '=https' --retry "$retries" --retry-all-errors --retry-delay 2 --connect-timeout 20 --max-time 1800 --output "$partial" -- "$candidate"; }
valid_partial() { chmod 600 "$partial" 2>/dev/null || true; [[ -f "$partial" && ! -L "$partial" ]] || return 1; [[ $(python3 -c 'import os,sys;print(os.stat(sys.argv[1],follow_symlinks=False).st_nlink)' "$partial") -eq 1 ]] || return 1; [[ -n $(ffprobe -v error -select_streams v:0 -show_entries stream=index -of csv=p=0 "$partial" 2>/dev/null || true) ]]; }
ok=0
if [[ "$accelerated" != "$origin" ]]; then if download "$accelerated" 8; then valid_partial && ok=1; elif valid_partial; then retained=$partial;stage='';die "transport failed; valid partial retained at: $retained";fi;fi
if [[ $ok -eq 0 ]]; then if download "$origin" 3;then valid_partial && ok=1;elif valid_partial;then retained=$partial;stage='';die "transport failed; valid partial retained at: $retained";fi;fi
[[ $ok -eq 1 ]] || die "download failed or returned invalid media; task was not resubmitted"
ln "$partial" "$output" 2>/dev/null || { retained=$partial;stage='';die "output appeared concurrently; valid download retained at: $retained"; }
rm -f -- "$partial";rm -rf -- "$stage";stage='';trap - EXIT HUP INT TERM
printf 'Downloaded and verified HitPaw result: %s\n' "$output"
