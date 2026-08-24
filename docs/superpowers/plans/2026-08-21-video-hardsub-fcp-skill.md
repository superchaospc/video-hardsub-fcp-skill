# Video Hardsub FCP Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, test, install, and publicly release a script-assisted Codex/Claude Code Skill that removes burned-in subtitles with HitPaw, detects fine jump cuts, and delivers editable Final Cut Pro FCPXML timelines.

**Architecture:** Keep the agent-facing workflow concise in `SKILL.md`, place fragile HitPaw operating rules in a one-level reference, and put deterministic inspection, cut analysis, FCPXML generation, verification, and installation behavior in scripts. Cut analysis uses scene scores, keyframes, and isolated frame-difference peaks to generate high-recall candidates, then requires visual before/after review before accepted cut frames can be converted into a contiguous FCPXML timeline.

**Tech Stack:** Bash, Python 3 standard library, ffmpeg/ffprobe, HitPaw Edimakor on macOS, FCPXML 1.10, `unittest`, GitHub Actions, GitHub CLI.

---

## File map

- `SKILL.md`: Cross-agent trigger text, workflow checklist, hard safety rules, and script entry points.
- `references/hitpaw-workflow.md`: HitPaw GUI submission/recovery rules and batch-specific decisions.
- `agents/openai.yaml`: Codex display metadata and `$video-hardsub-fcp` default invocation.
- `scripts/inspect-video.sh`: Probe source metadata and render contact sheets without modifying the source.
- `scripts/fetch-hitpaw-result.sh`: Recover the finished HitPaw URL from logs and download it once.
- `scripts/analyze-cuts.py`: Collect scene/keyframe/frame-difference evidence, merge candidates, and create frame-pair review sheets.
- `scripts/build-fcpxml.py`: Validate approved cut frames and generate a contiguous, audio-preserving FCPXML timeline.
- `scripts/verify-video.sh`: Check geometry, duration, audio, and full decode of the cleaned render.
- `scripts/package-deliverables.sh`: Build and integrity-test an optional per-video or batch delivery archive.
- `scripts/validate-skill.py`: Validate frontmatter, required files, executable scripts, and forbidden secret/media files.
- `scripts/install.sh`: Install the canonical Claude Code copy and symlink it into both Codex skill locations.
- `tests/test_analyze_cuts.py`: Unit tests for fine cut evidence, peak isolation, clustering, and JSON shape.
- `tests/test_build_fcpxml.py`: Unit tests for exact timeline coverage, path escaping, and invalid cuts.
- `tests/evaluations/*.json`: Three behavior evaluations covering a single file, a heterogeneous batch, and difficult micro-cuts.
- `.github/workflows/validate.yml`: Linux CI for unit tests, shell syntax, skill validation, and secret scan.
- `README.md`, `LICENSE`, `CHANGELOG.md`: Public documentation, MIT terms, and v1.0.0 release history.
- `docs/release-notes-v1.0.0.md`: Exact GitHub Release body.

### Task 1: Create the implementation branch and baseline evaluations

**Files:**
- Create: `tests/evaluations/single-video.json`
- Create: `tests/evaluations/batch-mixed-layouts.json`
- Create: `tests/evaluations/fine-jump-cuts.json`
- Create: `tests/evaluations/baseline-observations.md`

- [ ] **Step 1: Create a feature branch**

Run:

```bash
git switch -c feat/initial-release
```

Expected: `Switched to a new branch 'feat/initial-release'`.

- [ ] **Step 2: Add the three evaluation fixtures**

Create JSON files with these exact query/rubric pairs:

```json
{
  "skills": ["video-hardsub-fcp"],
  "query": "去字幕切段：处理这个带固定底部硬字幕的竖屏 MP4，交付可在 Final Cut Pro 继续编辑的时间线。",
  "expected_behavior": [
    "Inspects metadata and contact sheets before choosing a subtitle region",
    "Preserves the source and uses a separate ASCII working copy",
    "Submits the HitPaw job once and recovers the existing result instead of resubmitting",
    "Generates high-recall cut candidates and visually reviews adjacent frame pairs",
    "Verifies decode, audio, duration, geometry, and FCPXML continuity"
  ]
}
```

```json
{
  "skills": ["video-hardsub-fcp"],
  "query": "批量去字幕切段：这个文件夹里有 12 个视频，字幕位置可能不同，其中一个字幕会移动。",
  "expected_behavior": [
    "Inventories every file and groups only files with visually compatible subtitle regions",
    "Requests one explicit batch confirmation covering files, regions, full-frame risks, and credit usage",
    "Does not apply one crop or inpaint region blindly to all files",
    "Maintains resumable per-file state and never publishes source videos or service URLs",
    "Produces one cleaned media file and one editable FCPXML package per source"
  ]
}
```

```json
{
  "skills": ["video-hardsub-fcp"],
  "query": "去字幕切段，但上一版漏了很多同机位微跳剪。画面里还有蒸汽、撒料和快速翻炒，不要把持续运动都当成剪辑点。",
  "expected_behavior": [
    "Combines scene scores, keyframes, and isolated frame-difference peaks instead of relying on scene score alone",
    "Uses a fine high-recall mode and clusters nearby evidence into one candidate",
    "Produces before/after review sheets for every candidate",
    "Rejects persistent motion, steam, ingredient motion, and camera shake unless a true discontinuity is visible",
    "Builds the timeline only from visually approved cut frames"
  ]
}
```

- [ ] **Step 3: Run fresh-agent baseline evaluations without the new Skill**

Dispatch one fresh agent per fixture with the fixture query but without access to the new Skill. Save each response and score every rubric line as `PASS` or `FAIL` in `tests/evaluations/baseline-observations.md`. The file must name the three recurring gaps observed in prior work: scene-score-only under-detection, unsafe blind batching, and accidental resubmission after a slow HitPaw job.

- [ ] **Step 4: Commit the baseline**

```bash
git add tests/evaluations/single-video.json tests/evaluations/batch-mixed-layouts.json tests/evaluations/fine-jump-cuts.json tests/evaluations/baseline-observations.md
git commit -m "test: establish video workflow skill baseline"
```

Expected: one commit containing only evaluation artifacts.

### Task 2: Implement fine jump-cut candidate analysis with TDD

**Files:**
- Create: `scripts/analyze-cuts.py`
- Create: `tests/test_analyze_cuts.py`

- [ ] **Step 1: Write failing tests for isolated peaks and candidate clustering**

The test module loads the hyphenated script with `importlib.util` and includes these cases:

```python
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).parents[1] / "scripts" / "analyze-cuts.py"
SPEC = importlib.util.spec_from_file_location("analyze_cuts", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AnalyzeCutsTests(unittest.TestCase):
    def test_isolated_peak_survives_but_persistent_motion_is_rejected(self):
        scores = [1, 1, 2, 18, 2, 1, 9, 10, 11, 10, 9, 1]
        self.assertEqual(MODULE.isolated_peaks(scores, minimum=8, ratio=2.5), [3])

    def test_nearby_evidence_merges_into_one_highest_confidence_candidate(self):
        events = [
            MODULE.Evidence(100, "scene", 0.08),
            MODULE.Evidence(101, "keyframe", 1.0),
            MODULE.Evidence(102, "ydif", 19.0),
            MODULE.Evidence(220, "ydif", 15.0),
        ]
        candidates = MODULE.merge_evidence(events, cluster_frames=3)
        self.assertEqual([item.frame for item in candidates], [101, 220])
        self.assertEqual(candidates[0].reasons, ["keyframe", "scene", "ydif"])

    def test_cut_plan_requires_manual_review_before_xml(self):
        plan = MODULE.make_plan("source.mp4", 30.0, 300, [])
        self.assertTrue(plan["requires_visual_review"])
        self.assertEqual(plan["selected_frames"], [])
        self.assertEqual(plan["rejected_candidates"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```bash
python3 -m unittest tests/test_analyze_cuts.py -v
```

Expected: failure because `scripts/analyze-cuts.py` does not exist.

- [ ] **Step 3: Implement the deterministic core**

Implement these public interfaces in `scripts/analyze-cuts.py`:

```python
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

def isolated_peaks(scores: Sequence[float], minimum: float, ratio: float) -> list[int]:
    """Return sharp one-frame peaks; reject runs of sustained high motion."""

def merge_evidence(events: Sequence[Evidence], cluster_frames: int, fps: float = 30.0) -> list[Candidate]:
    """Cluster nearby evidence and choose the frame with the strongest combined support."""

def make_plan(source: str, fps: float, frame_count: int, candidates: Sequence[Candidate]) -> dict:
    """Return versioned JSON with candidates and an empty selected_frames list."""
```

Use a five-frame local median as the baseline for `isolated_peaks`. Reject a position when either adjacent score is at least 70% of the current score; this removes persistent steam, camera motion, and fast action runs. In `merge_evidence`, score evidence as keyframe `+2.0`, scene `+3.0 * min(score / 0.08, 2.0)`, and YDIF `+min(score / 12.0, 2.0)`, then prefer the frame with the most distinct evidence kinds and highest combined score.

- [ ] **Step 4: Run the tests and confirm GREEN**

```bash
python3 -m unittest tests/test_analyze_cuts.py -v
```

Expected: `Ran 3 tests ... OK`.

- [ ] **Step 5: Add ffmpeg/ffprobe collection and review-sheet CLI**

Add a CLI with this contract:

```text
python3 scripts/analyze-cuts.py INPUT --output cut-plan.json --review-dir review --mode fine
```

It must:

1. Require `ffmpeg` and `ffprobe` with actionable errors.
2. Probe rational frame rate, frame count, duration, and keyframe timestamps.
3. Parse scene evidence from `select='gt(scene,0.012)',metadata=print`.
4. Parse every-frame YDIF from `signalstats,metadata=print` and call `isolated_peaks` with `minimum=8` and `ratio=2.5` in fine mode.
5. Exclude frame zero and candidates within two frames of the end.
6. Write `cut-plan.json` atomically with `requires_visual_review: true`, empty `selected_frames`, and empty `rejected_candidates`.
7. Render PNG sheets containing the frame before and the candidate frame, ten candidates per sheet, plus `review/index.json` mapping each tile to its candidate frame.
8. Exit nonzero without overwriting an existing plan unless `--force` is given.

Visual review must fill `selected_frames` and `rejected_candidates`; every candidate frame must appear in exactly one of those lists before FCPXML generation. Each rejected record includes a short reason such as `persistent-motion`, `steam`, `ingredient-motion`, `camera-shake`, or `no-visible-discontinuity`.

- [ ] **Step 6: Add parsing and CLI error tests**

Add tests for decimal/rational FPS parsing, empty evidence, frame-zero filtering, atomic overwrite refusal, and malformed ffmpeg metadata. Run:

```bash
python3 -m unittest tests/test_analyze_cuts.py -v
```

Expected: all analyzer tests pass.

- [ ] **Step 7: Commit the analyzer**

```bash
git add scripts/analyze-cuts.py tests/test_analyze_cuts.py
git commit -m "feat: detect fine jump-cut candidates"
```

### Task 3: Generate contiguous Final Cut Pro timelines with TDD

**Files:**
- Create: `scripts/build-fcpxml.py`
- Create: `tests/test_build_fcpxml.py`

- [ ] **Step 1: Write failing FCPXML tests**

Cover a 300-frame, 30 fps asset with approved cuts `[75, 150, 221]`. Parse the XML with `xml.etree.ElementTree` and assert:

```python
self.assertEqual(root.attrib["version"], "1.10")
self.assertEqual(len(clips), 4)
self.assertEqual(sum(parse_fcpx_time(c.attrib["duration"]) for c in clips), Fraction(10, 1))
self.assertEqual([c.attrib["offset"] for c in clips], ["0s", "5/2s", "5s", "221/30s"])
self.assertEqual([c.attrib["start"] for c in clips], ["0s", "5/2s", "5s", "221/30s"])
```

Also assert that `&`, non-ASCII filenames, and spaces survive as a valid percent-encoded file URI, and that duplicate, unsorted, zero, negative, or out-of-range cuts raise `ValueError`.

- [ ] **Step 2: Run the tests and confirm RED**

```bash
python3 -m unittest tests/test_build_fcpxml.py -v
```

Expected: failure because `scripts/build-fcpxml.py` does not exist.

- [ ] **Step 3: Implement exact rational timeline construction**

Define:

```python
@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    fps: Fraction
    frame_count: int
    duration: Fraction
    has_audio: bool

def frames_to_time(frame: int, fps: Fraction) -> str:
    """Return a reduced FCPXML time such as 1001/30000s, 5/2s, or 0s."""

def validate_cuts(cuts: Sequence[int], frame_count: int) -> list[int]:
    """Require strictly increasing unique cut frames inside the media bounds."""

def build_fcpxml(source: Path, info: MediaInfo, cuts: Sequence[int], event_name: str) -> str:
    """Create one asset and contiguous asset-clips covering every source frame once."""
```

Use one `asset` resource for the cleaned media, one `format` resource with width/height/frame duration, and a single `spine` of `asset-clip` elements. For every segment, set `offset == start`, preserve source audio through the asset reference, and use the next cut or frame count to compute duration. Serialize as UTF-8 XML with an FCPXML doctype.

- [ ] **Step 4: Run tests and confirm GREEN**

```bash
python3 -m unittest tests/test_build_fcpxml.py -v
```

Expected: all FCPXML tests pass.

- [ ] **Step 5: Add the CLI and plan validation**

CLI contract:

```text
python3 scripts/build-fcpxml.py CLEANED.mp4 cut-plan.json --output project.fcpxml
```

The CLI must refuse a plan when `requires_visual_review` is true and `selected_frames` is empty, unless the video genuinely has no candidate cuts and the user explicitly sets `review_decision: "no-cuts"`. It must confirm that the plan source basename matches the supplied media or require `--accept-renamed-media`. Probe media with ffprobe, write atomically, parse the written XML back, and verify clip count, duration sum, and URI existence.

- [ ] **Step 6: Commit the generator**

```bash
git add scripts/build-fcpxml.py tests/test_build_fcpxml.py
git commit -m "feat: generate verified Final Cut Pro timelines"
```

### Task 4: Add inspection, HitPaw recovery, and media verification scripts

**Files:**
- Create: `scripts/inspect-video.sh`
- Create: `scripts/fetch-hitpaw-result.sh`
- Create: `scripts/verify-video.sh`
- Create: `scripts/package-deliverables.sh`

- [ ] **Step 1: Add `inspect-video.sh`**

The script accepts `INPUT OUTPUT_DIR`, refuses missing tools/files, creates the output directory, writes `probe.json`, renders a 4x4 full-frame contact sheet, and renders a subtitle-band sheet from the bottom 35% of the frame. Quote every path and use `set -euo pipefail`. It must never write beside or replace the source.

- [ ] **Step 2: Add `fetch-hitpaw-result.sh`**

The script accepts `[--wait] OUTPUT_FILE` and reads only `${HITPAW_LOG_DIR:-$HOME/Library/Caches/HitPaw Edimakor/HitpawEdimakor}`. It finds the newest completed HTTPS result URL, rejects non-HTTPS and empty matches, downloads to `OUTPUT_FILE.part`, verifies it with ffprobe, then atomically renames it. `--wait` requires a URL newer than the one present at startup and times out according to `HITPAW_WAIT_SECONDS`. If `OUTPUT_FILE` already validates, exit successfully without network access. Never print the signed URL.

- [ ] **Step 3: Add `verify-video.sh`**

The script accepts `CLEANED VERIFY_DIR [--source SOURCE]`, probes the cleaned video, creates one-frame-per-second verification sheets, and performs `ffmpeg -v error -xerror -i CLEANED -f null -`. When `--source` is present, it also compares width, height, duration tolerance, and audio presence and writes `VERIFY_DIR/report.json`. Geometry mismatch, missing source audio, duration drift over `max(0.20 seconds, two frames)`, or decode errors must exit nonzero.

- [ ] **Step 4: Add `package-deliverables.sh`**

The script accepts `MANIFEST_JSON OUTPUT_ZIP`. It validates that every manifest deliverable exists, rejects sources, logs, signed URLs, and files outside the manifest directory, writes a temporary ZIP containing only cleaned media, FCPXML, cut decisions, and verification reports, runs `unzip -t`, writes SHA-256 hashes into `SHA256SUMS`, then atomically renames the archive. A batch manifest may contain multiple per-video entries; partial or failed entries are excluded and remain resumable.

- [ ] **Step 5: Syntax-test the shell scripts**

```bash
bash -n scripts/inspect-video.sh scripts/fetch-hitpaw-result.sh scripts/verify-video.sh scripts/package-deliverables.sh
```

Expected: no output and exit code 0.

- [ ] **Step 6: Commit operational scripts**

```bash
git add scripts/inspect-video.sh scripts/fetch-hitpaw-result.sh scripts/verify-video.sh scripts/package-deliverables.sh
git commit -m "feat: add safe video processing helpers"
```

### Task 5: Author the cross-compatible Skill and installation path

**Files:**
- Create: `SKILL.md`
- Create: `references/hitpaw-workflow.md`
- Create: `agents/openai.yaml`
- Create: `scripts/install.sh`

- [ ] **Step 1: Write concise Skill metadata and workflow**

Use this frontmatter exactly:

```yaml
---
name: video-hardsub-fcp
description: Removes burned-in subtitles, detects fine same-camera jump cuts, and prepares editable Final Cut Pro FCPXML timelines. Use when a user says “去字幕切段”, asks to batch-process hard-subtitled videos, reports missed micro jump cuts, or wants cleaned media ready for Final Cut Pro.
---
```

The body must require this checklist:

```text
Video workflow:
- [ ] Inventory sources; preserve originals
- [ ] Inspect contact sheets; choose per-file subtitle regions
- [ ] Confirm one batch manifest, regions, full-frame risk, and HitPaw credits
- [ ] Submit each HitPaw job once; recover completed jobs from logs
- [ ] Restore source geometry/audio and fully decode-verify
- [ ] Generate high-recall cut candidates and frame-pair review sheets
- [ ] Classify every candidate: approve true discontinuities; reject persistent motion artifacts
- [ ] Build and validate one FCPXML timeline per cleaned video
- [ ] Deliver media, FCPXML, report, and a small manifest
```

It must state that “去字幕切段” is sufficient invocation, source files are immutable, full-frame removal needs a quality warning, candidate scores never replace visual review, and source videos/logs/signed URLs/account details must never enter Git or release artifacts. Link directly to `references/hitpaw-workflow.md`; do not create nested references.

- [ ] **Step 2: Write the HitPaw reference**

Document the exact safe sequence: ASCII working copy, inspect before region selection, tight band for fixed captions, full-frame only for moving captions, one submission, accept compatibility conversion, wait/recover from logs, avoid resubmission, fetch raw result, restore source geometry/audio, inspect at 1 fps across full duration, trim only confirmed end cards, and full decode verification. For batches, require a manifest with source, region, status, result path, verification status, cut-plan path, and FCPXML path.

- [ ] **Step 3: Add Codex agent metadata**

Create `agents/openai.yaml`:

```yaml
interface:
  display_name: "Video Hardsub → FCP"
  short_description: "去硬字幕、精细识别跳剪并生成 FCPXML"
  default_prompt: "Use $video-hardsub-fcp to remove burned-in subtitles, review fine jump cuts, and prepare an editable Final Cut Pro timeline."
policy:
  allow_implicit_invocation: true
```

- [ ] **Step 4: Add a conflict-safe installer**

`scripts/install.sh` must:

1. Require macOS, `git`, `python3`, `ffmpeg`, and `ffprobe`; report HitPaw as a separate GUI prerequisite.
2. Install/update the canonical checkout at `${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}/video-hardsub-fcp` from `https://github.com/superchaospc/video-hardsub-fcp-skill.git`.
3. Symlink that canonical directory to `${CODEX_HOME:-$HOME/.codex}/skills/video-hardsub-fcp` and `$HOME/.agents/skills/video-hardsub-fcp`.
4. Treat a correct symlink as success; refuse to replace any real file, real directory, or symlink to a different target.
5. Support `--source DIR` so the current local checkout can be installed before publishing without copying it.

- [ ] **Step 5: Run fresh-agent evaluations with the Skill**

Dispatch fresh agents for all three fixtures with access to the new Skill. Record rubric results in `tests/evaluations/skill-observations.md`. Each line must pass; when one fails, make the smallest instruction change, rerun only that fixture, and record the change and rerun outcome.

- [ ] **Step 6: Commit the Skill**

```bash
git add SKILL.md references/hitpaw-workflow.md agents/openai.yaml scripts/install.sh tests/evaluations/skill-observations.md
git commit -m "feat: add cross-compatible video workflow skill"
```

### Task 6: Add repository validation, docs, badges, and MIT release files

**Files:**
- Create: `scripts/validate-skill.py`
- Create: `tests/test_validate_skill.py`
- Create: `.github/workflows/validate.yml`
- Create: `.gitignore`
- Create: `README.md`
- Create: `LICENSE`
- Create: `CHANGELOG.md`
- Create: `docs/release-notes-v1.0.0.md`

- [ ] **Step 1: Test and implement repository validation**

Write tests that require both frontmatter fields, the exact Skill name, the `$video-hardsub-fcp` token in `agents/openai.yaml`, all declared scripts/references, executable bits on shell/Python entry points, and a clean media/secret scan. The validator must fail on extensions `.mp4`, `.mov`, `.mkv`, `.avi`, `.log`, `.key`, `.pem`, and on text matching `https://[^ ]+(token|signature|expires)=` case-insensitively.

Run RED, implement `scripts/validate-skill.py`, then run GREEN:

```bash
python3 -m unittest tests/test_validate_skill.py -v
python3 scripts/validate-skill.py .
```

Expected: tests pass and validator prints `Skill validation passed`.

- [ ] **Step 2: Add CI**

Create `.github/workflows/validate.yml` triggered on pushes, pull requests, and manual dispatch. On `ubuntu-latest`, install ffmpeg, then run:

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/*.sh
python3 scripts/validate-skill.py .
```

- [ ] **Step 3: Write the public README with badges**

Place these badges at the top and link them to the repository destinations:

```markdown
[![CI](https://github.com/superchaospc/video-hardsub-fcp-skill/actions/workflows/validate.yml/badge.svg)](https://github.com/superchaospc/video-hardsub-fcp-skill/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/superchaospc/video-hardsub-fcp-skill)](https://github.com/superchaospc/video-hardsub-fcp-skill/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![macOS](https://img.shields.io/badge/platform-macOS-lightgrey)
![Codex](https://img.shields.io/badge/Codex-compatible-000000)
![Claude Code](https://img.shields.io/badge/Claude%20Code-compatible-D97757)
![FCPXML](https://img.shields.io/badge/Final%20Cut%20Pro-FCPXML-blue)
```

README sections: one-line Chinese quick start (`把视频发给代理并说：去字幕切段`), features, output package and integrity check, prerequisites, install command, Codex/Claude locations, single-file usage, batch usage, fine cut review explanation, safety/privacy, development tests, limitations, license, and a short English summary. State clearly that HitPaw Edimakor and Final Cut Pro are user-installed proprietary apps and are not bundled.

- [ ] **Step 4: Add license, changelog, ignore rules, and release notes**

Use the standard MIT license with `Copyright (c) 2026 superchaospc`. Ignore Python caches, macOS metadata, working media, review images, result logs, and local batch manifests. `CHANGELOG.md` follows Keep a Changelog and lists v1.0.0 features. Release notes summarize cross-agent installation, safe HitPaw recovery, fine cut review, FCPXML delivery, and verification commands.

- [ ] **Step 5: Run the complete local validation**

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/*.sh
python3 scripts/validate-skill.py .
python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" .
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 6: Commit public repository files**

```bash
git add scripts/validate-skill.py tests/test_validate_skill.py .github/workflows/validate.yml .gitignore README.md LICENSE CHANGELOG.md docs/release-notes-v1.0.0.md
git commit -m "docs: prepare public v1.0.0 release"
```

### Task 7: Run realistic integration checks and install both agent targets

**Files:**
- Create: `tests/integration/synthetic-cuts.sh`
- Modify: `README.md`

- [ ] **Step 1: Add a synthetic integration fixture generator**

The shell test must use ffmpeg lavfi sources to create three short color/shape segments, concatenate them losslessly enough to preserve two visible discontinuities, run `analyze-cuts.py`, assert candidates occur within two frames of both known boundaries, classify every candidate into `selected_frames` or `rejected_candidates`, run `build-fcpxml.py`, parse the XML, and confirm its clip durations sum to the probed media duration within one frame. It then creates a manifest, runs `package-deliverables.sh`, and verifies the ZIP. All generated files live in `mktemp -d` and are removed by a trap.

- [ ] **Step 2: Run the integration and full suite**

```bash
bash tests/integration/synthetic-cuts.sh
python3 -m unittest discover -s tests -v
python3 scripts/validate-skill.py .
python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" .
```

Expected: integration prints `synthetic integration passed`; all unit and structural tests pass.

- [ ] **Step 3: Install the local checkout for Claude Code and Codex**

```bash
bash scripts/install.sh --source "$PWD"
readlink "$HOME/.codex/skills/video-hardsub-fcp"
readlink "$HOME/.agents/skills/video-hardsub-fcp"
```

Expected: both links resolve to `$HOME/.claude/skills/video-hardsub-fcp`, and the canonical path resolves to this checkout without replacing unrelated directories.

- [ ] **Step 4: Commit integration coverage**

```bash
git add tests/integration/synthetic-cuts.sh README.md
git commit -m "test: verify end-to-end cut timeline workflow"
```

### Task 8: Review, merge, publish, tag, and release v1.0.0

**Files:**
- No new files.

- [ ] **Step 1: Perform two-stage review**

Run a specification review against `docs/superpowers/specs/2026-08-21-video-hardsub-fcp-skill-design.md`, then a code-quality review. Resolve every high/medium issue with a test-first change and exact-path commit. Confirm `git status --short` is clean.

- [ ] **Step 2: Re-run final verification**

```bash
python3 -m unittest discover -s tests -v
bash tests/integration/synthetic-cuts.sh
bash -n scripts/*.sh
python3 scripts/validate-skill.py .
python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" .
git diff --check main...HEAD
```

Expected: all checks exit 0.

- [ ] **Step 3: Merge the feature branch locally**

```bash
git switch main
git merge --no-ff feat/initial-release -m "release: prepare video hardsub FCP skill v1.0.0"
```

Expected: a merge commit on `main` and a clean worktree.

- [ ] **Step 4: Create the public GitHub repository and push main**

```bash
gh repo create superchaospc/video-hardsub-fcp-skill --public --source=. --remote=origin --description "Cross-compatible Codex and Claude Code skill for hard-subtitle removal, fine jump-cut review, and Final Cut Pro FCPXML delivery."
git push -u origin main
```

Expected: public repository `https://github.com/superchaospc/video-hardsub-fcp-skill` exists and `main` tracks `origin/main`.

- [ ] **Step 5: Verify GitHub Actions before releasing**

```bash
gh run list --workflow validate.yml --limit 1
gh run watch "$(gh run list --workflow validate.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
```

Expected: the latest `validate.yml` run completes successfully.

- [ ] **Step 6: Create and push the annotated tag**

```bash
git tag -a v1.0.0 -m "Video Hardsub FCP Skill v1.0.0"
git push origin v1.0.0
```

Expected: `refs/tags/v1.0.0` exists locally and on GitHub.

- [ ] **Step 7: Create the GitHub Release**

```bash
gh release create v1.0.0 --repo superchaospc/video-hardsub-fcp-skill --title "Video Hardsub FCP Skill v1.0.0" --notes-file docs/release-notes-v1.0.0.md --verify-tag
```

Expected: a public non-draft, non-prerelease Release for v1.0.0.

- [ ] **Step 8: Publish checksums for GitHub's source archives**

```bash
release_tmp=$(mktemp -d)
curl --fail --location "https://github.com/superchaospc/video-hardsub-fcp-skill/archive/refs/tags/v1.0.0.zip" -o "$release_tmp/video-hardsub-fcp-skill-v1.0.0.zip"
curl --fail --location "https://github.com/superchaospc/video-hardsub-fcp-skill/archive/refs/tags/v1.0.0.tar.gz" -o "$release_tmp/video-hardsub-fcp-skill-v1.0.0.tar.gz"
(cd "$release_tmp" && shasum -a 256 video-hardsub-fcp-skill-v1.0.0.zip video-hardsub-fcp-skill-v1.0.0.tar.gz > SHA256SUMS)
gh release upload v1.0.0 "$release_tmp/SHA256SUMS" --repo superchaospc/video-hardsub-fcp-skill
```

Expected: the v1.0.0 Release has a `SHA256SUMS` asset covering both GitHub source archives.

- [ ] **Step 9: Verify the public surface and installed Skill**

```bash
gh repo view superchaospc/video-hardsub-fcp-skill --json nameWithOwner,visibility,url,defaultBranchRef
gh release view v1.0.0 --repo superchaospc/video-hardsub-fcp-skill --json tagName,isDraft,isPrerelease,url
python3 "$HOME/.codex/skills/video-hardsub-fcp/scripts/validate-skill.py" "$HOME/.codex/skills/video-hardsub-fcp"
```

Expected: visibility is `PUBLIC`, default branch is `main`, release flags are false, and installed validation passes.
