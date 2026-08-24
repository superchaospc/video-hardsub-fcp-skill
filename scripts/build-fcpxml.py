#!/usr/bin/env python3
"""Build a frame-exact, contiguous Final Cut Pro XML timeline."""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence
from urllib.parse import unquote, urlparse


FCPXML_VERSION = "1.10"
PLAN_VERSION = 1
REJECTION_REASONS = frozenset(
    {
        "persistent-motion",
        "steam",
        "ingredient-motion",
        "camera-shake",
        "no-visible-discontinuity",
    }
)
EVIDENCE_KINDS = frozenset({"keyframe", "scene", "ydif"})
MAX_RATIONAL_COMPONENT = 10**12
MAX_RATIONAL_TEXT_LENGTH = 128
MAX_RATIONAL_DIGITS = 64
FLOAT_DENOMINATOR_LIMIT = 1_000_000
SEQUENCE_AUDIO_RATES = {
    32000: "32k",
    44100: "44.1k",
    48000: "48k",
    88200: "88.2k",
    96000: "96k",
    176400: "176.4k",
    192000: "192k",
}


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    fps: Fraction
    frame_count: int
    duration: Fraction
    has_audio: bool


@dataclass(frozen=True)
class _ProbeDetails:
    media: MediaInfo
    audio_channels: int
    audio_sample_rate: int
    audio_sources: int


def frames_to_time(frame: int, fps: Fraction) -> str:
    """Return a reduced FCPXML rational time."""
    if not isinstance(frame, int) or isinstance(frame, bool) or frame < 0:
        raise ValueError("frame must be a non-negative integer")
    if not isinstance(fps, Fraction):
        try:
            fps = Fraction(fps)
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            raise ValueError("fps must be a positive rational number") from exc
    if fps <= 0:
        raise ValueError("fps must be positive")
    value = Fraction(frame, 1) / fps
    if value.denominator == 1:
        return f"{value.numerator}s"
    return f"{value.numerator}/{value.denominator}s"


def validate_cuts(cuts: Sequence[int], frame_count: int) -> list[int]:
    """Require strictly increasing, unique cut frames inside the media bounds."""
    if not isinstance(frame_count, int) or isinstance(frame_count, bool) or frame_count <= 0:
        raise ValueError("frame_count must be a positive integer")
    try:
        result = list(cuts)
    except TypeError as exc:
        raise ValueError("cuts must be a sequence of frame integers") from exc
    if any(not isinstance(frame, int) or isinstance(frame, bool) for frame in result):
        raise ValueError("every cut must be an integer frame number")
    if any(frame <= 0 or frame >= frame_count for frame in result):
        raise ValueError("cuts must be interior frames greater than zero and below frame_count")
    if any(left >= right for left, right in zip(result, result[1:])):
        raise ValueError("cuts must be strictly increasing and unique")
    return result


def _validate_media_info(info: MediaInfo) -> None:
    if info.width <= 0 or info.height <= 0:
        raise ValueError("media dimensions must be positive")
    if info.fps <= 0 or info.frame_count <= 0 or info.duration <= 0:
        raise ValueError("media fps, frame count, and duration must be positive")
    expected_duration = Fraction(info.frame_count, 1) / info.fps
    if info.duration != expected_duration:
        raise ValueError("media duration must exactly equal frame_count / fps")


def _audio_details(
    info: MediaInfo, probe: _ProbeDetails | None
) -> tuple[int, int, int] | None:
    if not info.has_audio:
        return None
    if probe is None:
        return None
    if probe.media != info:
        raise ValueError("probe details do not belong to this media")
    if (
        probe.audio_channels <= 0
        or probe.audio_sample_rate <= 0
        or probe.audio_sources <= 0
    ):
        raise ValueError("audio media requires channels, sample rate, and source count")
    return (probe.audio_channels, probe.audio_sample_rate, probe.audio_sources)


def build_fcpxml(source: Path, info: MediaInfo, cuts: Sequence[int], event_name: str) -> str:
    """Create one asset and contiguous asset clips covering every source frame once."""
    return _build_fcpxml(source, info, cuts, event_name, None)


def _build_fcpxml(
    source: Path,
    info: MediaInfo,
    cuts: Sequence[int],
    event_name: str,
    probe: _ProbeDetails | None,
) -> str:
    _validate_media_info(info)
    audio = _audio_details(info, probe)
    approved = validate_cuts(cuts, info.frame_count)
    source = Path(source).resolve()
    if not isinstance(event_name, str) or not event_name.strip():
        raise ValueError("event_name must be non-empty")

    root = ET.Element("fcpxml", {"version": FCPXML_VERSION})
    resources = ET.SubElement(root, "resources")
    ET.SubElement(
        resources,
        "format",
        {
            "id": "r1",
            "name": f"FFVideoFormat{info.width}x{info.height}",
            "frameDuration": frames_to_time(1, info.fps),
            "width": str(info.width),
            "height": str(info.height),
        },
    )
    duration = frames_to_time(info.frame_count, info.fps)
    asset_attributes = {
        "id": "r2",
        "name": source.name,
        "start": "0s",
        "duration": duration,
        "hasVideo": "1",
        "format": "r1",
        "videoSources": "1",
    }
    if info.has_audio:
        asset_attributes["hasAudio"] = "1"
        if audio is not None:
            audio_channels, audio_sample_rate, audio_sources = audio
            asset_attributes.update(
                {
                    "audioSources": str(audio_sources),
                    "audioChannels": str(audio_channels),
                    "audioRate": str(audio_sample_rate),
                }
            )
    asset = ET.SubElement(resources, "asset", asset_attributes)
    ET.SubElement(
        asset,
        "media-rep",
        {"kind": "original-media", "src": source.as_uri()},
    )

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": event_name})
    project = ET.SubElement(event, "project", {"name": event_name})
    sequence_attributes = {
        "format": "r1",
        "duration": duration,
        "tcStart": "0s",
        "tcFormat": "NDF",
    }
    if audio is not None and audio[1] in SEQUENCE_AUDIO_RATES:
        sequence_attributes["audioRate"] = SEQUENCE_AUDIO_RATES[audio[1]]
    sequence = ET.SubElement(project, "sequence", sequence_attributes)
    spine = ET.SubElement(sequence, "spine")
    boundaries = [0, *approved, info.frame_count]
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), 1):
        start_time = frames_to_time(start, info.fps)
        ET.SubElement(
            spine,
            "asset-clip",
            {
                "name": f"{source.stem} {index}",
                "ref": "r2",
                "offset": start_time,
                "start": start_time,
                "duration": frames_to_time(end - start, info.fps),
            },
        )

    body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n{body}\n'


def _bounded_fraction(
    value: object, label: str, *, positive: bool = False, nonnegative: bool = False
) -> Fraction:
    if value is None or isinstance(value, bool) or not isinstance(
        value, (str, int, float, Fraction)
    ):
        raise ValueError(f"invalid {label}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"invalid {label}")
    if isinstance(value, str):
        text = value.strip()
        if (
            len(text) > MAX_RATIONAL_TEXT_LENGTH
            or sum(character.isdigit() for character in text) > MAX_RATIONAL_DIGITS
        ):
            raise ValueError(f"{label} rational text is too large")
    try:
        if isinstance(value, float):
            parsed = Fraction(value).limit_denominator(FLOAT_DENOMINATOR_LIMIT)
        elif isinstance(value, str):
            parsed = Fraction(text)
        else:
            parsed = Fraction(value)
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if (
        abs(parsed.numerator) > MAX_RATIONAL_COMPONENT
        or parsed.denominator > MAX_RATIONAL_COMPONENT
    ):
        raise ValueError(f"{label} rational components are too large")
    if positive and parsed <= 0:
        raise ValueError(f"invalid {label}")
    if nonnegative and parsed < 0:
        raise ValueError(f"invalid {label}")
    return parsed


def _positive_fraction(value: object, label: str) -> Fraction:
    return _bounded_fraction(value, f"ffprobe {label}", positive=True)


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"ffprobe reported invalid {label}")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ffprobe reported invalid {label}") from exc
    if parsed <= 0:
        raise ValueError(f"ffprobe reported invalid {label}")
    return parsed


def _optional_positive_int(value: object, label: str) -> int | None:
    if value in (None, "N/A"):
        return None
    return _positive_int(value, label)


def validate_cfr_timestamps(
    lines: Iterable[str],
    fps: Fraction,
    expected_frames: int | None,
    time_base: Fraction,
) -> int:
    """Count every video frame and require integer PTS on the exact CFR grid."""
    fps = _positive_fraction(fps, "CFR frame rate")
    time_base = _positive_fraction(time_base, "CFR time base")
    if expected_frames is not None and (
        not isinstance(expected_frames, int)
        or isinstance(expected_frames, bool)
        or expected_frames <= 0
    ):
        raise ValueError("invalid expected frame count")
    half_tick = Fraction(1, 2)
    epsilon = Fraction(1, 1_000_000_000)
    observed = 0
    for line in lines:
        field = str(line).strip().split(",", 1)[0]
        if not field:
            continue
        try:
            pts = int(field)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("ffprobe returned a malformed video frame timestamp") from exc
        ideal_ticks = Fraction(observed, 1) / (fps * time_base)
        if abs(Fraction(pts) - ideal_ticks) > half_tick + epsilon:
            raise ValueError(
                "detected variable-frame-rate timestamps; normalize to CFR before FCPXML"
            )
        observed += 1
    if observed <= 0:
        raise ValueError("ffprobe returned no video frame timestamps")
    if expected_frames is not None and observed != expected_frames:
        raise ValueError(
            f"counted video frame count {observed} does not match metadata {expected_frames}"
        )
    return observed


def parse_ffprobe_json(
    output: str, frame_timestamps: Iterable[str] | None = None
) -> _ProbeDetails:
    """Parse ffprobe JSON and require exact, usable CFR metadata."""
    try:
        payload = json.loads(output)
        if not isinstance(payload, dict):
            raise TypeError
        streams = payload["streams"]
        if not isinstance(streams, list):
            raise TypeError
        if any(not isinstance(item, dict) for item in streams):
            raise TypeError
        format_info = payload.get("format", {})
        if not isinstance(format_info, dict):
            raise TypeError
        video_streams = [item for item in streams if item.get("codec_type") == "video"]
        audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
        if len(video_streams) != 1:
            raise ValueError("ffprobe must report exactly one video stream")
        video = video_streams[0]
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("ffprobe returned malformed media metadata") from exc

    average_fps = _positive_fraction(video.get("avg_frame_rate"), "average frame rate")
    nominal_fps = _positive_fraction(video.get("r_frame_rate"), "nominal frame rate")
    if average_fps != nominal_fps:
        raise ValueError(
            "ffprobe metadata indicates VFR input; transcode cleaned media to CFR first"
        )
    width = _positive_int(video.get("width"), "video width")
    height = _positive_int(video.get("height"), "video height")
    video_start = _bounded_fraction(
        video.get("start_time"), "ffprobe video start_time", nonnegative=True
    )
    if video_start != 0:
        raise ValueError(
            "nonzero video start_time is unsupported; normalize timestamps before FCPXML"
        )
    time_base = _positive_fraction(video.get("time_base"), "video time_base")

    counted_header = _optional_positive_int(
        video.get("nb_read_frames"), "exact counted frame count"
    )
    declared_header = _optional_positive_int(video.get("nb_frames"), "declared frame count")
    header_count = counted_header or declared_header
    if frame_timestamps is not None:
        frame_count = validate_cfr_timestamps(
            frame_timestamps, average_fps, header_count, time_base
        )
        if declared_header is not None and declared_header != frame_count:
            raise ValueError("declared frame count does not match counted video frames")
        if counted_header is not None and counted_header != frame_count:
            raise ValueError("counted frame metadata does not match video timestamps")
    else:
        frame_count = header_count
    if frame_count is None:
        raise ValueError("ffprobe did not report or count an exact frame count")

    duration = None
    for value in (video.get("duration"), format_info.get("duration")):
        try:
            duration = _positive_fraction(value, "media duration")
        except ValueError:
            continue
        break
    if duration is None:
        raise ValueError("ffprobe did not report an exact positive media duration")
    expected_duration = Fraction(frame_count, 1) / average_fps
    if abs(duration - expected_duration) > time_base / 2 + time_base / 1_000_000_000:
        raise ValueError(
            "ffprobe duration does not match the exact frame grid; normalize timestamps"
        )

    channels = 0
    sample_rates: set[int] = set()
    for stream in audio_streams:
        audio_start = _bounded_fraction(
            stream.get("start_time"), "ffprobe audio start_time", nonnegative=True
        )
        if audio_start != 0:
            raise ValueError(
                "nonzero or mismatched audio start_time is unsupported; "
                "normalize timestamps before FCPXML"
            )
        channels += _positive_int(stream.get("channels"), "audio channel count")
        sample_rates.add(_positive_int(stream.get("sample_rate"), "audio sample rate"))
    if len(sample_rates) > 1:
        raise ValueError("audio streams use different sample rates")
    media = MediaInfo(
        width,
        height,
        average_fps,
        frame_count,
        expected_duration,
        bool(audio_streams),
    )
    return _ProbeDetails(
        media=media,
        audio_channels=channels,
        audio_sample_rate=next(iter(sample_rates), 0),
        audio_sources=len(audio_streams),
    )


def _iter_command_stdout(command: Sequence[str], description: str) -> Iterator[str]:
    error_log = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
    try:
        try:
            process = subprocess.Popen(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=error_log,
                bufsize=1,
            )
        except OSError as exc:
            raise ValueError(f"{description} could not start: {exc}") from exc
        assert process.stdout is not None
        try:
            yield from process.stdout
            return_code = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            process.stdout.close()
        if return_code:
            error_log.seek(0)
            detail = error_log.read().strip() or "unknown ffprobe error"
            raise ValueError(f"{description} failed: {detail}")
    finally:
        error_log.close()


def probe_media(source: Path) -> _ProbeDetails:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-count_frames",
        "-show_entries",
        (
            "stream=codec_type,width,height,avg_frame_rate,r_frame_rate,nb_frames,"
            "nb_read_frames,duration,start_time,time_base,channels,sample_rate:"
            "format=duration"
        ),
        "-of",
        "json",
        str(source),
    ]
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or "unknown ffprobe error"
        raise ValueError(f"ffprobe could not inspect cleaned media: {detail}")
    timestamp_command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_frames",
        "-show_entries",
        "frame=best_effort_timestamp",
        "-of",
        "csv=p=0",
        str(source),
    ]
    return parse_ffprobe_json(
        completed.stdout,
        _iter_command_stdout(timestamp_command, "video timestamp probe"),
    )


def _plan_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def _plan_fps(value: object) -> Fraction:
    return _bounded_fraction(value, "plan fps", positive=True)


def _json_number_fraction(
    value: object, label: str, *, positive: bool = False, nonnegative: bool = False
) -> Fraction:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be a JSON number")
    return _bounded_fraction(
        value, label, positive=positive, nonnegative=nonnegative
    )


def _fraction_close(left: Fraction, right: Fraction, tolerance: Fraction) -> bool:
    return abs(left - right) <= tolerance


def validate_plan(
    plan: dict,
    source: Path,
    info: MediaInfo,
    accept_renamed_media: bool = False,
) -> list[int]:
    """Validate analyzer plan provenance, probe identity, and review partition."""
    if not isinstance(plan, dict):
        raise ValueError("cut plan must be a JSON object")
    if (
        not isinstance(plan.get("version"), int)
        or isinstance(plan.get("version"), bool)
        or plan.get("version") != PLAN_VERSION
    ):
        raise ValueError(f"cut plan version must be {PLAN_VERSION}")
    plan_source = plan.get("source")
    if not isinstance(plan_source, str) or not Path(plan_source).name:
        raise ValueError("cut plan source must be a non-empty path")
    if not accept_renamed_media and Path(plan_source).name != Path(source).name:
        raise ValueError(
            "cut plan source basename does not match cleaned media; "
            "use --accept-renamed-media only after confirming provenance"
        )
    plan_frame_count = _plan_int(plan.get("frame_count"), "plan frame_count")
    if plan_frame_count != info.frame_count:
        raise ValueError("cut plan frame_count does not match probed cleaned media")
    plan_fps = _plan_fps(plan.get("fps"))
    frame_duration = Fraction(1, 1) / info.fps
    frame_time_tolerance = frame_duration / 1_000_000
    if not _fraction_close(
        Fraction(1, 1) / plan_fps, frame_duration, frame_time_tolerance
    ):
        raise ValueError("cut plan fps does not match probed cleaned media")
    plan_duration = _json_number_fraction(
        plan.get("duration"), "plan duration", positive=True
    )
    if not _fraction_close(plan_duration, info.duration, frame_time_tolerance):
        raise ValueError("cut plan duration does not match probed cleaned media")
    if not isinstance(plan.get("requires_visual_review"), bool):
        raise ValueError("requires_visual_review must be a boolean")

    candidates_raw = plan.get("candidates")
    selected_raw = plan.get("selected_frames")
    rejected_raw = plan.get("rejected_candidates")
    if not isinstance(candidates_raw, list) or not isinstance(selected_raw, list):
        raise ValueError("candidates and selected_frames must be lists")
    if not isinstance(rejected_raw, list):
        raise ValueError("rejected_candidates must be a list")

    candidates: list[int] = []
    for record in candidates_raw:
        if not isinstance(record, dict):
            raise ValueError("every candidate must be an object")
        frame = _plan_int(record.get("frame"), "candidate frame")
        if frame < 0 or frame >= info.frame_count:
            raise ValueError("candidate frame is outside cleaned media")
        time_seconds = _json_number_fraction(
            record.get("time_seconds"), "candidate time_seconds", nonnegative=True
        )
        expected_time = Fraction(frame, 1) / info.fps
        if not _fraction_close(time_seconds, expected_time, frame_time_tolerance):
            raise ValueError("candidate time_seconds does not match its frame")
        _json_number_fraction(
            record.get("confidence"), "candidate confidence", nonnegative=True
        )
        evidence = record.get("evidence")
        if (
            not isinstance(evidence, list)
            or not evidence
            or any(not isinstance(item, str) or item not in EVIDENCE_KINDS for item in evidence)
            or len(evidence) != len(set(evidence))
        ):
            raise ValueError("candidate evidence must be a non-empty list of known evidence kinds")
        candidates.append(frame)
    if len(candidates) != len(set(candidates)):
        raise ValueError("candidate frames must be unique")

    selected = [_plan_int(frame, "selected frame") for frame in selected_raw]
    rejected: list[int] = []
    for record in rejected_raw:
        if not isinstance(record, dict):
            raise ValueError("every rejected candidate must be an object")
        frame = _plan_int(record.get("frame"), "rejected candidate frame")
        reason = record.get("reason")
        if not isinstance(reason, str) or reason not in REJECTION_REASONS:
            raise ValueError(f"invalid rejection reason: {reason!r}")
        rejected.append(frame)

    decision = plan.get("review_decision")
    if not candidates:
        if decision != "no-cuts" or selected or rejected:
            raise ValueError(
                'a zero-candidate plan requires review_decision "no-cuts" and empty decisions'
            )
        return []
    if decision is not None:
        raise ValueError("review_decision is only valid for a genuinely zero-candidate plan")

    decisions = selected + rejected
    if len(decisions) != len(set(decisions)) or sorted(decisions) != sorted(candidates):
        raise ValueError(
            "visual review must place every candidate exactly once in selected_frames "
            "or rejected_candidates"
        )
    if plan["requires_visual_review"] and not selected:
        raise ValueError(
            "visual review must select at least one candidate when candidates are present"
        )
    return validate_cuts(selected, info.frame_count)


def _parse_time(value: object) -> Fraction:
    if not isinstance(value, str) or not value.endswith("s"):
        raise ValueError(f"invalid FCPXML time: {value!r}")
    try:
        return Fraction(value[:-1])
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid FCPXML time: {value!r}") from exc


def _uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        raise ValueError("asset media URI must be a local file URI")
    return Path(unquote(parsed.path))


def verify_fcpxml(xml: str, source: Path, info: MediaInfo, cuts: Sequence[int]) -> None:
    """Parse a serialized FCPXML document and validate timeline invariants."""
    _verify_fcpxml(xml, source, info, cuts, None)


def _verify_fcpxml(
    xml: str,
    source: Path,
    info: MediaInfo,
    cuts: Sequence[int],
    probe: _ProbeDetails | None,
) -> None:
    _validate_media_info(info)
    audio = _audio_details(info, probe)
    approved = validate_cuts(cuts, info.frame_count)
    if "<!DOCTYPE fcpxml>" not in xml:
        raise ValueError("FCPXML doctype is missing")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"generated FCPXML is malformed: {exc}") from exc
    if root.tag != "fcpxml" or root.attrib.get("version") != FCPXML_VERSION:
        raise ValueError(f"generated document must be FCPXML {FCPXML_VERSION}")

    formats = root.findall("./resources/format")
    assets = root.findall("./resources/asset")
    if len(formats) != 1 or len(assets) != 1:
        raise ValueError("FCPXML must contain exactly one format and one asset resource")
    format_resource, asset = formats[0], assets[0]
    if (
        format_resource.attrib.get("id") != "r1"
        or format_resource.attrib.get("frameDuration") != frames_to_time(1, info.fps)
        or format_resource.attrib.get("width") != str(info.width)
        or format_resource.attrib.get("height") != str(info.height)
    ):
        raise ValueError("FCPXML format metadata does not match probed media")
    media_reps = asset.findall("media-rep")
    if len(media_reps) != 1:
        raise ValueError("asset must contain exactly one media representation")
    expected_total = Fraction(info.frame_count, 1) / info.fps
    if (
        asset.attrib.get("id") != "r2"
        or asset.attrib.get("format") != "r1"
        or asset.attrib.get("hasVideo") != "1"
        or _parse_time(asset.attrib.get("start")) != 0
        or _parse_time(asset.attrib.get("duration")) != expected_total
    ):
        raise ValueError("FCPXML asset metadata does not match probed media")
    media_path = _uri_path(media_reps[0].attrib.get("src", ""))
    if not media_path.is_file() or media_path.resolve() != Path(source).resolve():
        raise ValueError("asset media URI does not resolve to the cleaned media file")

    if info.has_audio:
        if asset.attrib.get("hasAudio") != "1":
            raise ValueError("asset audio metadata does not match probed media")
        if audio is None:
            if any(
                key in asset.attrib
                for key in ("audioSources", "audioChannels", "audioRate")
            ):
                raise ValueError("public FCPXML invented unavailable audio probe metadata")
        else:
            audio_channels, audio_sample_rate, audio_sources = audio
            expected_audio = {
                "audioSources": str(audio_sources),
                "audioChannels": str(audio_channels),
                "audioRate": str(audio_sample_rate),
            }
            if any(asset.attrib.get(key) != value for key, value in expected_audio.items()):
                raise ValueError("asset audio metadata does not match probed media")
    elif any(
        key in asset.attrib
        for key in ("hasAudio", "audioSources", "audioChannels", "audioRate")
    ):
        raise ValueError("video-only asset unexpectedly declares audio metadata")

    sequences = root.findall("./library/event/project/sequence")
    if len(sequences) != 1:
        raise ValueError("FCPXML must contain exactly one sequence")
    sequence = sequences[0]
    expected_sequence_rate = (
        SEQUENCE_AUDIO_RATES.get(audio[1]) if audio is not None else None
    )
    if sequence.attrib.get("audioRate") != expected_sequence_rate:
        raise ValueError("sequence audioRate is not a valid enum for probed media")
    if _parse_time(sequence.attrib.get("duration")) != expected_total:
        raise ValueError("sequence duration does not cover every media frame")
    spines = sequence.findall("spine")
    if len(spines) != 1:
        raise ValueError("sequence must contain exactly one spine")
    clips = spines[0].findall("asset-clip")
    if len(clips) != len(approved) + 1:
        raise ValueError("spine clip count does not match approved cuts")
    boundaries = [0, *approved, info.frame_count]
    duration_sum = Fraction()
    frame_sum = Fraction()
    for clip, start, end in zip(clips, boundaries, boundaries[1:]):
        expected_start = Fraction(start, 1) / info.fps
        expected_duration = Fraction(end - start, 1) / info.fps
        offset = _parse_time(clip.attrib.get("offset"))
        clip_start = _parse_time(clip.attrib.get("start"))
        clip_duration = _parse_time(clip.attrib.get("duration"))
        if clip.attrib.get("ref") != asset.attrib.get("id"):
            raise ValueError("every clip must reference the single media asset")
        if offset != expected_start or clip_start != offset:
            raise ValueError("clip offsets and starts must be strictly contiguous")
        if clip_duration <= 0 or clip_duration != expected_duration:
            raise ValueError("clip durations must be positive and frame exact")
        duration_sum += clip_duration
        frame_sum += clip_duration * info.fps
    if duration_sum != expected_total or frame_sum != info.frame_count:
        raise ValueError("spine clips do not cover every frame exactly once")


def ensure_distinct_paths(source: Path, output: Path) -> None:
    """Reject direct, symlink, and hardlink aliases before output mutations."""
    source = Path(source)
    output = Path(output)
    try:
        source_stat = source.stat()
    except OSError as exc:
        raise ValueError(f"cannot stat cleaned media: {exc}") from exc
    try:
        output_stat = output.stat()
    except FileNotFoundError:
        output_stat = None
    except OSError as exc:
        raise ValueError(f"cannot stat output path: {exc}") from exc
    if source.absolute() == output.absolute() or (
        output_stat is not None
        and (source_stat.st_dev, source_stat.st_ino) == (output_stat.st_dev, output_stat.st_ino)
    ):
        raise ValueError(
            "output path aliases the cleaned media (directly, via symlink, or hardlink)"
        )


def _rename_noreplace_syscall(source: Path, destination: Path) -> None:
    """Atomically move a path without replacing a destination name."""
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    ctypes.set_errno(0)
    if sys.platform == "darwin":
        try:
            rename_function = libc.renamex_np
        except AttributeError as exc:
            raise OSError(errno.ENOSYS, "renamex_np is unavailable") from exc
        rename_function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename_function.restype = ctypes.c_int
        result = rename_function(source_bytes, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        try:
            rename_function = libc.renameat2
        except AttributeError as exc:
            raise OSError(errno.ENOSYS, "renameat2 is unavailable") from exc
        rename_function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_function.restype = ctypes.c_int
        result = rename_function(-100, source_bytes, -100, destination_bytes, 0x00000001)
    else:
        raise OSError(errno.ENOSYS, f"unsupported platform: {sys.platform}")
    if result:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(error_number, os.strerror(error_number), os.fspath(destination))


def _recovery_path(output: Path, purpose: str) -> Path:
    return output.parent / f".{output.name}.recovery-{purpose}-{uuid.uuid4().hex}"


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _copy_owned_fd(descriptor: int, output: Path) -> Path:
    """Materialize immutable owned bytes at a new exclusive recovery name."""
    recovery = _recovery_path(output, "owned")
    recovery_descriptor = None
    try:
        recovery_descriptor = os.open(
            recovery, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
        offset = 0
        while True:
            chunk = os.pread(descriptor, 1024 * 1024, offset)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(recovery_descriptor, view)
                if written <= 0:
                    raise OSError(errno.EIO, "short write while preserving owned XML")
                view = view[written:]
            offset += len(chunk)
        os.fsync(recovery_descriptor)
    except OSError as exc:
        raise ValueError(
            f"could not materialize owned XML recovery at {recovery}: {exc}"
        ) from exc
    finally:
        if recovery_descriptor is not None:
            os.close(recovery_descriptor)
    return recovery


def _raise_initial_publication_failure(
    output: Path,
    stage: Path,
    descriptor: int,
    owned_stat: os.stat_result,
    failure: OSError,
) -> None:
    """Retain owned bytes without touching a possibly substituted stage pathname."""
    try:
        stage_is_owned = _same_inode(os.lstat(stage), owned_stat)
    except OSError:
        stage_is_owned = False
    if stage_is_owned:
        owned_location = stage
        owned_label = "owned stage retained"
    else:
        owned_location = _copy_owned_fd(descriptor, output)
        owned_label = "owned recovery retained"
    if failure.errno in {errno.EEXIST, errno.ENOTEMPTY}:
        reason = "output appeared before atomic publication"
    elif failure.errno in {
        errno.ENOSYS,
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.ENOSYS),
        getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
    }:
        reason = "atomic no-replace rename is unsupported"
    else:
        reason = f"atomic publication failed: {failure}"
    raise ValueError(f"{reason}; {owned_label} at {owned_location}") from failure


def _recover_after_publication(
    output: Path, descriptor: int, failure: Exception
) -> None:
    """Preserve owned bytes, then atomically quarantine the current public occupant."""
    owned_recovery = _copy_owned_fd(descriptor, output)
    public_recovery = _recovery_path(output, "public")
    try:
        _rename_noreplace_syscall(output, public_recovery)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise ValueError(
                f"post-publication validation failed: {failure}; public output is absent; "
                f"owned recovery retained at {owned_recovery}"
            ) from failure
        raise ValueError(
            f"post-publication validation failed: {failure}; could not quarantine "
            f"{output}: {exc}; owned recovery retained at {owned_recovery}"
        ) from failure
    raise ValueError(
        f"post-publication validation failed: {failure}; current public artifact "
        f"quarantined at {public_recovery}; owned recovery retained at {owned_recovery}"
    ) from failure


def _create_owned_stage(
    output: Path, contents: str
) -> tuple[Path, int, os.stat_result]:
    """Create and fsync a stage while retaining its open descriptor."""
    output = Path(output)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    stage = Path(temporary_name)
    try:
        data = contents.encode("utf-8")
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(errno.EIO, "short write while staging XML")
            view = view[written:]
        os.fsync(descriptor)
        owned_stat = os.fstat(descriptor)
    except Exception as exc:
        os.close(descriptor)
        raise ValueError(f"XML staging failed; stage retained at {stage}: {exc}") from exc
    return stage, descriptor, owned_stat


def _publish_and_verify(
    output: Path, contents: str, verifier: Callable[[str], None]
) -> None:
    output = Path(output)
    stage, descriptor, owned_stat = _create_owned_stage(output, contents)
    try:
        try:
            _rename_noreplace_syscall(stage, output)
        except OSError as exc:
            _raise_initial_publication_failure(
                output, stage, descriptor, owned_stat, exc
            )
        try:
            directory_fd = os.open(output.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            written = output.read_text(encoding="utf-8")
            verifier(written)
            current_stat = os.lstat(output)
            if not _same_inode(current_stat, owned_stat):
                raise ValueError("public output inode changed after XML verification")
        except Exception as exc:
            _recover_after_publication(output, descriptor, exc)
    finally:
        os.close(descriptor)


def atomic_write_noreplace(output: Path, contents: str) -> None:
    """Publish UTF-8 text atomically without replacing an existing destination."""
    def verify_exact(written: str) -> None:
        if written != contents:
            raise ValueError("published text differs from staged contents")

    _publish_and_verify(output, contents, verify_exact)


def _regular_file(path: Path, label: str) -> None:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError as exc:
        raise ValueError(f"{label} does not exist: {path}") from exc
    except OSError as exc:
        raise ValueError(f"cannot inspect {label}: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"{label} must be a regular file: {path}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cleaned_media", type=Path)
    parser.add_argument("cut_plan", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--event-name", default="Reviewed cuts")
    parser.add_argument("--accept-renamed-media", action="store_true")
    return parser


def run(args: argparse.Namespace) -> None:
    source = args.cleaned_media
    plan_path = args.cut_plan
    output = args.output
    if shutil.which("ffprobe") is None:
        raise ValueError("ffprobe is required; install ffmpeg and ensure ffprobe is on PATH")
    _regular_file(source, "cleaned media")
    _regular_file(plan_path, "cut plan")
    ensure_distinct_paths(source, output)
    if os.path.lexists(output):
        raise FileExistsError(f"output already exists (refusing to overwrite): {output}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("cut plan is not valid UTF-8") from exc
    probe = probe_media(source)
    info = probe.media
    cuts = validate_plan(plan, source, info, args.accept_renamed_media)
    xml = _build_fcpxml(source, info, cuts, args.event_name, probe)
    _verify_fcpxml(xml, source, info, cuts, probe)

    def verify_written(written: str) -> None:
        _verify_fcpxml(written, source, info, cuts, probe)

    _publish_and_verify(output, xml, verify_written)
    print(f"Wrote verified FCPXML: {output}")


def main(argv: Sequence[str] | None = None) -> int:
    if sys.version_info < (3, 10):
        print("error: Python 3.10 or newer is required", file=sys.stderr)
        return 2
    try:
        args = _parser().parse_args(argv)
        run(args)
    except (
        ValueError,
        OSError,
        json.JSONDecodeError,
        ET.ParseError,
        KeyError,
        TypeError,
        AttributeError,
        OverflowError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
