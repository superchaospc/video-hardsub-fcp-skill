# Video Hardsub FCP Skill Design

## Goal

Create a public, script-assisted Agent Skill that lets Codex or Claude Code respond to requests such as “去字幕切段” by removing burned-in subtitles, detecting both obvious shot changes and same-camera micro jump cuts, and delivering an editable Final Cut Pro XML package.

The canonical repository will be `superchaospc/video-hardsub-fcp-skill`, released as `v1.0.0` under the MIT License.

## Scope

The first release supports:

- macOS video workflows using HitPaw Edimakor for burned-in subtitle removal;
- one video or a folder of videos;
- MP4 and MOV input;
- frame-aligned, high-recall jump-cut detection;
- an editable FCPXML timeline that keeps the entire cleaned video and audio in their original order;
- per-video deliverables plus an optional batch package;
- installation in Claude Code with symlinks for Codex discovery.

The first release does not build a standalone GUI, replace HitPaw with a custom inpainting model, or silently buy credits. It does not modify source media.

## Repository Layout

```text
video-hardsub-fcp-skill/
├── SKILL.md
├── README.md
├── LICENSE
├── CHANGELOG.md
├── agents/
│   └── openai.yaml
├── scripts/
│   ├── inspect-video.sh
│   ├── fetch-hitpaw-result.sh
│   ├── analyze-cuts.py
│   ├── build-fcpxml.py
│   ├── verify-video.sh
│   └── install.sh
├── tests/
│   ├── test_analyze_cuts.py
│   └── test_build_fcpxml.py
└── .github/workflows/validate.yml
```

`SKILL.md` stays concise and routes deterministic work to scripts. `README.md` is user-facing packaging documentation and contains GitHub badges, installation commands, examples, limitations, and release information.

## Workflow

1. Inventory the requested file or folder with `ffprobe`; reject unsupported or empty inputs without changing them.
2. Generate full-duration contact sheets and identify the smallest safe subtitle region for each video.
3. Present one batch confirmation containing the exact files, subtitle regions, and expected HitPaw submissions. Submit each video once after approval.
4. Drive HitPaw through the desktop-control capability available in the current host. If the host has no desktop-control capability, stop with a precise handoff instead of claiming automation succeeded.
5. Recover the result from HitPaw logs, including the known stalled-download path, without resubmitting the paid task.
6. Restore the intended output geometry, preserve audio, decode the full result, and inspect at least one frame per second.
7. Analyze edit boundaries using three signals: scene-change score, encoded I-frame or packet discontinuity, and isolated local frame-difference peaks. Produce a review sheet around every candidate.
8. Bias toward recall for the default “精细切段” mode. Include same-camera action jumps only after frame-pair review; do not treat persistent fast motion, steam, falling ingredients, or camera shake as cuts by score alone.
9. Generate one contiguous FCPXML timeline per video. Every source frame appears exactly once, clip boundaries align to the source frame rate, and audio order is unchanged.
10. Validate XML syntax, clip-count and duration invariants, media existence, full video decode, and archive integrity before delivery.

## Script Interfaces

### `inspect-video.sh INPUT WORK_DIR`

Writes probe data and full-duration contact sheets without modifying `INPUT`.

### `fetch-hitpaw-result.sh --wait OUTPUT`

Polls HitPaw logs for the newest completed result and downloads it once. It never submits or retries an AI removal task.

### `analyze-cuts.py VIDEO --output CUTS_JSON --mode fine`

Uses `ffmpeg` and `ffprobe` output to generate frame-aligned candidates with supporting metrics. The JSON records selected cuts, rejected candidates, confidence, and evidence so an agent can audit the decision.

### `build-fcpxml.py VIDEO CUTS_JSON --output PROJECT.fcpxml`

Builds a contiguous FCPXML project from reviewed cut frames. It rejects non-monotonic, duplicate, out-of-range, or non-frame-aligned cuts.

### `verify-video.sh VIDEO VERIFY_DIR`

Performs full decode, probes dimensions and audio, and creates one-frame-per-second verification sheets.

### `install.sh`

Installs or updates the repository under `~/.claude/skills/video-hardsub-fcp` and creates symlinks in existing Codex skill directories. It refuses to overwrite real directories.

## Host Compatibility

Claude Code and Codex use the same `SKILL.md` format. The repository is the single source of truth:

- Claude Code discovers the canonical checkout in `~/.claude/skills/video-hardsub-fcp`.
- Codex discovers symlinks in `~/.codex/skills/` and `~/.agents/skills/`.
- `agents/openai.yaml` provides Codex display metadata and keeps implicit invocation enabled.

Desktop automation is capability-dependent. The skill describes the required UI outcome and uses the host's available desktop-control tool rather than hard-coding a tool name that exists in only one client.

## Safety and Failure Handling

- Preserve source files and write intermediates under a dedicated work directory.
- Confirm the exact batch and subtitle regions before paid HitPaw submissions.
- Submit each file once. Slow processing or stalled downloading is not permission to resubmit.
- Use a tight subtitle band when possible; warn before full-frame removal because it may erase legitimate scene text.
- Keep raw HitPaw output for comparison when inpainting damages food, hands, tools, packaging, or interfaces.
- On partial batch failure, retain completed outputs and write a per-file status report so the next run resumes only unfinished files.
- Never publish source videos, signed download URLs, HitPaw logs, account data, or generated user media to GitHub.

## Testing Strategy

Skill behavior is evaluation-driven:

- Baseline scenarios without the skill document failures such as using scene score alone, resubmitting a slow paid task, or generating non-contiguous FCPXML.
- The same scenarios run with the skill must produce the correct plan and stop conditions.
- Unit tests cover cut-frame normalization, invalid candidate rejection, FCPXML escaping, frame-rate handling, clip continuity, and duration sums.
- Integration tests use synthetic videos generated by `ffmpeg`; no copyrighted or private media is committed.
- GitHub Actions runs Python tests, shell syntax checks, XML validation, and a repository secret scan.

## Documentation and Release

The README will be Chinese-first with an English summary. Badges will cover the latest GitHub Release, CI status, MIT License, macOS, Codex, Claude Code, and Final Cut Pro/FCPXML compatibility.

Release `v1.0.0` will include installation instructions, the trigger phrase, batch usage, limitations, and checksums for a source archive generated by GitHub. The release will not include user videos or local logs.

## Success Criteria

- `quick_validate.py` accepts the Skill structure and frontmatter.
- Baseline failures are reproduced, and post-skill evaluation scenarios pass.
- All script tests pass on macOS and in GitHub Actions where platform-independent.
- A synthetic timeline round-trip yields valid XML with continuous clips whose total frame count equals the source.
- Claude Code and Codex resolve the same canonical `SKILL.md` through the installed checkout and symlinks.
- The public repository, `v1.0.0` tag, and GitHub Release are visible and contain no private media or credentials.
