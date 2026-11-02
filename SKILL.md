---
name: video-hardsub-fcp
description: Removes burned-in subtitles, detects fine same-camera jump cuts, and prepares editable Final Cut Pro FCPXML timelines. Use when a user says “去字幕切段”, asks to batch-process hard-subtitled videos, reports missed micro jump cuts, or wants cleaned media ready for Final Cut Pro.
---

# Video Hardsub → FCP

## Subtitle quality and brief exceptions (user preference, 2026-11-02)

- **Prioritize Watermark Remover for subtitles with a backing bar**: select the complete text, shadow and bar together. Do not default to Text Remover followed by filling the leftover bar. Text Remover is an alternative only when a representative sample demonstrates complete, natural removal; do not automatically switch to Object Remover.
- For a subtitle in an unusual position that appears for only a few frames or a very short interval, leave it intact when repair would damage the picture or enlarge the repair region. The user will crop or cut that portion later. Record its exact start/end times or frame numbers in the manifest/report as intentionally retained; do not invent a fixed duration threshold.
- Prioritize sustained main captions. Inspect the entire frame, but exclude the documented brief exceptions from the removal-region union. Do not expand a whole-video mask or submit another job solely to remove a brief outlier.
- Keep the full video, frames and audio. The user's statement that they will crop later does **not** authorize the agent to crop, trim, delete frames or cut the timeline. Deliver 1080×1920 as usual; only perform cuts when separately requested.
- Validate difficult short samples before processing the full video. Inspect the repaired region in motion for smearing, stretched textures, repeated patterns and damaged hands/food/utensils. No-text scans and outside-mask PSNR do not establish repair quality. List intentional residuals separately from failures and never describe a result containing them as completely text-free.

## Respect the requested scope

When the user asks only for bottom subtitles with Watermark Remover, process only the bottom text and its backing bar using that tool. Preserve other titles/text, full duration, audio and delivery geometry. Do not expand into Object Remover, photo repair, temporal reconstruction, extra samples or other cleanup unless the user requests it. Scope-specific user instructions override the general quality workflow below.

## Standing authorization for this user

The user's latest instruction on 2026-11-02 is: “如果积分消耗一次超过100，先报警给我，让我点通过，不到100的没关系”. This supersedes the earlier blanket automatic-submission authorization and the earlier quality-rework instruction not to worry about credits.

Before each credit-consuming submission, prepare the exact file, selected region, current displayed cost and sufficient balance. **If one submission costs more than 100 credits, show that concrete job and its cost to the user and wait for explicit approval before clicking Submit/Remove.** A progress update, silence, elapsed time or an earlier blanket authorization is not approval. For a submission below 100 credits, use the existing standing authorization after these checks. The user did not specify the exactly-100 boundary; if it arises, obtain approval rather than inventing authorization for that case. Do not split an otherwise single job into smaller charges to bypass approval. Record the displayed cost, threshold decision and any explicit approval in the manifest. Never purchase or top up credits. Submit each approved entry once; recover a stalled job rather than paying for a duplicate. Apply the same rule on MacBook and Mini, in Codex and Claude Code.

Treat “去字幕切段” as sufficient to start this workflow. Do not ask the user to restate the goal. Apply the per-submission credit rule above: prepare jobs autonomously, but obtain explicit approval before a charge over 100 credits. Also stop for insufficient balance or genuinely missing input/capability.

Set `SKILL_DIR` to the directory containing this `SKILL.md`; invoke every helper by its skill-relative path under `"$SKILL_DIR/scripts"`.

## Required checklist

Copy this checklist into the working notes and keep it current:

Video workflow:
- [ ] Inventory sources; preserve originals
- [ ] Record each source's `width`x`height`; carry it as the required output resolution
- [ ] Inspect contact sheets; choose per-file subtitle regions
- [ ] Record one batch manifest, regions, full-frame risk, HitPaw cost/balance and standing authorization
- [ ] Submit each HitPaw job once; recover completed jobs from logs
- [ ] Restore source geometry/audio and fully decode-verify
- [ ] Conform the cleaned media to the 1080x1920 delivery format and verify it
- [ ] Generate high-recall cut candidates and frame-pair review sheets
- [ ] Classify every candidate: approve true discontinuities; reject persistent motion artifacts
- [ ] Build and validate one FCPXML timeline per cleaned video
- [ ] Deliver media, FCPXML, report, and a small manifest

## Resolution is a deliverable

Two resolutions matter, and they are checked separately.

The restored media — the subtitle-free picture before delivery conforming — must equal the source resolution. Record `width`x`height` from the first `ffprobe` of each source and treat it as a required property, not something to read off the finished file. Upstream subtitle removal silently downscales — an export preset that says "1080" turns a 720x1280 source into 608x1080 — and a downscale is unrecoverable once it has happened. Never hide it by upscaling the restored media back to source dimensions; fix it at the export, or report the loss.

The delivered media is always vertical 1080x1920, whatever the source resolution. `conform-vertical.sh` produces it as the last media step: it scales the picture to fit, centres it on black when the aspect is not 9:16, never crops or stretches, copies audio, and keeps every frame. This upscale is the requested delivery format, not a repair, so it happens only after the restored media has been verified against the source — otherwise a downscale would disappear inside it.

## Entry path: subtitles already removed

If the user supplies media whose subtitles were removed elsewhere ("去字幕已经好了", "只要切段", a file from another tool), skip the HitPaw sections and start at Fine jump-cut decisions — but first:

1. Ask for, or locate, the media **as it existed before subtitle removal**. Set it as `$SOURCE`. The file the user just handed you is `$CLEANED_MEDIA`, never `$SOURCE`.
2. Run the verification below with that `$SOURCE`. `verify-video.sh` fails `source_identity` when `$SOURCE` and `$CLEANED_MEDIA` are byte-identical, because a file compared against itself proves nothing about geometry.
3. If the pre-removal original genuinely cannot be produced, run verification without `--source` and state plainly in the report: resolution, frame rate, and duration could not be verified against the original, so any upstream change to them is unknown. Do not describe the delivery as matching the source.

A resolution mismatch found here is reported, not repaired: tell the user the source and supplied dimensions, and that re-exporting from the subtitle-removal tool at source resolution is the only real fix. Then still conform and deliver 1080x1920 as below, and say in the report that it was upscaled from the smaller supplied file.

## Safety and inventory

- Keep sources immutable. Work in a dedicated ASCII-only path and make a separate working copy for each source.
- Accept only MP4/MOV inputs, with one independent task directory for each working copy. Build an exact inventory before processing; never silently skip or merge files.
- Run inspection before choosing any subtitle region:

```bash
"$SKILL_DIR/scripts/inspect-video.sh" "$WORKING_COPY" "$JOB_DIR/inspect"
```

- Inspect both contact sheets for every file. Group files only when their visible layouts support the same region; store the decision per file.
- Measure caption position by scanning every row of the frame, from 0 to the full height, not by reading it off a contact sheet. Sheets are scaled down far enough to hide a low-contrast cue, and the cue that sits away from the others is the one they hide. Classify brief unusual-position cues as intentional exceptions when appropriate. Union only captions selected for removal, with a modest margin around text, shadow and backing. Enlarging a repair region can damage image content.
- Use a tight band for a fixed caption. For moving sustained captions, prefer shot-specific regions; use full-frame repair only if necessary and validated on difficult samples, noting that hands, food, tools, packaging or UI may be damaged. Preserve the raw HitPaw output separately.
- After removal, run the same full-height scan on the result. Review every surviving cue: documented brief intentional exceptions are allowed, while unintended residuals require correction. A handful of thumbnails does not establish coverage or visual quality.

## HitPaw submission boundary

Before any HitPaw submission, read [references/hitpaw-workflow.md](references/hitpaw-workflow.md) completely.

Create one batch manifest recording the exact files, each region, every full-frame quality risk, displayed HitPaw cost and sufficient balance. Apply the per-submission credit rule above; a charge over 100 credits requires explicit approval before submission. Report material quality risks in progress updates; never purchase or top up credits.

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

After trimming, use `$TRIMMED_CLEANED_MEDIA` as `$CLEANED_MEDIA` for the conform step below. Never remux full-length source audio onto trimmed video, and never verify a trimmed output against the full source. `verify-video.sh` checks display geometry, duration tolerance, full decodability, 1 fps sheet generation, whether audio is present when the comparison source has audio, and `source_identity` — that `$SOURCE` is not byte-identical to `$CLEANED_MEDIA`, which would make every source comparison vacuous. It does not prove audio identity or matching FPS; the remux and explicit FPS comparisons above are required. Visually compare the raw HitPaw result with the source wherever food, hands, tools, packaging, or UI may have been damaged. A script pass does not replace this review.

## Conform to 1080x1920

Only after the verification above passes (or its failure has been reported), conform the cleaned media and verify the result. `--delivery-size` swaps the source geometry comparison for a check that the file is exactly 1080x1920; identity, audio, duration, and decode checks still run against the source:

```bash
"$SKILL_DIR/scripts/conform-vertical.sh" "$CLEANED_MEDIA" "$DELIVERY_MEDIA"
"$SKILL_DIR/scripts/verify-video.sh" "$DELIVERY_MEDIA" "$JOB_DIR/verify-delivery" --source "$SOURCE" --delivery-size 1080x1920
```

Pass the trimmed source reference as `--source` when the media was trimmed. `$DELIVERY_MEDIA` keeps the extension of `$CLEANED_MEDIA`. It is the media used for cut analysis, FCPXML, and packaging, and `verify-delivery/report.json` is the verification that ships with it.

## Fine jump-cut decisions

Generate high-recall candidates in fine mode. The analyzer combines scene scores, keyframes, and isolated YDIF peaks, then clusters nearby evidence into one candidate; its scores rank candidates but never approve cuts.

```bash
python3 "$SKILL_DIR/scripts/analyze-cuts.py" "$DELIVERY_MEDIA" --mode fine --output "$JOB_DIR/cut-plan.json" --review-dir "$JOB_DIR/cut-review"
```

Review every candidate. Each sheet row holds one candidate as four consecutive frames (offsets -2, -1, 0, +1), so continuous motion carries across the row while a real cut breaks it; judge from the whole row, not from one adjacent pair. Rows are clamped and repeat an end frame when a candidate sits near the media boundary. Put every candidate exactly once in `selected_frames` or `rejected_candidates`. Approve only a visible temporal discontinuity. Reject continuous steam, ingredient motion, fast action, camera shake, or other persistent motion unless the row shows a true discontinuity. Allowed rejection reasons are `persistent-motion`, `steam`, `ingredient-motion`, `camera-shake`, and `no-visible-discontinuity`.

A candidate anchors a cluster of nearby evidence on the frame whose own evidence is strongest, which is the first frame of the new shot. If a row nevertheless shows the cut between the -2 and -1 tiles, the anchor is one frame late: re-run the analyzer from the current skill rather than hand-editing the plan, and never add fields to a candidate, because the packager accepts only the public audit fields. Keep the candidate's own frame number: `build-fcpxml.py` requires `selected_frames` to be drawn from the candidate list. A static-camera jump cut can have a low YDIF (a salt pile vanishing between two frames scored 4); judge it from the row, not the score.

## FCPXML and delivery

Build FCPXML only after all candidate decisions are complete:

```bash
python3 "$SKILL_DIR/scripts/build-fcpxml.py" "$DELIVERY_MEDIA" "$JOB_DIR/cut-plan.json" --output "$JOB_DIR/project.fcpxml"
```

Require every source frame exactly once, in order, with contiguous video and matching audio order. Produce one 1080x1920 delivery media file and one validated FCPXML timeline per source.

The project (sequence) format is always vertical 1080x1920; do not change it to match the media. The asset keeps a separate format with the media's real dimensions, so media that somehow is not 1080x1920 is still fitted rather than mislabelled. Neither the conformed media nor the fixed project format replaces the restored-media resolution check: a downscale found there is still reported, even though the delivery fills the 1080x1920 timeline.

Maintain the full operational per-file manifest described in the HitPaw reference and resume only unfinished entries. Do not pass that operational manifest to the packager. Convert completed entries to the separate minimal package manifest schema in the reference, excluding working state and service data. A batch archive is optional:

```bash
"$SKILL_DIR/scripts/package-deliverables.sh" "$BATCH_MANIFEST" "$DELIVERY_ZIP"
```

The archive packager creates a self-contained directory per entry: its archive-only FCPXML uses a same-directory relative URI for `cleaned.mp4`/`cleaned.mov`, and its cut plan keeps frame decisions while removing local working paths. Extract the whole ZIP before importing the FCPXML; keep each `entry-NNN` XML and cleaned media together.

Never put source videos, generated user media, raw logs, signed URLs, HitPaw account or credit data, or credentials in Git or a release. Hidden recovery artifacts produced by the safety-conscious atomic scripts are deliberate; inspect their paths and contents before any manual deletion.
