#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<EOF
Usage: $0 MANIFEST_JSON OUTPUT_ZIP

Manifest schema (all deliverables must be beneath the manifest's directory):
  {"version":1,"entries":[{"source":"/abs/source.mp4","status":"complete","deliverables":{"cleaned":"/abs/job/cleaned.mp4","fcpxml":"/abs/job/project.fcpxml","cuts":"/abs/job/cut-plan.json","verification":"/abs/job/verify/report.json"}}]}

Only complete entries are packaged; other entries remain resumable.
Manifest, JSON, and FCPXML text files are limited by PACKAGE_TEXT_MAX_BYTES (default: 16777216).
EOF
}

die() {
  printf 'package-deliverables: %s\n' "$*" >&2
  exit 1
}

[[ $# -eq 2 ]] || { usage; exit 2; }
for tool in python3 ffprobe zip unzip; do
  command -v "$tool" >/dev/null 2>&1 || die "required tool not found: $tool"
done
script_dir=$(cd "$(dirname "$0")" && pwd -P)
builder="$script_dir/build-fcpxml.py"
[[ -f "$builder" && ! -L "$builder" ]] || die "build-fcpxml.py must be a regular, non-symlink sibling"

manifest=$1
output=$2
[[ -f "$manifest" && ! -L "$manifest" ]] || die "manifest must be a regular, non-symlink JSON file"
case ${output##*.} in
  [zZ][iI][pP]) ;;
  *) die "output must have a .zip extension" ;;
esac
[[ ! -e "$output" && ! -L "$output" ]] || die "output already exists; refusing to overwrite it"

output_parent=$(dirname "$output")
mkdir -p "$output_parent" || die "could not create output directory"
[[ ! -L "$output_parent" ]] || die "output directory must not be a symlink"
output_parent=$(cd "$output_parent" && pwd -P)
output="$output_parent/$(basename "$output")"
stage=$(mktemp -d "$output_parent/.$(basename "$output").stage.XXXXXX")
chmod 700 "$stage"
cleanup() { rm -rf -- "$stage"; }
trap cleanup EXIT HUP INT TERM
tree="$stage/archive"
mkdir "$tree"

if ! python3 - "$manifest" "$tree" "$builder" <<'PY'
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
from urllib.parse import parse_qsl, urlsplit

manifest_path = Path(sys.argv[1]).absolute()
stage = Path(sys.argv[2])
builder_path = Path(sys.argv[3])
root = manifest_path.parent.resolve()

def fail(message):
    raise SystemExit(message)

spec = importlib.util.spec_from_file_location("package_fcpxml_validator", builder_path)
if spec is None or spec.loader is None:
    fail("could not load build-fcpxml.py validation rules")
fcpxml_validator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fcpxml_validator
try:
    spec.loader.exec_module(fcpxml_validator)
except Exception as exc:
    fail(f"could not load build-fcpxml.py validation rules: {exc}")

try:
    max_text_bytes = int(os.environ.get("PACKAGE_TEXT_MAX_BYTES", "16777216"))
except ValueError:
    fail("PACKAGE_TEXT_MAX_BYTES must be a positive integer")
if max_text_bytes <= 0:
    fail("PACKAGE_TEXT_MAX_BYTES must be a positive integer")
try:
    if manifest_path.stat().st_size > max_text_bytes:
        fail("manifest exceeds PACKAGE_TEXT_MAX_BYTES")
except OSError as exc:
    fail(f"manifest is unavailable: {exc}")

try:
    with manifest_path.open("rb") as handle:
        manifest_bytes = handle.read(max_text_bytes + 1)
    if len(manifest_bytes) > max_text_bytes:
        fail("manifest exceeds PACKAGE_TEXT_MAX_BYTES")
    data = json.loads(
        manifest_bytes.decode("utf-8"),
        parse_constant=lambda value: fail(f"manifest contains non-finite JSON number: {value}"),
    )
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    fail(f"invalid manifest JSON: {exc}")

if not isinstance(data, dict) or set(data) != {"version", "entries"}:
    fail("manifest must contain exactly version and entries")
if data["version"] != 1 or isinstance(data["version"], bool):
    fail("manifest version must be 1")
if not isinstance(data["entries"], list):
    fail("manifest entries must be an array")

required = {"cleaned", "fcpxml", "cuts", "verification"}
allowed_ext = {
    "cleaned": {".mp4", ".mov"},
    "fcpxml": {".fcpxml"},
    "cuts": {".json"},
    "verification": {".json"},
}
binary_sensitive = re.compile(
    rb"(?:[?&](?:token|signature|expires|x-amz-[^=&#\s]*|credential|key|auth|sig)=|bearer\s+|ossaccesskeyid|x-amz-signature)",
    re.I,
)
email_address = re.compile(
    rb"(?<![A-Z0-9.!#$%&'*+/=?^_`{|}~-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+",
    re.I,
)
email_text = re.compile(r"(?<![A-Z0-9.!#$%&'*+/=?^_`{|}~-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+", re.I)
sensitive_keys = re.compile(r"email|(?:^|[_-])(?:account|user|credential|token|auth|key|secret|password|authorization|signature|expires)(?:$|[_-])|^(?:x-amz-.*|ossaccesskeyid|access[_-]?token|api[_-]?key|secret[_-]?key|signed[_-]?url|user(?:name|id)|account(?:name|id)|task[_-]?(?:id|uuid)|sig)$", re.I)
query_secret = re.compile(r"^(?:token|signature|expires|x-amz-.*|credential|key|auth|sig|ossaccesskeyid)$", re.I)

def bad_string(value, key=""):
    if email_text.search(value) or (sensitive_keys.search(key) and value):
        return True
    try:
        parsed = urlsplit(value)
        if any(query_secret.match(name) for name, _ in parse_qsl(parsed.query, keep_blank_values=True)):
            return True
    except ValueError:
        return True
    return bool(re.search(r"(?:bearer\s+|ossaccesskeyid|x-amz-signature|(?:token|password|signature|credential|api[_-]?key|secret[_-]?key)\s*[:=]\s*\S+)", value, re.I))

def benign_sensitive_subtree(value):
    if value is None or value is False or value == "":
        return True
    if isinstance(value, dict):
        return all(benign_sensitive_subtree(child) for child in value.values())
    if isinstance(value, list):
        return all(benign_sensitive_subtree(child) for child in value)
    return False

def scan_json(value, key="", sensitive_parent=False):
    key_is_sensitive = bool(sensitive_keys.search(key))
    under_sensitive = sensitive_parent or key_is_sensitive
    if under_sensitive and not benign_sensitive_subtree(value):
        fail("decoded text contains account or credential data")
    if isinstance(value, str):
        if bad_string(value): fail("decoded text contains account or credential data")
    elif isinstance(value, dict):
        for child_key, child in value.items():
            key_text = str(child_key)
            if bad_string(key_text): fail("decoded text contains account or credential data")
            scan_json(child, key_text, under_sensitive)
    elif isinstance(value, list):
        for child in value: scan_json(child, key, under_sensitive)

file_uri = re.compile(r"(?i)(?<![A-Za-z0-9+.-])file:(?://)?[^\x00\s<>'\"]+")
windows_path = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/]|\\\\[^\\/\s]+[\\/])"
)
unix_path = re.compile(
    r"(?<![A-Za-z0-9_+.\-/])/(?!/)(?:[^\x00\s/]+(?:/[^\x00\s/]*)*)?"
)

def contains_absolute_local_reference(value):
    if not isinstance(value, str):
        return False
    return bool(
        file_uri.search(value)
        or windows_path.search(value)
        or unix_path.search(value)
    )

def reject_local_references(value):
    if isinstance(value, str):
        if contains_absolute_local_reference(value):
            fail("archive text contains an absolute local path")
    elif isinstance(value, dict):
        for child_key, child in value.items():
            if contains_absolute_local_reference(str(child_key)):
                fail("archive text contains an absolute local path")
            reject_local_references(child)
    elif isinstance(value, list):
        for child in value:
            reject_local_references(child)

def validate_cut_plan_fields(plan):
    required = {
        "version", "source", "fps", "frame_count", "duration", "candidates",
        "requires_visual_review", "selected_frames", "rejected_candidates",
    }
    if not isinstance(plan, dict) or not required.issubset(plan):
        fail("cut plan has missing required fields")
    if set(plan) not in (required, required | {"review_decision"}):
        fail("cut plan has unknown fields")
    candidates = plan.get("candidates")
    rejected = plan.get("rejected_candidates")
    if not isinstance(candidates, list) or not all(
        isinstance(item, dict)
        and set(item) == {"frame", "time_seconds", "confidence", "evidence"}
        for item in candidates
    ):
        fail("cut plan candidates must contain exactly the public audit fields")
    if not isinstance(rejected, list) or not all(
        isinstance(item, dict) and set(item) == {"frame", "reason"}
        for item in rejected
    ):
        fail("cut plan rejected candidates must contain exactly frame and reason")

def sanitized_cut_plan(plan, media_name):
    top_fields = (
        "version", "fps", "frame_count", "duration", "candidates",
        "requires_visual_review", "selected_frames", "rejected_candidates",
    )
    candidate_fields = ("frame", "time_seconds", "confidence", "evidence")
    rejected_fields = ("frame", "reason")
    sanitized = {key: plan[key] for key in top_fields if key in plan}
    sanitized["source"] = media_name
    sanitized["candidates"] = [
        {key: item[key] for key in candidate_fields if key in item}
        for item in plan["candidates"]
    ]
    sanitized["rejected_candidates"] = [
        {key: item[key] for key in rejected_fields if key in item}
        for item in plan["rejected_candidates"]
    ]
    if plan.get("review_decision") == "no-cuts":
        sanitized["review_decision"] = "no-cuts"
    return sanitized

def write_json(path, value):
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")

complete = []
source_identities = set()
for index, entry in enumerate(data["entries"]):
    if not isinstance(entry, dict):
        fail(f"entry {index + 1} must be an object")
    if not {"source", "status"}.issubset(entry) or not set(entry).issubset({"source", "status", "deliverables"}):
        fail(f"entry {index + 1} has missing or unknown fields")
    if not isinstance(entry["source"], str) or not os.path.isabs(entry["source"]):
        fail(f"entry {index + 1} source must be an absolute path string")
    if not isinstance(entry["status"], str):
        fail(f"entry {index + 1} status must be a string")
    source_path = Path(entry["source"])
    source_real = source_path.resolve(strict=False)
    path_source=("path",str(source_real))
    if path_source in source_identities: fail(f"entry {index + 1} duplicates a source")
    source_identities.add(path_source)
    try:
        source_stat = source_path.stat()
        if stat.S_ISREG(source_stat.st_mode):
            inode_source=("inode",source_stat.st_dev,source_stat.st_ino)
            if inode_source in source_identities: fail(f"entry {index + 1} duplicates a source")
            source_identities.add(inode_source)
    except OSError:
        pass
    if entry["status"] != "complete":
        continue
    deliverables = entry.get("deliverables")
    if not isinstance(deliverables, dict) or set(deliverables) != required:
        fail(f"complete entry {index + 1} must define exactly cleaned, fcpxml, cuts, and verification")
    complete.append((index, entry))
if not complete:
    fail("manifest must contain at least one complete entry")

archive_names = set()
deliverable_identities = set()
sanitized_entries = []
checksum_rows = []
for ordinal, (manifest_index, entry) in enumerate(complete, 1):
    archive_deliverables = {}
    cleaned_source = None
    cleaned_probe = None
    approved_cuts = None
    entry_dir = stage / f"entry-{ordinal:03d}"
    entry_dir.mkdir()
    names = {
        "cleaned": "cleaned" + Path(entry["deliverables"]["cleaned"]).suffix.lower(),
        "fcpxml": "project.fcpxml",
        "cuts": "cut-plan.json",
        "verification": "verification.json",
    }
    for kind in ("cleaned", "cuts", "fcpxml", "verification"):
        raw = entry["deliverables"][kind]
        if not isinstance(raw, str) or not os.path.isabs(raw):
            fail(f"complete entry {manifest_index + 1} {kind} must be an absolute path string")
        path = Path(raw)
        if path.suffix.lower() not in allowed_ext[kind]:
            fail(f"complete entry {manifest_index + 1} {kind} has an unsupported extension")
        try:
            lst = path.lstat()
        except OSError as exc:
            fail(f"complete entry {manifest_index + 1} {kind} is unavailable: {exc}")
        if stat.S_ISLNK(lst.st_mode):
            fail(f"complete entry {manifest_index + 1} {kind} must not be a symlink")
        if not stat.S_ISREG(lst.st_mode):
            fail(f"complete entry {manifest_index + 1} {kind} must be a regular file")
        if lst.st_nlink != 1:
            fail(f"complete entry {manifest_index + 1} {kind} must have exactly one hard link")
        if kind != "cleaned" and lst.st_size > max_text_bytes:
            fail(f"complete entry {manifest_index + 1} {kind} exceeds PACKAGE_TEXT_MAX_BYTES")
        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError:
            fail(f"complete entry {manifest_index + 1} {kind} escapes the manifest directory")
        if (("path", str(resolved)) in source_identities or
                ("inode", lst.st_dev, lst.st_ino) in source_identities):
            fail(f"complete entry {manifest_index + 1} {kind} aliases source media")
        path_identity = ("path", str(resolved))
        inode_identity = ("inode", lst.st_dev, lst.st_ino)
        if path_identity in deliverable_identities or inode_identity in deliverable_identities:
            fail(f"complete entry {manifest_index + 1} {kind} duplicates another deliverable")
        deliverable_identities.update((path_identity, inode_identity))
        if resolved.suffix.lower() == ".log":
            fail(f"complete entry {manifest_index + 1} includes a log")

        archive_name = f"entry-{ordinal:03d}/{names[kind]}"
        if archive_name in archive_names:
            fail(f"duplicate archive name: {archive_name}")
        archive_names.add(archive_name)
        destination = stage / archive_name

        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(resolved, flags)
        try:
            opened = os.fstat(descriptor)
            before=(lst.st_dev,lst.st_ino,lst.st_size,lst.st_mtime_ns,lst.st_ctime_ns)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev,opened.st_ino,opened.st_size,opened.st_mtime_ns,opened.st_ctime_ns) != before:
                fail(f"complete entry {manifest_index + 1} {kind} changed during packaging")
            digest = hashlib.sha256()
            media_overlap = b""
            with os.fdopen(descriptor, "rb", closefd=False) as source, destination.open("xb") as target:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    target.write(chunk)
                    if kind == "cleaned":
                        window=media_overlap+chunk
                        if email_address.search(window) or binary_sensitive.search(window):
                            destination.unlink(missing_ok=True)
                            fail(f"complete entry {manifest_index + 1} cleaned contains account or credential data")
                        media_overlap=window[-4096:]
            after=path.stat()
            if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns) != before:
                destination.unlink(missing_ok=True)
                fail(f"complete entry {manifest_index + 1} {kind} changed during packaging")
        finally:
            os.close(descriptor)
        if kind == "cleaned":
            checked=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=index","-of","csv=p=0",str(destination)],capture_output=True,text=True)
            if checked.returncode or not checked.stdout.strip(): fail(f"complete entry {manifest_index + 1} cleaned is not valid media")
            try:
                cleaned_probe=fcpxml_validator.probe_media(destination)
            except Exception as exc:
                fail(f"complete entry {manifest_index + 1} cleaned fails strict FCPXML media validation: {exc}")
            cleaned_source=resolved
        elif kind in {"cuts","verification"}:
            try:
                with destination.open(encoding="utf-8") as handle:
                    decoded=json.load(
                        handle,
                        parse_constant=lambda value: fail(
                            f"complete entry {manifest_index + 1} {kind} contains non-finite JSON number: {value}"
                        ),
                    )
            except (OSError,UnicodeError,json.JSONDecodeError): fail(f"complete entry {manifest_index + 1} {kind} is not valid JSON")
            scan_json(decoded)
            if not isinstance(decoded,dict): fail(f"complete entry {manifest_index + 1} {kind} must be a JSON object")
            if kind == "cuts":
                validate_cut_plan_fields(decoded)
                if cleaned_source is None or cleaned_probe is None:
                    fail("internal error: cleaned media must be validated before cut plan")
                plan_source=decoded.get("source")
                if (
                    isinstance(plan_source,str)
                    and contains_absolute_local_reference(plan_source)
                    and Path(plan_source).resolve(strict=False) != cleaned_source
                ):
                    fail("archive text contains an absolute local path")
                try:
                    approved_cuts=fcpxml_validator.validate_plan(
                        decoded, cleaned_source, cleaned_probe.media
                    )
                except Exception as exc:
                    fail(f"complete entry {manifest_index + 1} cuts fails strict timeline validation: {exc}")
                decoded=sanitized_cut_plan(decoded,names["cleaned"])
                scan_json(decoded)
                reject_local_references(decoded)
                write_json(destination,decoded)
            else:
                checks=decoded.get("checks")
                if decoded.get("version") != 1 or isinstance(decoded.get("version"),bool) or decoded.get("pass") is not True:
                    fail(f"complete entry {manifest_index + 1} verification report has an invalid summary")
                if not isinstance(checks,dict) or not checks:
                    fail(f"complete entry {manifest_index + 1} verification report must contain checks")
                if any(not isinstance(name,str) or not name or not isinstance(check,dict) or check.get("pass") is not True for name,check in checks.items()):
                    fail(f"complete entry {manifest_index + 1} verification report contains an invalid or failed check")
                reject_local_references(decoded)
        else:
            xml_bytes=destination.read_bytes()
            try:
                original_xml=xml_bytes.decode("utf-8")
            except UnicodeDecodeError:
                fail(f"complete entry {manifest_index + 1} fcpxml is not valid UTF-8")
            lexical=re.sub(rb"<!--.*?-->|<!\[CDATA\[.*?\]\]>",b"",xml_bytes,flags=re.S)
            for instruction in re.findall(rb"<\?(.*?)\?>",lexical,re.S):
                text=instruction.decode("utf-8","replace")
                if not re.match(rb"xml(?:\s|$)",instruction) or bad_string(text):
                    fail(f"complete entry {manifest_index + 1} fcpxml contains a forbidden processing instruction")
            doctypes=re.findall(rb"<!DOCTYPE\b.*?>",lexical,re.I|re.S)
            for declaration in doctypes:
                text=declaration.decode("utf-8","replace")
                if bad_string(text) or not re.fullmatch(rb"<!DOCTYPE\s+fcpxml\s*>",declaration,re.I|re.S):
                    fail(f"complete entry {manifest_index + 1} fcpxml contains a forbidden DTD declaration")
            if re.search(rb"<!DOCTYPE\b[^>]*\[|<!DOCTYPE\b[^>]*(?:SYSTEM|PUBLIC)",lexical,re.I|re.S):
                fail(f"complete entry {manifest_index + 1} fcpxml contains an internal or external DTD declaration")
            try: parsed=ET.parse(destination,parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
            except (OSError,ET.ParseError): fail(f"complete entry {manifest_index + 1} fcpxml is not valid XML")
            if parsed.getroot().tag != "fcpxml": fail(f"complete entry {manifest_index + 1} fcpxml has the wrong root")
            if cleaned_source is None or cleaned_probe is None or approved_cuts is None:
                fail("internal error: media and cut plan must be validated before FCPXML")
            try:
                fcpxml_validator._verify_fcpxml(
                    original_xml,
                    cleaned_source,
                    cleaned_probe.media,
                    approved_cuts,
                    cleaned_probe,
                )
            except Exception as exc:
                fail(f"complete entry {manifest_index + 1} fcpxml fails strict timeline validation: {exc}")
            media_reps=parsed.getroot().findall("./resources/asset/media-rep")
            if len(media_reps) != 1 or len(parsed.getroot().findall(".//media-rep")) != 1:
                fail(f"complete entry {manifest_index + 1} fcpxml must contain exactly one asset media reference")
            media_reps[0].set("src",names["cleaned"])
            if "suggestedFilename" in media_reps[0].attrib:
                media_reps[0].set("suggestedFilename",names["cleaned"])
            asset=parsed.getroot().find("./resources/asset")
            if asset is not None and "name" in asset.attrib:
                asset.set("name",names["cleaned"])
            all_clips=parsed.getroot().findall(".//asset-clip")
            timeline_clips=parsed.getroot().findall(
                "./library/event/project/sequence/spine/asset-clip"
            )
            if asset is None or any(
                clip.attrib.get("ref") != asset.attrib.get("id")
                for clip in all_clips
            ):
                fail(f"complete entry {manifest_index + 1} every asset-clip must reference the single asset")
            if len(all_clips) != len(timeline_clips):
                fail(f"complete entry {manifest_index + 1} fcpxml contains unexpected nested asset clips")
            for element in parsed.iter():
                if element.tag is ET.Comment:
                    if element.text and bad_string(element.text,"comment"): fail("decoded XML contains account or credential data")
                    if element.text and contains_absolute_local_reference(element.text): fail("decoded XML contains an absolute local path")
                    if element.tail and bad_string(element.tail): fail("decoded XML contains account or credential data")
                    if element.tail and contains_absolute_local_reference(element.tail): fail("decoded XML contains an absolute local path")
                    continue
                local=str(element.tag).rsplit('}',1)[-1]
                if element.text and bad_string(element.text,local): fail("decoded XML contains account or credential data")
                if element.text and contains_absolute_local_reference(element.text): fail("decoded XML contains an absolute local path")
                if element.tail and bad_string(element.tail): fail("decoded XML contains account or credential data")
                if element.tail and contains_absolute_local_reference(element.tail): fail("decoded XML contains an absolute local path")
                for name,value in element.attrib.items():
                    if bad_string(value,str(name).rsplit('}',1)[-1]): fail("decoded XML contains account or credential data")
                    if contains_absolute_local_reference(value): fail("decoded XML contains an absolute local path")
            body=ET.tostring(parsed.getroot(),encoding="utf-8",short_empty_elements=True)
            destination.write_bytes(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'+body+b'\n')
            validation_root=ET.fromstring(body)
            validation_rep=validation_root.find("./resources/asset/media-rep")
            validation_rep.set("src",destination.parent.joinpath(names["cleaned"]).resolve().as_uri())
            validation_body=ET.tostring(validation_root,encoding="unicode",short_empty_elements=True)
            validation_xml='<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'+validation_body+'\n'
            try:
                fcpxml_validator._verify_fcpxml(
                    validation_xml,
                    destination.parent / names["cleaned"],
                    cleaned_probe.media,
                    approved_cuts,
                    cleaned_probe,
                )
            except Exception as exc:
                fail(f"complete entry {manifest_index + 1} archived fcpxml failed validation: {exc}")
        os.utime(destination,(315532800,315532800))
        final_digest = digest.hexdigest() if kind == "cleaned" else hashlib.sha256(destination.read_bytes()).hexdigest()
        checksum_rows.append((archive_name, final_digest))
        archive_deliverables[kind] = archive_name
    sanitized_entries.append({"id": f"entry-{ordinal:03d}", "status": "complete", "deliverables": archive_deliverables})

sanitized = {"version": 1, "entries": sanitized_entries}
with (stage / "manifest.json").open("x", encoding="utf-8") as handle:
    json.dump(sanitized, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.utime(stage / "manifest.json",(315532800,315532800))
manifest_digest = hashlib.sha256((stage / "manifest.json").read_bytes()).hexdigest()
checksum_rows.append(("manifest.json", manifest_digest))
with (stage / "SHA256SUMS").open("x", encoding="ascii") as handle:
    for archive_name, digest in sorted(checksum_rows):
        handle.write(f"{digest}  {archive_name}\n")
os.utime(stage / "SHA256SUMS",(315532800,315532800))
PY
then
  die "manifest or deliverable validation failed"
fi

partial="$stage/deliverables.zip"
(
  cd "$tree"
  find . -type f -print | LC_ALL=C sort | TZ=UTC zip -q -X "$partial" -@
) || die "could not create ZIP archive"
unzip -tq "$partial" >/dev/null || die "ZIP integrity test failed"

if ! ln "$partial" "$output" 2>/dev/null; then
  die "output appeared concurrently; refusing to overwrite it"
fi
printf 'Package created and verified: %s\n' "$output"
