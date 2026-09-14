---
name: video-hardsub-fcp
description: Removes burned-in subtitles, detects fine same-camera jump cuts, and prepares editable Final Cut Pro FCPXML timelines. Use when a user says “去字幕切段”, asks to batch-process hard-subtitled videos, reports missed micro jump cuts, or wants cleaned media ready for Final Cut Pro.
---

# Video Hardsub → FCP

Treat “去字幕切段” as sufficient to start this workflow. Do not ask the user to restate the goal. Pause only at the single paid-submission confirmation or when a required capability/input is unavailable.

Set `SKILL_DIR` to the directory containing this `SKILL.md`; invoke every helper by its skill-relative path under `"$SKILL_DIR/scripts"`.

## Required checklist

Copy this checklist into the working notes and keep it current:

Video workflow:
- [ ] Inventory sources; preserve originals
- [ ] Record each source's `width`x`height`; carry it as the required output resolution
- [ ] Inspect contact sheets; choose per-file subtitle regions
- [ ] Confirm one batch manifest, regions, full-frame risk, and HitPaw credits
- [ ] Submit each HitPaw job once; recover completed jobs from logs
- [ ] Restore source geometry/audio and fully decode-verify
- [ ] Generate high-recall cut candidates and frame-pair review sheets
- [ ] Classify every candidate: approve true discontinuities; reject persistent motion artifacts
- [ ] Build and validate one FCPXML timeline per cleaned video
- [ ] Deliver media, FCPXML, report, and a small manifest

## Resolution is a deliverable

The output resolution must equal the source resolution. Record `width`x`height` from the first `ffprobe` of each source and treat it as a required output property, not something to read off the finished file. Upstream subtitle removal silently downscales — an export preset that says "1080" turns a 720x1080 source into 608x1080 — and a downscale is unrecoverable once it has happened.

Never satisfy this by upscaling a downscaled result back to source dimensions. That passes `source_display_geometry` while the detail is already gone. Fix it at the export, or report the loss.

## Entry path: subtitles already removed

If the user supplies media whose subtitles were removed elsewhere ("去字幕已经好了", "只要切段", a file from another tool), skip the HitPaw sections and start at Fine jump-cut decisions — but first:

1. Ask for, or locate, the media **as it existed before subtitle removal**. Set it as `$SOURCE`. The file the user just handed you is `$CLEANED_MEDIA`, never `$SOURCE`.
2. Run the verification below with that `$SOURCE`. `verify-video.sh` fails `source_identity` when `$SOURCE` and `$CLEANED_MEDIA` are byte-identical, because a file compared against itself proves nothing about geometry.
3. If the pre-removal original genuinely cannot be produced, run verification without `--source` and state plainly in the report: resolution, frame rate, and duration could not be verified against the original, so any upstream change to them is unknown. Do not describe the delivery as matching the source.

A resolution mismatch found here is reported, not repaired: tell the user the source and delivered dimensions, and that re-exporting from the subtitle-removal tool at source resolution is the only real fix.

## Safety and inventory

- Keep sources immutable. Work in a dedicated ASCII-only path and make a separate working copy for each source.
- Accept only MP4/MOV inputs, with one independent task directory for each working copy. Build an exact inventory before processing; never silently skip or merge files.
- Run inspection before choosing any subtitle region:

```bash
"$SKILL_DIR/scripts/inspect-video.sh" "$WORKING_COPY" "$JOB_DIR/inspect"
```

- Inspect both contact sheets for every file. Group files only when their visible layouts support the same region; store the decision per file.
- Measure caption position by scanning every row of the frame, from 0 to the full height, not by reading it off a contact sheet. Sheets are scaled down far enough to hide a low-contrast cue, and the cue that sits away from the others is the one they hide. Take the union of every row band the scan reports across the whole clip, then pad it. Padding costs nothing because pixels outside the region come from the source; missing one cue costs a second paid job.
- Use a tight band for a fixed caption. A moving caption requires full-frame removal and an explicit warning that hands, food, tools, packaging, or UI may be damaged. Preserve the raw HitPaw output separately.
- After removal, run the same full-height scan on the result. An empty result on every frame is what proves the region covered every cue; a handful of thumbnails does not.

## HitPaw submission boundary

Before any HitPaw submission, read [references/hitpaw-workflow.md](references/hitpaw-workflow.md) completely.

Create one batch manifest and request one explicit confirmation covering the exact files, each region, every full-frame quality risk, and the paid HitPaw credits. Never silently buy or consume credits.

Check for the current host's desktop-control capability before interacting with HitPaw. If it is unavailable, stop with this precise handoff: the manifest and working copies are ready, but HitPaw submission must be performed in a host with desktop control; no job was submitted and no success is claimed.

Submit each approved file exactly once. A slow render, stalled download, timeout, or missing UI update is not permission to resubmit. Recover the completed job from local logs:

```bash
"$SKILL_DIR/scripts/fetch-hitpaw-result.sh" --wait "$JOB_DIR/hitpaw-raw.mp4"
"$SKILL_DIR/scripts/fetch-hitpaw-result.sh" "$JOB_DIR/hitpaw-raw.mp4"
```

Use `--wait` while an already-submitted job is running; use the second form to fetch an already-completed result. Never copy signed result URLs into notes, manifests, reports, Git, or releases.

## Restore and verify

Restore the source display geometry while retaining the raw HitPaw output. Remux audio from the preserved source into that restored picture; this intentionally discards any HitPaw audio:

```bash
ffmpeg -nostdin -i "$RESTORED_VIDEO" -i "$SOURCE" -map 0:v:0 -map '1:a?' -c:v copy -c:a copy -map_metadata 1 "$CLEANED_MEDIA"
source_fps=$(ffprobe -v error -select_streams v:0 -show_entries stream=avg_frame_rate -of default=nw=1:nk=1 "$SOURCE")
cleaned_fps=$(ffprobe -v error -select_streams v:0 -show_entries stream=avg_frame_rate -of default=nw=1:nk=1 "$CLEANED_MEDIA")
[ "$source_fps" = "$cleaned_fps" ] || { printf 'FPS mismatch: source=%s cleaned=%s\n' "$source_fps" "$cleaned_fps" >&2; exit 1; }
"$SKILL_DIR/scripts/verify-video.sh" "$CLEANED_MEDIA" "$JOB_DIR/verify" --source "$SOURCE"
```

Trim only an end card that the user explicitly confirmed. Record one approved interval as `TRIM_START` and `TRIM_END`, and apply those exact timestamps to both the restored picture and preserved source audio. Create a correspondingly trimmed preserved-source reference and verify against it—not against the full source:

```bash
ffmpeg -nostdin \
  -ss "$TRIM_START" -to "$TRIM_END" -i "$RESTORED_VIDEO" \
  -ss "$TRIM_START" -to "$TRIM_END" -i "$SOURCE" \
  -map 0:v:0 -map '1:a?' -c:v copy -c:a copy -map_metadata 1 -shortest "$TRIMMED_CLEANED_MEDIA"
ffmpeg -nostdin -ss "$TRIM_START" -to "$TRIM_END" -i "$SOURCE" \
  -map 0:v:0 -map '0:a?' -c copy -map_metadata 0 -shortest "$TRIMMED_SOURCE_REFERENCE"
trimmed_source_fps=$(ffprobe -v error -select_streams v:0 -show_entries stream=avg_frame_rate -of default=nw=1:nk=1 "$TRIMMED_SOURCE_REFERENCE")
trimmed_cleaned_fps=$(ffprobe -v error -select_streams v:0 -show_entries stream=avg_frame_rate -of default=nw=1:nk=1 "$TRIMMED_CLEANED_MEDIA")
[ "$trimmed_source_fps" = "$trimmed_cleaned_fps" ] || { printf 'FPS mismatch after trim: source=%s cleaned=%s\n' "$trimmed_source_fps" "$trimmed_cleaned_fps" >&2; exit 1; }
"$SKILL_DIR/scripts/verify-video.sh" "$TRIMMED_CLEANED_MEDIA" "$JOB_DIR/verify-trimmed" --source "$TRIMMED_SOURCE_REFERENCE"
```

After trimming, use `$TRIMMED_CLEANED_MEDIA` as the cleaned deliverable. Never remux full-length source audio onto trimmed video, and never verify a trimmed output against the full source. `verify-video.sh` checks display geometry, duration tolerance, full decodability, 1 fps sheet generation, whether audio is present when the comparison source has audio, and `source_identity` — that `$SOURCE` is not byte-identical to `$CLEANED_MEDIA`, which would make every source comparison vacuous. It does not prove audio identity or matching FPS; the remux and explicit FPS comparisons above are required. Visually compare the raw HitPaw result with the source wherever food, hands, tools, packaging, or UI may have been damaged. A script pass does not replace this review.

## Fine jump-cut decisions

Generate high-recall candidates in fine mode. The analyzer combines scene scores, keyframes, and isolated YDIF peaks, then clusters nearby evidence into one candidate; its scores rank candidates but never approve cuts.

```bash
python3 "$SKILL_DIR/scripts/analyze-cuts.py" "$CLEANED_MEDIA" --mode fine --output "$JOB_DIR/cut-plan.json" --review-dir "$JOB_DIR/cut-review"
```

Review every candidate. Each sheet row holds one candidate as four consecutive frames (offsets -2, -1, 0, +1), so continuous motion carries across the row while a real cut breaks it; judge from the whole row, not from one adjacent pair. Rows are clamped and repeat an end frame when a candidate sits near the media boundary. Put every candidate exactly once in `selected_frames` or `rejected_candidates`. Approve only a visible temporal discontinuity. Reject continuous steam, ingredient motion, fast action, camera shake, or other persistent motion unless the row shows a true discontinuity. Allowed rejection reasons are `persistent-motion`, `steam`, `ingredient-motion`, `camera-shake`, and `no-visible-discontinuity`.

A candidate anchors a cluster of nearby evidence, so its frame can sit one frame after the visible cut when the cluster carries no keyframe evidence. Judge the cluster, and keep the candidate's own frame number: `build-fcpxml.py` requires `selected_frames` to be drawn from the candidate list.

## FCPXML and delivery

Build FCPXML only after all candidate decisions are complete:

```bash
python3 "$SKILL_DIR/scripts/build-fcpxml.py" "$CLEANED_MEDIA" "$JOB_DIR/cut-plan.json" --output "$JOB_DIR/project.fcpxml"
```

Require every source frame exactly once, in order, with contiguous video and matching audio order. Produce one cleaned media file and one validated FCPXML timeline per source.

The project (sequence) format is always vertical 1080x1920, whatever the media resolution; do not change it to match the media. The asset keeps a separate format with the media's real dimensions so Final Cut Pro fits the picture into the project instead of mislabelling its size. The fixed project format never replaces the resolution check above: a downscaled cleaned file still has to be reported, even though it fills the 1080x1920 timeline.

Maintain the full operational per-file manifest described in the HitPaw reference and resume only unfinished entries. Do not pass that operational manifest to the packager. Convert completed entries to the separate minimal package manifest schema in the reference, excluding working state and service data. A batch archive is optional:

```bash
"$SKILL_DIR/scripts/package-deliverables.sh" "$BATCH_MANIFEST" "$DELIVERY_ZIP"
```

The archive packager creates a self-contained directory per entry: its archive-only FCPXML uses a same-directory relative URI for `cleaned.mp4`/`cleaned.mov`, and its cut plan keeps frame decisions while removing local working paths. Extract the whole ZIP before importing the FCPXML; keep each `entry-NNN` XML and cleaned media together.

Never put source videos, generated user media, raw logs, signed URLs, HitPaw account or credit data, or credentials in Git or a release. Hidden recovery artifacts produced by the safety-conscious atomic scripts are deliberate; inspect their paths and contents before any manual deletion.
