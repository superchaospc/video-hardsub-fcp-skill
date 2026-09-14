# HitPaw workflow

Read this sequence completely before submitting any HitPaw job. HitPaw is a paid GUI prerequisite; use only the current host's desktop-control capability. If that capability is absent, prepare the manifest and working copies, then hand off without claiming submission or success.

## Safe sequence

1. Create a dedicated ASCII-only work root. Copy each MP4/MOV into its own folder; never edit or upload the source in place.
2. Run `"$SKILL_DIR/scripts/inspect-video.sh" "$WORKING_COPY" "$JOB_DIR/inspect"` and inspect the metadata plus full-frame and subtitle-band contact sheets.
3. Select a per-file region from a full-height row scan, never from the contact sheets: at thumbnail scale a low-contrast cue is invisible, and an outlying cue is exactly the one that hides. Union every row band the scan reports across the clip, then pad it. Use a tight band for fixed captions. Use full frame for moving captions only after warning that generative repair can damage food, hands, tools, packaging, or UI.
4. Set HitPaw's export resolution to the source's exact `width`x`height` before submitting, and record it as `export_resolution` in the manifest entry. HitPaw's presets are named by one dimension ("1080"), so a 720x1280 source silently exports as 608x1080 unless the resolution is set explicitly. Verify the setting in the export dialog rather than assuming the default preserves it — this is the only step that actually preserves resolution; every later check can only detect the loss.
5. Confirm desktop-control capability. Without it, stop before submission and state that no job was submitted.
6. Read the AI-credit balance in HitPaw's header first and check the batch against it, so the confirmation you ask for is one you can actually honour. The Remove button prints the per-job price beside the balance. If the balance will not cover every entry, say which entries it covers and stop there. Then prepare one batch manifest and obtain one confirmation for the exact files, regions, full-frame risks, and total paid-credit use. Never silently purchase or consume credits, and never purchase them at all.
7. If HitPaw rejects the working media, create a compatibility MP4 inside the job folder while retaining the working copy:

   ```bash
   ffmpeg -nostdin -i "$WORKING_COPY" -map 0:v:0 -map '0:a?' -c:v libx264 -pix_fmt yuv420p -c:a aac -movflags +faststart "$JOB_DIR/hitpaw-input.mp4"
   ```

8. Process paid jobs sequentially per manifest entry: submit one approved file once, immediately record its `submission_status`, then wait for or recover that file's result before submitting the next entry. This prevents concurrent or out-of-order completions from being assigned to the wrong source. Do not submit the same entry again after a slow render, stalled download, timeout, restart, or ambiguous UI state.
9. Wait for or recover the existing completion from HitPaw's local logs. Edimakor writes the finished URL under `removeWatermark result url:` or, when the app's own download failed and the card reads "Failed to download", under `FileReady url:` instead; the fetch script matches both and takes whichever came last. A "Failed to download" card means the render finished and only the transfer failed, so recover it rather than resubmitting. Do not paste raw logs or signed URLs into the manifest. If jobs were already submitted concurrently, set `HITPAW_LOG_FILE` to the job-specific log for each recovery and stop for human resolution if the source-to-result mapping is not unambiguous:

   ```bash
   "$SKILL_DIR/scripts/fetch-hitpaw-result.sh" --wait "$JOB_DIR/hitpaw-raw.mp4"
   "$SKILL_DIR/scripts/fetch-hitpaw-result.sh" "$JOB_DIR/hitpaw-raw.mp4"
   ```

   The first command watches for a newer result from an already-submitted job. The second fetches an existing completed result. A download failure is not permission to resubmit.
10. Preserve `hitpaw-raw.mp4`. Compare its `width`x`height` against the source's before anything else. If they differ, step 4's export resolution did not take effect: re-export from HitPaw at the source resolution rather than continuing. Only when HitPaw cannot be made to preserve the dimensions may you rescale `hitpaw-raw.mp4` to `$RESTORED_VIDEO` — and then record the raw result's dimensions in the manifest and tell the user the delivery was upscaled from a smaller export and has lost detail. Rescaling makes `source_display_geometry` pass; it does not restore resolution, so never do it silently. Then remux the original source audio into the cleaned picture: `ffmpeg -nostdin -i "$RESTORED_VIDEO" -i "$SOURCE" -map 0:v:0 -map '1:a?' -c:v copy -c:a copy -map_metadata 1 "$CLEANED_MEDIA"`.
11. Compare source and cleaned `avg_frame_rate` explicitly with `ffprobe`, then run `"$SKILL_DIR/scripts/verify-video.sh" "$CLEANED_MEDIA" "$JOB_DIR/verify" --source "$SOURCE"`. The helper checks display geometry, duration tolerance, full decodability, 1 fps sheet generation, audio presence when the source has audio, and `source_identity` — that `$SOURCE` is not byte-identical to `$CLEANED_MEDIA`, since a file compared against itself proves nothing. It does not prove audio identity or FPS equality; the remux and separate FPS comparison are mandatory.
12. Trim only an end card the user explicitly confirmed. Record one approved `TRIM_START`/`TRIM_END` interval and apply it identically to the restored picture and preserved source audio. Use `-shortest` and quoted optional maps when creating `$TRIMMED_CLEANED_MEDIA`, then create `$TRIMMED_SOURCE_REFERENCE` from the same source interval:

    ```bash
    ffmpeg -nostdin \
      -ss "$TRIM_START" -to "$TRIM_END" -i "$RESTORED_VIDEO" \
      -ss "$TRIM_START" -to "$TRIM_END" -i "$SOURCE" \
      -map 0:v:0 -map '1:a?' -c:v copy -c:a copy -map_metadata 1 -shortest "$TRIMMED_CLEANED_MEDIA"
    ffmpeg -nostdin -ss "$TRIM_START" -to "$TRIM_END" -i "$SOURCE" \
      -map 0:v:0 -map '0:a?' -c copy -map_metadata 0 -shortest "$TRIMMED_SOURCE_REFERENCE"
    "$SKILL_DIR/scripts/verify-video.sh" "$TRIMMED_CLEANED_MEDIA" "$JOB_DIR/verify-trimmed" --source "$TRIMMED_SOURCE_REFERENCE"
    ```

    Compare the trimmed files' `avg_frame_rate` separately as in step 11, document the approved interval, and use `$TRIMMED_CLEANED_MEDIA` as the deliverable. Never remux full-length source audio after trimming and never verify trimmed output against the full source.
13. Conform the verified cleaned media to the 1080x1920 delivery format and verify that file: `"$SKILL_DIR/scripts/conform-vertical.sh" "$CLEANED_MEDIA" "$DELIVERY_MEDIA"`, then `"$SKILL_DIR/scripts/verify-video.sh" "$DELIVERY_MEDIA" "$JOB_DIR/verify-delivery" --source "$SOURCE" --delivery-size 1080x1920`. Conform only after steps 10–12, because the upscale would otherwise hide a downscale. `$DELIVERY_MEDIA` is the deliverable used for cuts, FCPXML, and packaging; its `verify-delivery/report.json` is the packaged verification.
14. Compare the raw HitPaw result against the source anywhere full-frame repair may have damaged food, hands, tools, packaging, or UI. Do not promote damaged output merely because decoding succeeds.

## Operational batch manifest

Store one record per file with these fields:

- `source`
- `source_resolution`
- `working_copy`
- `subtitle_region`
- `export_resolution`
- `full_frame_warning`
- `approval`
- `submission_status`
- `result_path`
- `verification_status`
- `cut_plan_path`
- `FCPXML_path`
- `package_status`

Update each record after every durable transition. On restart, inventory the manifest and resume only unfinished entries. A submitted entry remains submitted until its existing result is recovered or a human explicitly resolves the external job; never infer permission to create a duplicate paid job.

This internal operational manifest is not accepted by `package-deliverables.sh` and must not be delivered. It may contain local working paths and submission state, so keep it outside Git and releases.

## Package manifest

For packaging, convert only completed operational entries to a separate minimal local manifest with exactly this schema:

```json
{
  "version": 1,
  "entries": [
    {
      "source": "/absolute/source.mp4",
      "status": "complete",
      "deliverables": {
        "cleaned": "/absolute/job/cleaned.mp4",
        "fcpxml": "/absolute/job/project.fcpxml",
        "cuts": "/absolute/job/cut-plan.json",
        "verification": "/absolute/job/verify/report.json"
      }
    }
  ]
}
```

Exclude `working_copy`, region/approval state, submission status, result URLs, account/credit data, logs, credentials, and signed URLs. Include only `status: "complete"` entries and the four deliverables. Keep this local package-input manifest private because validation requires absolute source paths. `package-deliverables.sh` emits a sanitized archive manifest, rewrites the archive-only FCPXML to a same-directory relative `cleaned.mp4`/`cleaned.mov` URI, and emits a cut plan containing the useful frame decisions without local working paths. Extract the entire archive and keep each `entry-NNN` XML beside its cleaned media when importing it into Final Cut Pro.
