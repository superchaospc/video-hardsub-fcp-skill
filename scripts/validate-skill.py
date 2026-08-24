#!/usr/bin/env python3
"""Validate the publishable video-hardsub-fcp Skill repository."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


EXPECTED_NAME = "video-hardsub-fcp"
OPENAI_TOKEN = "$video-hardsub-fcp"
REQUIRED_PATHS = (
    "SKILL.md",
    "agents/openai.yaml",
    "references/hitpaw-workflow.md",
    "scripts/analyze-cuts.py",
    "scripts/build-fcpxml.py",
    "scripts/fetch-hitpaw-result.sh",
    "scripts/inspect-video.sh",
    "scripts/install.sh",
    "scripts/package-deliverables.sh",
    "scripts/verify-video.sh",
)
FORBIDDEN_SUFFIXES = frozenset(
    {
        ".mp4",
        ".mov",
        ".mkv",
        ".avi",
        ".log",
        ".key",
        ".pem",
        ".zip",
        ".tar",
        ".gz",
        ".7z",
        ".rar",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".heic",
    }
)
GIT_METADATA_NAME = ".git"

DECLARED_PATH_RE = re.compile(r"(?<![A-Za-z0-9_.-])((?:scripts|references)/[A-Za-z0-9_.-]+)")
SIGNED_URL_RE = re.compile(
    r"https://[^\s<>()\[\]{}\"']*[?&](?:"
    r"[A-Za-z0-9._~-]*(?:token|signature|expires)|"
    r"sig|credential|key|"
    r"auth(?:[_-]?key)?|api[_-]?key|secret(?:[_-]?key)?|password|"
    r"x-amz-(?:signature|credential|security-token|expires)|ossaccesskeyid"
    r")=",
    re.IGNORECASE,
)
PRIVATE_PATH_RE = re.compile(
    r"(?:/" + r"Users/[A-Za-z0-9._-]+/|/home/[A-Za-z0-9._-]+/|[A-Za-z]:\\Users\\[^\\\s]+\\)",
    re.IGNORECASE,
)


def _regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


class RepositoryTextError(ValueError):
    """Raised when a repository file is not clean UTF-8 text."""


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if b"\0" in data:
        raise RepositoryTextError("NUL byte")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RepositoryTextError("invalid UTF-8") from exc


def _frontmatter(text: str) -> tuple[dict[str, str], str | None]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, "SKILL.md must begin with YAML frontmatter"
    try:
        closing = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration:
        return {}, "SKILL.md frontmatter is missing its closing '---'"

    fields: dict[str, str] = {}
    for line in lines[1:closing]:
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\s*$", line)
        if match:
            value = match.group(2)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            fields[match.group(1)] = value
    return fields, None


def _inspect_publishable_paths(root: Path, relatives: list[str]) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    errors: list[str] = []
    for relative in relatives:
        candidate = Path(relative)
        if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
            errors.append(f"invalid publishable repository path: {relative!r}")
            continue
        path = root / candidate
        normalized = candidate.as_posix()
        try:
            mode = os.lstat(path).st_mode
        except OSError:
            errors.append(f"repository entry could not be inspected: {normalized}")
            continue
        if stat.S_ISLNK(mode):
            errors.append(f"symlink is not allowed: {normalized}")
        elif stat.S_ISREG(mode):
            files.append(path)
        else:
            errors.append(f"unsupported repository filesystem entry: {normalized}")
    return sorted(files), errors


def _git_publishable_files(root: Path) -> tuple[list[Path], list[str]] | None:
    git_metadata = root / GIT_METADATA_NAME
    if not git_metadata.exists() and not git_metadata.is_symlink():
        return None
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    try:
        relatives = [raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw]
    except UnicodeDecodeError:
        return [], ["Git publishable repository path is not valid UTF-8"]
    return _inspect_publishable_paths(root, relatives)


def _repository_files(root: Path) -> tuple[list[Path], list[str]]:
    git_files = _git_publishable_files(root)
    if git_files is not None:
        return git_files

    files: list[Path] = []
    errors: list[str] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError:
            relative = directory.relative_to(root).as_posix() or "."
            errors.append(f"repository directory could not be read: {relative}")
            continue
        for entry in entries:
            if entry.name == GIT_METADATA_NAME:
                continue
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            try:
                mode = os.lstat(path).st_mode
            except OSError:
                errors.append(f"repository entry could not be inspected: {relative}")
                continue
            if stat.S_ISLNK(mode):
                errors.append(f"symlink is not allowed: {relative}")
            elif stat.S_ISDIR(mode):
                pending.append(path)
            elif stat.S_ISREG(mode):
                files.append(path)
            else:
                errors.append(f"unsupported repository filesystem entry: {relative}")
    return sorted(files), errors


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    root = root.resolve()
    if not root.is_dir():
        return [f"repository root is not a directory: {root}"]

    for relative in REQUIRED_PATHS:
        path = root / relative
        if not _regular_file(path):
            errors.append(f"required file is missing or is not a regular file: {relative}")

    skill_path = root / "SKILL.md"
    skill_text = None
    if _regular_file(skill_path):
        try:
            skill_text = _read_text(skill_path)
        except (OSError, RepositoryTextError):
            errors.append("SKILL.md must be UTF-8 text without NUL bytes")
    if skill_text is not None:
        fields, parse_error = _frontmatter(skill_text)
        if parse_error:
            errors.append(parse_error)
        for field in ("name", "description"):
            if not fields.get(field, "").strip():
                errors.append(f"SKILL.md requires non-empty frontmatter field '{field}'")
        if fields.get("name") and fields["name"] != EXPECTED_NAME:
            errors.append(f"SKILL.md frontmatter name must be exactly '{EXPECTED_NAME}'")

        for relative in sorted(set(DECLARED_PATH_RE.findall(skill_text))):
            if not _regular_file(root / relative):
                errors.append(f"declared Skill path is missing or is not a regular file: {relative}")

    openai_path = root / "agents/openai.yaml"
    openai_text = None
    if _regular_file(openai_path):
        try:
            openai_text = _read_text(openai_path)
        except (OSError, RepositoryTextError):
            errors.append("agents/openai.yaml must be UTF-8 text without NUL bytes")
    if openai_text is not None and OPENAI_TOKEN not in openai_text:
        errors.append(f"agents/openai.yaml must contain the token {OPENAI_TOKEN}")

    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        for path in sorted(scripts_dir.iterdir()):
            if path.suffix.lower() not in {".py", ".sh"} or not _regular_file(path):
                continue
            try:
                mode = path.lstat().st_mode
            except OSError:
                continue
            if mode & 0o111 == 0:
                errors.append(f"script entry point is not executable: {path.relative_to(root)}")

    repository_files, scan_errors = _repository_files(root)
    errors.extend(scan_errors)
    for path in repository_files:
        relative = path.relative_to(root).as_posix()
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden release file: {relative}")
        try:
            text = _read_text(path)
        except RepositoryTextError:
            errors.append(f"binary or non-UTF-8 repository file: {relative}")
            continue
        except OSError:
            errors.append(f"repository file could not be read: {relative}")
            continue
        if SIGNED_URL_RE.search(text):
            errors.append(f"possible signed URL in repository text: {relative}")
        if PRIVATE_PATH_RE.search(text):
            errors.append(f"private user path in repository text: {relative}")

    return sorted(set(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", type=Path, help="Skill repository root")
    args = parser.parse_args(argv)
    errors = validate(args.root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Skill validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
