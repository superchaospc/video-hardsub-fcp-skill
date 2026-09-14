from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPOSITORY_ROOT / "scripts" / "validate-skill.py"

REQUIRED_SCRIPTS = (
    "analyze-cuts.py",
    "build-fcpxml.py",
    "conform-vertical.sh",
    "fetch-hitpaw-result.sh",
    "inspect-video.sh",
    "install.sh",
    "package-deliverables.sh",
    "verify-video.sh",
)


class SkillFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.write(
            "SKILL.md",
            """---
name: video-hardsub-fcp
description: Use when a user asks to remove hard subtitles and review jump cuts.
---

# Video Hardsub FCP

Read [the HitPaw workflow](references/hitpaw-workflow.md).
Run `scripts/analyze-cuts.py`, `scripts/build-fcpxml.py`,
`scripts/conform-vertical.sh`, `scripts/fetch-hitpaw-result.sh`, `scripts/inspect-video.sh`,
`scripts/install.sh`, `scripts/package-deliverables.sh`, and
`scripts/verify-video.sh`.
""",
        )
        self.write(
            "agents/openai.yaml",
            'interface:\n  default_prompt: "Use $video-hardsub-fcp for this workflow."\n',
        )
        self.write("references/hitpaw-workflow.md", "# HitPaw workflow\n")
        for name in REQUIRED_SCRIPTS:
            path = self.write(f"scripts/{name}", "#!/usr/bin/env python3\n")
            path.chmod(0o755)

    def write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_bytes(self, relative: str, content: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def remove(self, relative: str) -> None:
        (self.root / relative).unlink()


class ValidateSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = SkillFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_validator(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def initialize_git_repository(self) -> None:
        result = subprocess.run(
            ["git", "init", "--quiet", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def assert_validation_fails(self, expected: str) -> None:
        result = self.run_validator()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(expected, result.stderr)

    def test_valid_skill_passes(self) -> None:
        result = self.run_validator()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "Skill validation passed")

    def test_requires_frontmatter_name(self) -> None:
        skill = (self.root / "SKILL.md").read_text(encoding="utf-8")
        self.fixture.write("SKILL.md", skill.replace("name: video-hardsub-fcp\n", ""))
        self.assert_validation_fails("frontmatter field 'name'")

    def test_requires_frontmatter_description(self) -> None:
        skill = (self.root / "SKILL.md").read_text(encoding="utf-8")
        self.fixture.write("SKILL.md", skill.replace(skill.splitlines()[2] + "\n", ""))
        self.assert_validation_fails("frontmatter field 'description'")

    def test_requires_exact_skill_name(self) -> None:
        skill = (self.root / "SKILL.md").read_text(encoding="utf-8")
        self.fixture.write(
            "SKILL.md", skill.replace("name: video-hardsub-fcp", "name: video-hardsub-fcp-v2")
        )
        self.assert_validation_fails("exactly 'video-hardsub-fcp'")

    def test_requires_codex_prompt_token(self) -> None:
        self.fixture.write("agents/openai.yaml", "interface:\n  default_prompt: missing\n")
        self.assert_validation_fails("$video-hardsub-fcp")

    def test_requires_skill_markdown_to_be_clean_utf8_text(self) -> None:
        original = (self.root / "SKILL.md").read_bytes()
        for payload in (
            b"\xff\xfe\xfd",
            b"---\nname: video-hardsub-fcp\n---\n\0",
            b"a" * 9000 + b"\0",
        ):
            with self.subTest(payload=payload):
                self.fixture.write_bytes("SKILL.md", payload)
                self.assert_validation_fails("SKILL.md must be UTF-8 text without NUL bytes")
                self.fixture.write_bytes("SKILL.md", original)

    def test_requires_openai_metadata_to_be_clean_utf8_text(self) -> None:
        original = (self.root / "agents/openai.yaml").read_bytes()
        for payload in (b"\xff\xfe\xfd", b"interface:\0 invalid\n", b"a" * 9000 + b"\0"):
            with self.subTest(payload=payload):
                self.fixture.write_bytes("agents/openai.yaml", payload)
                self.assert_validation_fails(
                    "agents/openai.yaml must be UTF-8 text without NUL bytes"
                )
                self.fixture.write_bytes("agents/openai.yaml", original)

    def test_requires_every_declared_reference(self) -> None:
        self.fixture.remove("references/hitpaw-workflow.md")
        self.assert_validation_fails("references/hitpaw-workflow.md")

    def test_requires_every_workflow_script(self) -> None:
        self.fixture.remove("scripts/analyze-cuts.py")
        self.assert_validation_fails("scripts/analyze-cuts.py")

    def test_requires_executable_entry_points(self) -> None:
        script = self.root / "scripts" / "inspect-video.sh"
        script.chmod(0o644)
        self.assert_validation_fails("not executable")

    def test_rejects_forbidden_repository_files(self) -> None:
        for suffix in (".mp4", ".mov", ".mkv", ".avi", ".log", ".key", ".pem"):
            with self.subTest(suffix=suffix):
                path = self.fixture.write(f"artifacts/leak{suffix}", "not for release\n")
                self.assert_validation_fails(f"artifacts/leak{suffix}")
                path.unlink()

    def test_rejects_archive_and_common_image_artifacts(self) -> None:
        for suffix in (
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
        ):
            with self.subTest(suffix=suffix):
                path = self.fixture.write(f"artifacts/release{suffix}", "not for release\n")
                self.assert_validation_fails(f"artifacts/release{suffix}")
                path.unlink()

    def test_rejects_binary_content_even_with_an_unknown_extension(self) -> None:
        self.fixture.write_bytes("artifacts/opaque.bin", b"\x89binary\0payload")
        self.assert_validation_fails("binary or non-UTF-8 repository file: artifacts/opaque.bin")

    def test_non_git_fallback_scans_media_inside_cache_directories(self) -> None:
        self.fixture.write("__pycache__/private.mp4", "private media placeholder\n")
        self.assert_validation_fails("__pycache__/private.mp4")

    def test_non_git_fallback_scans_signed_urls_inside_cache_directories(self) -> None:
        self.fixture.write(
            ".pytest_cache/secret.txt",
            "https://cdn.example.invalid/result?" + "token=secret\n",
        )
        self.assert_validation_fails("signed URL")

    def test_git_publishable_set_ignores_untracked_ignored_cache(self) -> None:
        self.initialize_git_repository()
        self.fixture.write(".gitignore", "__pycache__/\n")
        self.fixture.write_bytes("__pycache__/local.pyc", b"\0local cache\n")
        result = self.run_validator()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_git_publishable_set_scans_force_added_cache(self) -> None:
        self.initialize_git_repository()
        self.fixture.write(".gitignore", "__pycache__/\n")
        self.fixture.write("__pycache__/private.mp4", "tracked media placeholder\n")
        added = subprocess.run(
            ["git", "-C", str(self.root), "add", "--force", "__pycache__/private.mp4"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assert_validation_fails("__pycache__/private.mp4")

    def test_rejects_signed_urls_case_insensitively(self) -> None:
        for parameter in (
            "token",
            "Signature",
            "EXPIRES",
            "sig",
            "auth_key",
            "X-Amz-Credential",
            "X-Amz-Signature",
            "X-Amz-Security-Token",
            "api_key",
            "OSSAccessKeyId",
        ):
            with self.subTest(parameter=parameter):
                self.fixture.write(
                    "README.md",
                    f"download: https://cdn.example.invalid/result?download=1&{parameter}=secret\n",
                )
                self.assert_validation_fails("signed URL")

    def test_rejects_google_signature_suffix_query_key(self) -> None:
        self.fixture.write(
            "README.md",
            "https://storage.example.invalid/object?" + "X-Goog-Signature=secret\n",
        )
        self.assert_validation_fails("signed URL")

    def test_rejects_short_prefixed_signature_suffix_query_key(self) -> None:
        self.fixture.write(
            "README.md",
            "https://storage.example.invalid/object?download=1&" + "q-signature=secret\n",
        )
        self.assert_validation_fails("signed URL")

    def test_rejects_private_user_paths(self) -> None:
        for private_path in (
            "/" + "Users/alice/Movies/source",
            "/" + "home/alice/videos/source",
            "C:" + r"\Users\alice\Videos\source",
        ):
            with self.subTest(private_path=private_path):
                self.fixture.write("README.md", f"local path: {private_path}\n")
                self.assert_validation_fails("private user path")

    def test_rejects_private_user_paths_in_superpowers_plans(self) -> None:
        private_path = "/" + "Users/alice/private/project"
        self.fixture.write("docs/superpowers/plans/release.md", f"path: {private_path}\n")
        self.assert_validation_fails("private user path")

    def test_rejects_file_and_directory_symlinks(self) -> None:
        target_file = self.fixture.write("safe-target.txt", "target\n")
        file_link = self.root / "file-link.txt"
        file_link.symlink_to(target_file.name)
        self.assert_validation_fails("symlink is not allowed: file-link.txt")
        file_link.unlink()

        target_dir = self.root / "safe-directory"
        target_dir.mkdir()
        directory_link = self.root / "directory-link"
        directory_link.symlink_to(target_dir.name, target_is_directory=True)
        self.assert_validation_fails("symlink is not allowed: directory-link")

    def test_ignores_git_metadata(self) -> None:
        self.fixture.write(".git/objects/leak.mp4", "ignored\n")
        self.fixture.write(
            ".git/config", "https://cdn.example.invalid/result?" + "token=ignored\n"
        )
        result = self.run_validator()
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
