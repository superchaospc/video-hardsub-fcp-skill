# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/superchaospc/video-hardsub-fcp-skill/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/superchaospc/video-hardsub-fcp-skill/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/superchaospc/video-hardsub-fcp-skill/releases/tag/v1.0.0
