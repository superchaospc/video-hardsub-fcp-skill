# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.5.0] - 2026-09-14

### Added

- `scripts/conform-vertical.sh` converts the cleaned media to the 1080x1920 delivery format: fit-scaled, centred on black when the aspect is not 9:16, never cropped or stretched, audio copied, every frame kept. Media that is already 1080x1920 is stream-copied.
- `verify-video.sh --delivery-size WIDTHxHEIGHT` checks the delivered size in place of the source geometry comparison.

### Changed

- The delivered media is now always 1080x1920. The restored media is still verified against the source resolution before conforming, so an upstream downscale is still reported rather than hidden by the upscale.
- Every generated FCPXML timeline now uses a vertical 1080x1920 project format, regardless of the cleaned media's resolution. Previously the project copied the media's dimensions, so a 720x1280 or 608x1080 file produced a project of that size. The asset keeps its own format with the real media dimensions, so Final Cut Pro fits it into the 1080x1920 project; media that is already 1080x1920 shares the project format.

### Fixed

- The synthetic integration test used a byte copy of the source as the cleaned media, which `source_identity` has rejected since 1.2.0; it now uses a remux and exercises the conform step.

## [1.4.0] - 2026-09-12

### Fixed

- Result recovery matched only `removeWatermark result url:` in the Edimakor log. A job whose render succeeded but whose in-app download failed logs the finished URL under `FileReady url:` instead, so the fetch script reported no result for a job that was sitting complete on the server. Both markers are now matched, newest wins.

### Changed

- The subtitle region is chosen from a full-height row scan rather than from the contact sheets, and the same scan is run on the result to prove every cue was covered. Contact sheets are scaled down far enough to hide a low-contrast cue, and the outlying cue is the one they hide; a missed cue costs a second paid job.
- The credit balance is read before asking for batch approval, so the confirmation requested is one that can actually be honoured. Purchasing credits is out of scope in all cases.

## [1.3.0] - 2026-09-08

### Fixed

- Packaging scanned every byte of the cleaned media for email addresses and signed-URL fragments. Compressed video is effectively random data, so those patterns appeared by chance and rejected legitimate deliveries, while a real credential would never live in the bitstream to begin with.

### Changed

- The credential scan for cleaned media now reads `format_tags` and `stream_tags` via `ffprobe` and passes them through the same `scan_json` used for the JSON deliverables, catching the case that can actually occur instead of the one that cannot.

## [1.2.0] - 2026-09-08

### Fixed

- An upstream downscale could reach delivery unreported. When subtitles had been removed elsewhere and only cutting was requested, the workflow had no defined `$SOURCE`, so the delivered file was passed to `verify-video.sh` as its own `--source`; `source_display_geometry` then compared a file to itself and passed. A 720x1280 source delivered as 608x1080 was reported as matching the source.
- `verify-video.sh` gained a `source_identity` check that fails when `--source` is byte-identical to the cleaned media. The existing `source_alias` check only caught the same inode, not a copy.

### Added

- An explicit entry path for media whose subtitles were removed elsewhere: the pre-removal original must be supplied as `$SOURCE`, or the report must state that resolution, frame rate, and duration could not be verified.
- `source_resolution` and `export_resolution` manifest fields.

### Changed

- HitPaw's export resolution must be set to the source's exact dimensions and recorded before submission. Its presets are named by one dimension, so a 720x1280 source silently exports as 608x1080 unless set explicitly.
- Rescaling a downscaled result back to source dimensions is now a disclosed last resort rather than an unqualified step. It satisfies `source_display_geometry` without restoring any detail, so it must never be done silently.

## [1.1.0] - 2026-08-25

### Changed

- Review sheets now render four consecutive frames per candidate (offsets -2, -1, 0, +1) instead of a single before/after pair, so continuous motion can be separated from a real jump cut. Windows clamp at the media boundaries and repeat an end frame to keep the column count fixed.
- Review sheet pages adapt to the source aspect ratio: 3 candidates per page for portrait sources, 5 for landscape. The previous fixed 4x5 grid was sized for 16:9 and produced unreadably small frames on 9:16 material.
- `index.json` tiles carry a new `context_frames` field alongside the existing `frame` and `before_frame`.

### Fixed

- Review frames now reach the tiler in requested order. Context windows repeat frames and can step backwards between nearby candidates, which decode order alone no longer satisfies; the pipeline buffers only frames still owed and releases each after its final write.

### Documented

- A candidate anchors a cluster of nearby evidence and can sit one frame after the visible cut when that cluster carries no keyframe evidence.

## [1.0.0] - 2026-08-24

### Added

- Cross-compatible installation for Claude Code, Codex, and the shared agent Skill directory.
- Safe HitPaw Edimakor submission boundary and completed-result recovery without duplicate paid jobs.
- Full-duration inspection sheets and per-file subtitle-region decisions.
- High-recall scene, keyframe, and frame-difference jump-cut candidate analysis with mandatory frame-pair review.
- Contiguous, frame-aligned Final Cut Pro FCPXML generation that preserves the complete media order.
- Decode, geometry, duration, audio-presence, XML, package-integrity, privacy, and repository validation.
- Deterministic batch delivery archives with sanitized manifests and SHA-256 checksums.

[Unreleased]: https://github.com/superchaospc/video-hardsub-fcp-skill/compare/v1.5.0...HEAD
[1.5.0]: https://github.com/superchaospc/video-hardsub-fcp-skill/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/superchaospc/video-hardsub-fcp-skill/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/superchaospc/video-hardsub-fcp-skill/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/superchaospc/video-hardsub-fcp-skill/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/superchaospc/video-hardsub-fcp-skill/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/superchaospc/video-hardsub-fcp-skill/releases/tag/v1.0.0
