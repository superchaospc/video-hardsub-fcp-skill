#!/usr/bin/env python3
"""Find fine jump-cut candidates and render review sheets.

This command deliberately stops at a visual-review plan.  It does not generate
FCPXML, because motion-only evidence cannot safely distinguish a real edit from
steam, camera shake, or ingredient movement.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import errno
import fcntl
import io
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import uuid
from collections import Counter, deque
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator, Sequence


SCENE_THRESHOLD = 0.012
REJECTION_REASONS = frozenset(
    {
        "persistent-motion",
        "steam",
        "ingredient-motion",
        "camera-shake",
        "no-visible-discontinuity",
    }
)


class AnalysisError(RuntimeError):
    """A user-actionable analysis failure."""


@dataclass(frozen=True)
class Evidence:
    frame: int
    kind: str
    score: float


@dataclass(frozen=True)
class Candidate:
    frame: int
    time_seconds: float
    confidence: float
    reasons: list[str]


@dataclass(frozen=True)
class ProbeInfo:
    fps: float
    frame_count: int
    duration: float
    start_time: float
    width: int = 0
    height: int = 0
    time_base: float = 0.0
    fps_ratio: str = ""
    time_base_ratio: str = ""


def parse_fps(value: str | float | int) -> float:
    """Parse ffprobe's rational or decimal frame-rate representation."""
    text = str(value).strip()
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            result = float(numerator) / float(denominator)
        else:
            result = float(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise AnalysisError(f"Invalid frame rate reported by ffprobe: {value!r}") from exc
    if not math.isfinite(result) or result <= 0:
        raise AnalysisError(f"Invalid frame rate reported by ffprobe: {value!r}")
    return result


def isolated_peaks(scores: Sequence[float], minimum: float, ratio: float) -> list[int]:
    """Return sharp one-frame peaks; reject runs of sustained high motion.

    The baseline is the median of the centered five-frame window (shortened only
    at the stream edges).  The explicit neighbor check is intentionally strict:
    persistent steam, camera motion, and fast action generally produce runs.
    """
    peaks: list[int] = []
    for index, raw_score in enumerate(scores):
        score = float(raw_score)
        if score < minimum:
            continue
        start = max(0, index - 2)
        baseline = statistics.median(float(item) for item in scores[start : index + 3])
        if score < ratio * baseline:
            continue
        if index > 0 and float(scores[index - 1]) >= 0.7 * score:
            continue
        if index + 1 < len(scores) and float(scores[index + 1]) >= 0.7 * score:
            continue
        peaks.append(index)
    return peaks


def _evidence_weight(event: Evidence) -> float:
    if event.kind == "keyframe":
        return 2.0
    if event.kind == "scene":
        return 3.0 * min(max(event.score, 0.0) / 0.08, 2.0)
    if event.kind == "ydif":
        return min(max(event.score, 0.0) / 12.0, 2.0)
    return 0.0


def _cluster_anchor(cluster: Sequence[Evidence]) -> int:
    """Pick the frame with the strongest aligned, multi-kind support.

    ffmpeg filters may report the same physical boundary one frame apart.  A
    one-frame alignment window counts those distinct kinds as combined support;
    remaining ties prefer the weighted support, then the cluster's center.
    """
    frames = sorted({event.frame for event in cluster})
    center = statistics.median(event.frame for event in cluster)

    def rank(frame: int) -> tuple[int, float, float, int]:
        aligned = [event for event in cluster if abs(event.frame - frame) <= 1]
        kinds = len({event.kind for event in aligned})
        support = sum(_evidence_weight(event) for event in aligned)
        return kinds, support, -abs(frame - center), -frame

    return max(frames, key=rank)


def merge_evidence(
    events: Sequence[Evidence], cluster_frames: int, fps: float = 30.0
) -> list[Candidate]:
    """Cluster nearby evidence and choose the strongest supported frame."""
    if cluster_frames < 0:
        raise ValueError("cluster_frames must be non-negative")
    fps = parse_fps(fps)
    ordered = sorted(events, key=lambda item: (item.frame, item.kind, item.score))
    if not ordered:
        return []

    clusters: list[list[Evidence]] = [[ordered[0]]]
    for event in ordered[1:]:
        if event.frame - clusters[-1][-1].frame <= cluster_frames:
            clusters[-1].append(event)
        else:
            clusters.append([event])

    candidates: list[Candidate] = []
    for cluster in clusters:
        frame = _cluster_anchor(cluster)
        reasons = sorted({event.kind for event in cluster})
        confidence = sum(_evidence_weight(event) for event in cluster)
        candidates.append(Candidate(frame, frame / fps, confidence, reasons))
    return candidates


def make_plan(
    source: str, fps: float, frame_count: int, candidates: Sequence[Candidate]
) -> dict:
    """Return a versioned plan that must be reviewed before XML generation."""
    fps_value = parse_fps(fps)
    return {
        "version": 1,
        "source": str(source),
        "fps": fps_value,
        "frame_count": int(frame_count),
        "duration": int(frame_count) / fps_value,
        "candidates": [
            {
                "frame": candidate.frame,
                "time_seconds": candidate.time_seconds,
                "confidence": candidate.confidence,
                "evidence": list(candidate.reasons),
            }
            for candidate in candidates
        ],
        "requires_visual_review": True,
        "selected_frames": [],
        "rejected_candidates": [],
    }


def validate_review_decisions(plan: dict) -> None:
    """Validate the completed visual-review partition for downstream callers."""
    candidates = [int(item["frame"]) for item in plan.get("candidates", [])]
    selected = [int(frame) for frame in plan.get("selected_frames", [])]
    rejected_items = plan.get("rejected_candidates", [])
    rejected = [int(item["frame"]) for item in rejected_items]
    bad_reasons = [item.get("reason") for item in rejected_items if item.get("reason") not in REJECTION_REASONS]
    if bad_reasons:
        raise AnalysisError(f"Invalid rejection reason(s): {bad_reasons}")
    decisions = selected + rejected
    if len(decisions) != len(set(decisions)) or sorted(decisions) != sorted(candidates):
        raise AnalysisError(
            "Visual review must place every candidate exactly once in "
            "selected_frames or rejected_candidates"
        )


def parse_probe_json(output: str) -> ProbeInfo:
    try:
        payload = json.loads(output)
        stream = payload["streams"][0]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise AnalysisError("ffprobe returned malformed video metadata") from exc

    try:
        average_fps = parse_fps(stream.get("avg_frame_rate"))
        nominal_fps = parse_fps(stream.get("r_frame_rate"))
    except AnalysisError as exc:
        raise AnalysisError(
            "Cannot confirm constant-frame-rate (CFR) input from ffprobe metadata; "
            "transcode the source to CFR before analysis"
        ) from exc
    if not math.isclose(average_fps, nominal_fps, rel_tol=1e-6, abs_tol=1e-6):
        raise AnalysisError(
            "Detected variable-frame-rate input (avg_frame_rate differs from "
            "r_frame_rate); transcode to CFR before frame-based cut analysis"
        )
    fps = average_fps
    duration: float | None = None
    for duration_value in (
        stream.get("duration"),
        payload.get("format", {}).get("duration"),
    ):
        try:
            parsed_duration = float(duration_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed_duration) and parsed_duration > 0:
            duration = parsed_duration
            break
    if duration is None:
        raise AnalysisError("ffprobe did not report a usable video duration")

    raw_count = stream.get("nb_frames")
    try:
        frame_count = int(raw_count)
    except (TypeError, ValueError):
        frame_count = round(duration * fps)
    if frame_count <= 0:
        raise AnalysisError("ffprobe did not report a usable video frame count")
    raw_start = stream.get("start_time")
    try:
        start_time = 0.0 if raw_start in (None, "N/A") else float(raw_start)
    except (TypeError, ValueError) as exc:
        raise AnalysisError("ffprobe reported an invalid video stream start_time") from exc
    if not math.isfinite(start_time):
        raise AnalysisError("ffprobe reported an invalid video stream start_time")
    try:
        width = int(stream.get("width", 0))
        height = int(stream.get("height", 0))
    except (TypeError, ValueError) as exc:
        raise AnalysisError("ffprobe reported invalid video dimensions") from exc
    try:
        time_base = parse_fps(stream.get("time_base"))
    except AnalysisError as exc:
        raise AnalysisError(
            "ffprobe did not report a usable stream time_base for CFR validation"
        ) from exc
    return ProbeInfo(
        fps=fps,
        frame_count=frame_count,
        duration=duration,
        start_time=start_time,
        width=width,
        height=height,
        time_base=time_base,
        fps_ratio=str(stream.get("avg_frame_rate")),
        time_base_ratio=str(stream.get("time_base")),
    )


_FRAME_HEADER = re.compile(
    r"\bframe:\s*(?P<frame>\d+)(?:.*?\bpts_time:\s*(?P<time>[-+0-9.eE]+))?"
)


def parse_metadata(
    output: str | Iterable[str],
    key: str,
    kind: str,
    fps: float,
    start_time: float = 0.0,
) -> list[Evidence]:
    """Collect metadata events from a string or one-pass line iterable."""
    return list(iter_metadata(output, key, kind, fps, start_time=start_time))


def iter_metadata(
    output: str | Iterable[str],
    key: str,
    kind: str,
    fps: float,
    start_time: float = 0.0,
) -> Iterator[Evidence]:
    """Parse ffmpeg metadata output, tolerating its two common line formats.

    Both ``frame:N pts:...`` headers and subsequent ``lavfi.*=value`` lines are
    accepted.  A valid pts_time is preferred because a select filter renumbers
    output frames; malformed headers or values are ignored without losing the
    last valid frame association.
    """
    fps_value = parse_fps(fps)
    current_frame: int | None = None
    value_pattern = re.compile(rf"(?:^|\s){re.escape(key)}\s*=\s*(\S+)")
    lines: Iterable[str] = io.StringIO(output) if isinstance(output, str) else output
    for line in lines:
        header = _FRAME_HEADER.search(line)
        if header:
            frame = int(header.group("frame"))
            time_text = header.group("time")
            if time_text is not None:
                try:
                    frame = round((float(time_text) - start_time) * fps_value)
                except ValueError:
                    pass
            current_frame = frame
        match = value_pattern.search(line)
        if match and current_frame is not None:
            try:
                score = float(match.group(1))
            except ValueError:
                continue
            if math.isfinite(score):
                yield Evidence(current_frame, kind, score)


def parse_keyframe_timestamps(
    output: str | Iterable[str], fps: float, start_time: float = 0.0
) -> list[Evidence]:
    """Convert ffprobe CSV keyframe timestamps into evidence."""
    fps_value = parse_fps(fps)
    events: list[Evidence] = []
    lines: Iterable[str] = io.StringIO(output) if isinstance(output, str) else output
    for line in lines:
        fields = [part.strip() for part in line.split(",")]
        timestamp: float | None = None
        for field in reversed(fields):
            try:
                timestamp = float(field)
                break
            except ValueError:
                continue
        if timestamp is not None and math.isfinite(timestamp):
            normalized = timestamp - start_time
            if normalized >= -0.5 / fps_value:
                events.append(Evidence(max(0, round(normalized * fps_value)), "keyframe", 1.0))
    return events


def _iter_frame_scores(
    events: Iterable[Evidence], frame_count: int
) -> Iterator[tuple[int, float]]:
    """Expand ordered sparse metadata to frame scores with constant memory."""
    current_frame = 0
    current_score = 0.0
    for event in events:
        if event.frame < current_frame or event.frame >= frame_count:
            continue
        while current_frame < event.frame:
            yield current_frame, current_score
            current_frame += 1
            current_score = 0.0
        current_score = max(current_score, float(event.score))
    while current_frame < frame_count:
        yield current_frame, current_score
        current_frame += 1
        current_score = 0.0


def iter_ydif_peak_events(
    events: Iterable[Evidence],
    frame_count: int,
    minimum: float = 8.0,
    ratio: float = 2.5,
) -> Iterator[Evidence]:
    """Yield five-frame YDIF peaks after two following samples arrive."""
    def peak_from_window(
        samples: Sequence[tuple[int, float]], center_frame: int
    ) -> Evidence | None:
        relevant = [
            item for item in samples if center_frame - 2 <= item[0] <= center_frame + 2
        ]
        center_index = next(
            (index for index, item in enumerate(relevant) if item[0] == center_frame),
            None,
        )
        if center_index is None:
            return None
        center_score = relevant[center_index][1]
        baseline = statistics.median(item[1] for item in relevant)
        if center_score < minimum or center_score < ratio * baseline:
            return None
        if center_index > 0 and relevant[center_index - 1][1] >= 0.7 * center_score:
            return None
        if (
            center_index + 1 < len(relevant)
            and relevant[center_index + 1][1] >= 0.7 * center_score
        ):
            return None
        return Evidence(center_frame, "ydif", center_score)

    window: deque[tuple[int, float]] = deque(maxlen=5)
    for frame, score in _iter_frame_scores(events, frame_count):
        window.append((frame, score))
        if len(window) < 3:
            continue
        peak = peak_from_window(window, frame - 2)
        if peak is not None:
            yield peak
    for center_frame in range(max(0, frame_count - 2), frame_count):
        peak = peak_from_window(window, center_frame)
        if peak is not None:
            yield peak


def build_scene_command(source: str | Path) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-copyts",
        "-i",
        str(source),
        "-vf",
        f"select='gt(scene,{SCENE_THRESHOLD})',metadata=print",
        "-an",
        "-f",
        "null",
        "-",
    ]


def build_ydif_command(source: str | Path) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-copyts",
        "-i",
        str(source),
        "-vf",
        "signalstats,metadata=print:key=lavfi.signalstats.YDIF",
        "-an",
        "-f",
        "null",
        "-",
    ]


def _run(command: Sequence[str], description: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, text=True, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "unknown ffmpeg error").strip()
        raise AnalysisError(f"{description} failed: {detail}") from exc
    except OSError as exc:
        raise AnalysisError(f"{description} could not start: {exc}") from exc


def iter_command_stderr(command: Sequence[str], description: str) -> Iterator[str]:
    """Yield stderr line-by-line without retaining full per-frame metadata."""
    try:
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
    except OSError as exc:
        raise AnalysisError(f"{description} could not start: {exc}") from exc
    assert process.stderr is not None
    tail: deque[str] = deque(maxlen=20)
    try:
        for line in process.stderr:
            tail.append(line.rstrip())
            yield line
        return_code = process.wait()
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        process.stderr.close()
    if return_code != 0:
        detail = "\n".join(tail).strip() or "unknown ffmpeg error"
        raise AnalysisError(f"{description} failed: {detail}")


def iter_command_stdout(command: Sequence[str], description: str) -> Iterator[str]:
    """Yield stdout line-by-line for large ffprobe frame listings."""
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
            raise AnalysisError(f"{description} could not start: {exc}") from exc
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
        if return_code != 0:
            error_log.seek(0)
            detail = error_log.read().strip() or "unknown ffprobe error"
            raise AnalysisError(f"{description} failed: {detail}")
    finally:
        error_log.close()


def validate_cfr_timestamps(
    lines: Iterable[str],
    fps: float | str,
    expected_frames: int,
    start_time: float = 0.0,
    time_base: float | str = 0.0,
    integer_pts: bool = False,
) -> None:
    """Reject timestamps over half a time-base tick from the exact CFR grid."""
    try:
        fps_fraction = Fraction(str(fps))
        time_base_fraction = Fraction(str(time_base))
    except (ValueError, ZeroDivisionError) as exc:
        raise AnalysisError("Invalid rational timing values for CFR validation") from exc
    if fps_fraction <= 0 or time_base_fraction <= 0:
        raise AnalysisError("Invalid rational timing values for CFR validation")
    half_tick = Fraction(1, 2)
    tick_epsilon = Fraction(1, 1_000_000_000)
    time_epsilon = time_base_fraction * tick_epsilon
    observed = 0
    first_pts: int | None = None
    for line in lines:
        first_field = line.strip().split(",", 1)[0]
        if integer_pts:
            try:
                pts = int(first_field)
            except ValueError:
                continue
            if first_pts is None:
                first_pts = pts
            ideal_ticks = Fraction(first_pts) + Fraction(observed, 1) / (
                fps_fraction * time_base_fraction
            )
            outside_grid = abs(Fraction(pts) - ideal_ticks) > half_tick + tick_epsilon
        else:
            try:
                timestamp = Fraction(first_field)
            except (ValueError, ZeroDivisionError):
                continue
            expected = Fraction(str(start_time)) + Fraction(observed, 1) / fps_fraction
            outside_grid = (
                abs(timestamp - expected)
                > time_base_fraction * half_tick + time_epsilon
            )
        if outside_grid:
            raise AnalysisError(
                "Detected variable-frame-rate timestamps; transcode to CFR "
                "before frame-based cut analysis"
            )
        observed += 1
    if expected_frames > 1 and observed < 2:
        raise AnalysisError(
            "Cannot confirm CFR because ffprobe returned too few frame timestamps"
        )


def probe_video(source: Path) -> tuple[ProbeInfo, list[Evidence]]:
    metadata_command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=avg_frame_rate,r_frame_rate,nb_frames,duration,start_time,width,height,time_base:format=duration",
        "-of",
        "json",
        str(source),
    ]
    info = parse_probe_json(_run(metadata_command, "video probe").stdout)
    timing_command = [
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
    validate_cfr_timestamps(
        iter_command_stdout(timing_command, "CFR timestamp probe"),
        info.fps_ratio or info.fps,
        info.frame_count,
        start_time=info.start_time,
        time_base=info.time_base_ratio or info.time_base,
        integer_pts=True,
    )
    keyframe_command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-skip_frame",
        "nokey",
        "-show_frames",
        "-show_entries",
        "frame=best_effort_timestamp_time",
        "-of",
        "csv=p=0",
        str(source),
    ]
    keyframes = parse_keyframe_timestamps(
        iter_command_stdout(keyframe_command, "keyframe probe"),
        info.fps,
        start_time=info.start_time,
    )
    return info, keyframes


def filter_edge_candidates(
    candidates: Sequence[Candidate], frame_count: int
) -> list[Candidate]:
    """Remove frame zero and the final two frames from review consideration."""
    return sorted(
        (item for item in candidates if 0 < item.frame < frame_count - 2),
        key=lambda item: item.frame,
    )


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        rename_noreplace(temporary, path)
    except BaseException:
        if _lexists(temporary):
            _report_retained(temporary, "atomic JSON temporary")
        raise


def make_review_index(candidates: Sequence[Candidate]) -> dict:
    """Build the deterministic page/tile mapping used beside review sheets."""
    pages: list[dict] = []
    for offset in range(0, len(candidates), 10):
        page_number = offset // 10 + 1
        page_candidates = candidates[offset : offset + 10]
        tiles = [
            {
                "tile": tile_number,
                "frame": candidate.frame,
                "before_frame": candidate.frame - 1,
            }
            for tile_number, candidate in enumerate(page_candidates, start=1)
        ]
        pages.append(
            {"page": page_number, "file": f"sheet-{page_number:03d}.png", "tiles": tiles}
        )
    return {"version": 1, "pages": pages}


def build_review_commands(
    source: Path,
    candidates: Sequence[Candidate],
    review_dir: Path,
    width: int = 320,
    height: int = 180,
    fps: float = 30.0,
) -> list[list[str]]:
    """Build a constant-size decoder→raw-frame→sheet pipeline."""
    if not candidates:
        return []
    if width <= 0 or height <= 0:
        raise AnalysisError("Cannot render review sheets without valid video dimensions")
    scaled_width = 320
    scaled_height = max(2, round(height * scaled_width / width))
    if scaled_height % 2:
        scaled_height += 1
    page_count = math.ceil(len(candidates) / 10)
    decoder = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        f"scale={scaled_width}:{scaled_height}",
        "-pix_fmt",
        "rgb24",
        "-fps_mode",
        "passthrough",
        "-f",
        "rawvideo",
        "-",
    ]
    tiler = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-video_size",
        f"{scaled_width}x{scaled_height}",
        "-framerate",
        str(parse_fps(fps)),
        "-i",
        "-",
        "-vf",
        "tile=4x5:nb_frames=20:padding=2:margin=2:color=black",
        "-frames:v",
        str(page_count),
        "-y",
        str(review_dir / "sheet-%03d.png"),
    ]
    return [decoder, tiler]


def _read_raw_frame(stream: BinaryIO, frame_size: int) -> bytes | None:
    data = bytearray()
    while len(data) < frame_size:
        chunk = stream.read(frame_size - len(data))
        if not chunk:
            if data:
                raise AnalysisError("Decoder returned a truncated raw video frame")
            return None
        data.extend(chunk)
    return bytes(data)


def _run_review_pipeline(
    commands: Sequence[Sequence[str]],
    requested_frames: Sequence[int],
    frame_size: int,
) -> None:
    if len(commands) != 2:
        raise ValueError("review pipeline requires decoder and tiler commands")
    decoder_error = tempfile.TemporaryFile(mode="w+b")
    tiler_error = tempfile.TemporaryFile(mode="w+b")
    decoder: subprocess.Popen[bytes] | None = None
    tiler: subprocess.Popen[bytes] | None = None
    try:
        try:
            tiler = subprocess.Popen(
                commands[1],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=tiler_error,
            )
            decoder = subprocess.Popen(
                commands[0],
                stdout=subprocess.PIPE,
                stderr=decoder_error,
            )
        except OSError as exc:
            raise AnalysisError(f"Review pipeline could not start: {exc}") from exc
        assert decoder.stdout is not None
        assert tiler.stdin is not None
        needed = Counter(requested_frames)
        frame_number = 0
        while True:
            frame = _read_raw_frame(decoder.stdout, frame_size)
            if frame is None:
                break
            for _ in range(needed.pop(frame_number, 0)):
                tiler.stdin.write(frame)
            frame_number += 1
        decoder.stdout.close()
        decoder_code = decoder.wait()
        tiler.stdin.close()
        tiler_code = tiler.wait()
        if decoder_code != 0 or tiler_code != 0:
            decoder_error.seek(0)
            tiler_error.seek(0)
            detail = (decoder_error.read() + tiler_error.read()).decode(
                errors="replace"
            ).strip()
            raise AnalysisError(f"Review pipeline failed: {detail or 'unknown ffmpeg error'}")
        if needed:
            raise AnalysisError(
                "Review pipeline could not decode requested frame(s): "
                + ", ".join(str(frame) for frame in sorted(needed))
            )
    except BaseException:
        for process in (decoder, tiler):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait()
        raise
    finally:
        decoder_error.close()
        tiler_error.close()


def render_review(
    source: Path,
    candidates: Sequence[Candidate],
    review_dir: Path,
    width: int = 320,
    height: int = 180,
    fps: float = 30.0,
) -> dict:
    review_dir.mkdir(parents=True, exist_ok=False)
    index = make_review_index(candidates)
    commands = build_review_commands(
        source, candidates, review_dir, width=width, height=height, fps=fps
    )
    if commands:
        scaled_height = max(2, round(height * 320 / width))
        if scaled_height % 2:
            scaled_height += 1
        requested = [
            frame
            for candidate in candidates
            for frame in (candidate.frame - 1, candidate.frame)
        ]
        _run_review_pipeline(commands, requested, 320 * scaled_height * 3)
    _atomic_json(review_dir / "index.json", index)
    return index


def _require_tools() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        joined = ", ".join(missing)
        raise AnalysisError(
            f"Required tool(s) not found: {joined}. Install ffmpeg (which includes ffprobe) "
            "and ensure both commands are on PATH."
        )


def _lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _reject_unsafe_paths(source: Path, output: Path, review_dir: Path) -> None:
    """Reject artifact paths that could overwrite or remove the input."""
    resolved_source = source.resolve(strict=True)
    resolved_output = output.resolve(strict=False)
    resolved_review = review_dir.resolve(strict=False)
    output_aliases_source = resolved_output == resolved_source
    if _lexists(output):
        output_aliases_source = output_aliases_source or os.path.samefile(source, output)
    if output_aliases_source:
        raise AnalysisError("Output and input resolve to the same file; source will not be modified")
    if resolved_review == resolved_source or resolved_review in resolved_source.parents:
        raise AnalysisError("Review directory cannot be the input file or contain the input file")
    if resolved_output == resolved_review or resolved_review in resolved_output.parents:
        raise AnalysisError("Output plan must be outside the review directory")


@contextlib.contextmanager
def _artifact_locks(output: Path, review_dir: Path) -> Iterator[None]:
    lock_paths = sorted(
        {
            output.parent / f".{output.name}.lock",
            review_dir.parent / f".{review_dir.name}.lock",
        },
        key=lambda path: str(path),
    )
    acquired: list[tuple[Path, int]] = []
    try:
        for lock_path in lock_paths:
            try:
                flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(lock_path, flags, 0o600)
            except OSError as exc:
                raise AnalysisError(f"Could not open analyzer lock {lock_path}: {exc}") from exc
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(descriptor)
                raise AnalysisError(
                    f"Another analyzer may be publishing these artifacts ({lock_path}); "
                    "wait for it to finish and retry"
                ) from exc
            acquired.append((lock_path, descriptor))
        yield
    finally:
        for _, descriptor in reversed(acquired):
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _report_retained(path: Path, purpose: str) -> None:
    print(f"warning: retained {purpose} artifact for safe cleanup: {path}", file=sys.stderr)


def _validate_staged_artifacts(plan_path: Path, review_dir: Path, plan: dict) -> None:
    try:
        with plan_path.open(encoding="utf-8") as handle:
            stored_plan = json.load(handle)
        with (review_dir / "index.json").open(encoding="utf-8") as handle:
            stored_index = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"Staged artifact validation failed: {exc}") from exc
    if stored_plan != plan:
        raise AnalysisError("Staged plan failed deterministic JSON validation")
    expected_index = make_review_index(
        [
            Candidate(
                item["frame"],
                item["time_seconds"],
                item["confidence"],
                item["evidence"],
            )
            for item in plan["candidates"]
        ]
    )
    if stored_index != expected_index:
        raise AnalysisError("Staged review index does not match candidate frames")
    for page in stored_index["pages"]:
        sheet = review_dir / page["file"]
        if not sheet.is_file() or sheet.stat().st_size == 0:
            raise AnalysisError(f"Staged review sheet is missing or empty: {sheet.name}")


def _rename_noreplace_syscall(source: Path, destination: Path) -> None:
    """Invoke the platform's atomic no-replace rename primitive."""
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
    if result != 0:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(error_number, os.strerror(error_number), os.fspath(destination))


def rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically install a file or directory without replacing its target."""
    try:
        _rename_noreplace_syscall(Path(source), Path(destination))
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            raise AnalysisError(
                f"Artifact target already exists: {destination}. "
                "Re-run with --force only if replacement is intended."
            ) from exc
        unsupported_errors = {
            errno.ENOSYS,
            errno.EINVAL,
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
        }
        if exc.errno in unsupported_errors:
            raise AnalysisError(
                "Atomic no-replace publication is unsupported on this platform or "
                "filesystem; move the work directory to a supported macOS/Linux "
                "filesystem. Publication stopped without using a racy fallback"
            ) from exc
        raise AnalysisError(
            f"Could not publish {destination} without replacement: {exc}"
        ) from exc


def _recovery_path(target: Path, purpose: str) -> Path:
    return target.parent / f".{target.name}.recovery-{purpose}-{uuid.uuid4().hex}"


def _copy_open_plan_to_recovery(
    published_handle: BinaryIO, output: Path
) -> Path:
    """Snapshot the open published plan at an exclusive, discoverable name."""
    recovery = _recovery_path(output, "owned")
    descriptor: int | None = None
    try:
        descriptor = os.open(recovery, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        source_descriptor = published_handle.fileno()
        offset = 0
        while True:
            chunk = os.pread(source_descriptor, 1024 * 1024, offset)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError(errno.EIO, "short write while preserving plan")
                view = view[written:]
            offset += len(chunk)
        os.fsync(descriptor)
    except OSError as exc:
        raise AnalysisError(
            f"Could not preserve the published plan; partial recovery may remain at "
            f"{recovery}: {exc}"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return recovery


def _rollback_published_plan(
    output: Path, published_handle: BinaryIO
) -> list[Path]:
    """Preserve our plan and quarantine the public name without deleting either."""
    recoveries = [_copy_open_plan_to_recovery(published_handle, output)]
    public_recovery = _recovery_path(output, "public")
    try:
        rename_noreplace(output, public_recovery)
    except AnalysisError as exc:
        if not _lexists(output):
            return recoveries
        raise AnalysisError(
            f"Could not quarantine the current plan target {output}; owned plan "
            f"recovery retained at {recoveries[0]}: {exc}"
        ) from exc
    recoveries.append(public_recovery)
    return recoveries


def _publish_without_replacement(
    staged_plan: Path,
    staged_review: Path,
    output: Path,
    review_dir: Path,
) -> None:
    """Install both names atomically, rolling back only our published plan."""
    with staged_plan.open("rb") as published_handle:
        plan_published = False
        try:
            rename_noreplace(staged_plan, output)
            plan_published = True
            rename_noreplace(staged_review, review_dir)
        except AnalysisError as exc:
            rollback_error: OSError | AnalysisError | None = None
            recoveries: list[Path] = []
            if plan_published:
                try:
                    recoveries = _rollback_published_plan(output, published_handle)
                except (OSError, AnalysisError) as cleanup_exc:
                    rollback_error = cleanup_exc
            if rollback_error is not None:
                raise AnalysisError(
                    f"{exc}; could not roll back the just-published plan: {rollback_error}"
                ) from exc
            if recoveries:
                raise AnalysisError(
                    f"{exc}; recovery artifacts retained: "
                    + ", ".join(str(path) for path in recoveries)
                ) from exc
            raise


def _rollback_force_publication(
    backups: dict[Path, Path],
    targets: Sequence[Path],
    affected_targets: set[Path],
) -> tuple[list[Path], list[Path], list[str]]:
    """Preserve public artifacts, then restore backups without replacement."""
    recoveries: list[Path] = []
    errors: list[str] = []
    for target in reversed(targets):
        if target not in affected_targets:
            continue
        if not _lexists(target):
            continue
        recovery = _recovery_path(target, "public")
        try:
            rename_noreplace(target, recovery)
            recoveries.append(recovery)
        except AnalysisError as exc:
            errors.append(f"could not quarantine {target}: {exc}")

    for target in reversed(targets):
        backup = backups.get(target)
        if backup is None or not _lexists(backup):
            continue
        try:
            rename_noreplace(backup, target)
        except AnalysisError as exc:
            errors.append(f"could not restore {backup} to {target}: {exc}")

    retained_backups = [backup for backup in backups.values() if _lexists(backup)]
    return recoveries, retained_backups, errors


def _preflight_noreplace_parents(parents: Iterable[Path]) -> list[Path]:
    """Exercise file and directory no-replace support before public mutation."""
    retained: list[Path] = []
    unique_parents = sorted({Path(parent).resolve() for parent in parents}, key=str)
    for parent in unique_parents:
        token = uuid.uuid4().hex
        file_source = parent / f".analyze-cuts.preflight-file-source-{token}"
        file_destination = parent / f".analyze-cuts.preflight-file-destination-{token}"
        directory_source = parent / f".analyze-cuts.preflight-dir-source-{token}"
        directory_destination = parent / f".analyze-cuts.preflight-dir-destination-{token}"
        try:
            descriptor = os.open(
                file_source, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
            os.close(descriptor)
            retained.append(file_source)
            directory_source.mkdir()
            retained.append(directory_source)
            rename_noreplace(file_source, file_destination)
            retained[-2] = file_destination
            rename_noreplace(directory_source, directory_destination)
            retained[-1] = directory_destination
        except (OSError, AnalysisError) as exc:
            existing = [path for path in retained if _lexists(path)]
            for artifact in existing:
                _report_retained(artifact, "no-replace preflight")
            detail = ", ".join(str(path) for path in existing) or "none"
            raise AnalysisError(
                f"Atomic no-replace capability preflight failed before public mutation: "
                f"{exc}; preflight artifacts retained: {detail}"
            ) from exc
    return retained


def _publish_artifacts(
    staged_plan: Path,
    staged_review: Path,
    output: Path,
    review_dir: Path,
    force: bool,
) -> None:
    """Publish both artifacts with rollback if either replacement fails."""
    preflight_artifacts = _preflight_noreplace_parents(
        (output.parent, review_dir.parent)
    )
    for artifact in preflight_artifacts:
        _report_retained(artifact, "no-replace preflight")
    if not force:
        _publish_without_replacement(staged_plan, staged_review, output, review_dir)
        return
    token = uuid.uuid4().hex
    targets = (review_dir, output)
    backups: dict[Path, Path] = {}
    affected_targets: set[Path] = set()
    try:
        for target in targets:
            if _lexists(target):
                backup = target.parent / f".{target.name}.backup-{token}"
                rename_noreplace(target, backup)
                backups[target] = backup
                affected_targets.add(target)
        affected_targets.add(review_dir)
        rename_noreplace(staged_review, review_dir)
        affected_targets.add(output)
        rename_noreplace(staged_plan, output)
    except (OSError, AnalysisError) as exc:
        recoveries, retained_backups, rollback_errors = _rollback_force_publication(
            backups, targets, affected_targets
        )
        details: list[str] = []
        if recoveries:
            details.append(
                "recovery artifacts retained: "
                + ", ".join(str(path) for path in recoveries)
            )
        if retained_backups:
            details.append(
                "backups retained: "
                + ", ".join(str(path) for path in retained_backups)
            )
        if rollback_errors:
            details.append("rollback issues: " + "; ".join(rollback_errors))
        suffix = "; " + "; ".join(details) if details else ""
        raise AnalysisError(f"Artifact publication failed: {exc}{suffix}") from exc
    for backup in backups.values():
        _report_retained(backup, "successful-force backup")


def _source_signature(source: Path) -> tuple[int, int, int, int]:
    stat = source.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def run_analysis(
    source: Path,
    output: Path,
    review_dir: Path,
    mode: str,
    force: bool,
) -> dict:
    source = Path(source)
    output = Path(output)
    review_dir = Path(review_dir)
    try:
        return _run_analysis(source, output, review_dir, mode, force)
    except AnalysisError:
        raise
    except OSError as exc:
        raise AnalysisError(f"Filesystem operation failed: {exc}") from exc


def _run_analysis(
    source: Path,
    output: Path,
    review_dir: Path,
    mode: str,
    force: bool,
) -> dict:
    if not source.is_file():
        raise AnalysisError(f"Input video does not exist or is not a file: {source}")
    _reject_unsafe_paths(source, output, review_dir)
    _require_tools()
    if mode != "fine":
        raise AnalysisError("Only --mode fine is currently supported")
    for label, parent in (("output", output.parent), ("review", review_dir.parent)):
        if not parent.is_dir():
            raise AnalysisError(f"The {label} parent directory does not exist: {parent}")
    original_source = _source_signature(source)

    with _artifact_locks(output, review_dir):
        _reject_unsafe_paths(source, output, review_dir)
        existing = [path for path in (output, review_dir) if _lexists(path)]
        if existing and not force:
            raise AnalysisError(
                "Refusing to overwrite existing plan/review artifacts: "
                + ", ".join(str(path) for path in existing)
                + ". Re-run with --force to replace them."
            )
        if _lexists(output) and output.is_dir() and not output.is_symlink():
            raise AnalysisError(f"Output plan path is a directory: {output}")
        if _lexists(review_dir) and not review_dir.is_dir():
            raise AnalysisError(f"Review path exists but is not a directory: {review_dir}")

        plan_stage_root: Path | None = None
        review_stage_root: Path | None = None
        try:
            plan_stage_root = Path(
                tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent)
            )
            review_stage_root = Path(
                tempfile.mkdtemp(prefix=f".{review_dir.name}.stage-", dir=review_dir.parent)
            )
            staged_plan = plan_stage_root / output.name
            staged_review = review_stage_root / review_dir.name
            info, keyframes = probe_video(source)
            scene_events = parse_metadata(
                iter_command_stderr(build_scene_command(source), "scene analysis"),
                "lavfi.scene_score",
                "scene",
                info.fps,
                start_time=info.start_time,
            )
            ydif_lines = iter_command_stderr(build_ydif_command(source), "YDIF analysis")
            all_ydif = iter_metadata(
                ydif_lines,
                "lavfi.signalstats.YDIF",
                "ydif",
                info.fps,
                start_time=info.start_time,
            )
            ydif_events = list(
                iter_ydif_peak_events(
                    all_ydif,
                    info.frame_count,
                    minimum=8,
                    ratio=2.5,
                )
            )
            candidates = merge_evidence(
                keyframes + scene_events + ydif_events, 3, info.fps
            )
            candidates = filter_edge_candidates(candidates, info.frame_count)
            plan = make_plan(str(source), info.fps, info.frame_count, candidates)
            plan["duration"] = info.duration

            render_review(
                source,
                candidates,
                staged_review,
                width=info.width,
                height=info.height,
                fps=info.fps,
            )
            _atomic_json(staged_plan, plan)
            _validate_staged_artifacts(staged_plan, staged_review, plan)
            if _source_signature(source) != original_source:
                raise AnalysisError("Input video changed during analysis; artifacts were not published")
            _reject_unsafe_paths(source, output, review_dir)
            _publish_artifacts(
                staged_plan,
                staged_review,
                output,
                review_dir,
                force=force,
            )
            return plan
        finally:
            if plan_stage_root is not None:
                _report_retained(plan_stage_root, "plan staging")
            if review_stage_root is not None:
                _report_retained(review_stage_root, "review staging")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect fine jump-cut candidates and create visual-review sheets."
    )
    parser.add_argument("input", type=Path, help="source video")
    parser.add_argument("--output", type=Path, required=True, help="cut-plan JSON path")
    parser.add_argument("--review-dir", type=Path, required=True, help="PNG review directory")
    parser.add_argument("--mode", choices=("fine",), default="fine")
    parser.add_argument("--force", action="store_true", help="replace existing output artifacts")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = run_analysis(args.input, args.output, args.review_dir, args.mode, args.force)
    except AnalysisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            "note: Retained hidden artifacts are deliberate safety copies; "
            "manually inspect them before deleting.",
            file=sys.stderr,
        )
        return 2
    print(
        f"Wrote {len(plan['candidates'])} candidates to {args.output}; "
        f"review sheets: {args.review_dir}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
