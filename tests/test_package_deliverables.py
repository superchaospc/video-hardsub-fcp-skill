import copy
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile


REPO = Path(__file__).resolve().parents[1]
PACKAGER = REPO / "scripts" / "package-deliverables.sh"
BUILDER = REPO / "scripts" / "build-fcpxml.py"
COLON_PRIVATE_PATH = "review:/" + "Users/alice/private.mp4"


def candidate_plan() -> dict:
    return {
        "version": 1,
        "fps": 10.0,
        "frame_count": 2,
        "duration": 0.2,
        "candidates": [
            {
                "frame": 1,
                "time_seconds": 0.1,
                "confidence": 3.0,
                "evidence": ["scene"],
            }
        ],
        "requires_visual_review": True,
        "selected_frames": [1],
        "rejected_candidates": [],
    }


def zero_cut_plan() -> dict:
    return {
        "version": 1,
        "fps": 10.0,
        "frame_count": 2,
        "duration": 0.2,
        "candidates": [],
        "requires_visual_review": True,
        "selected_frames": [],
        "rejected_candidates": [],
        "review_decision": "no-cuts",
    }


def write_xml(path: Path, root: ET.Element) -> None:
    body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<!DOCTYPE fcpxml>\n{body}\n",
        encoding="utf-8",
    )


def package_fixture(
    root: Path,
    *,
    timeline_plan: dict | None = None,
    packaged_plan: dict | None = None,
    verification: dict | None = None,
    xml_mutator=None,
):
    job = root / "private-job"
    job.mkdir()
    cleaned = job / "original cleaned.mp4"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "color=c=black:s=32x32:r=10:d=0.2", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(cleaned),
        ],
        check=True,
    )
    source = job / "source.mp4"
    source.write_bytes(b"preserved source identity")
    cuts = job / "cut-plan.json"
    timeline = copy.deepcopy(timeline_plan or candidate_plan())
    timeline["source"] = str(cleaned.resolve())
    cuts.write_text(json.dumps(timeline) + "\n", encoding="utf-8")
    fcpxml = job / "project.fcpxml"
    subprocess.run(
        ["python3", str(BUILDER), str(cleaned), str(cuts), "--output", str(fcpxml)],
        check=True,
        capture_output=True,
        text=True,
    )
    if packaged_plan is not None:
        package_plan = copy.deepcopy(packaged_plan)
        package_plan.setdefault("source", str(cleaned.resolve()))
        cuts.write_text(json.dumps(package_plan) + "\n", encoding="utf-8")
    if xml_mutator is not None:
        xml_mutator(fcpxml)
    verification_path = job / "verification.json"
    verification_path.write_text(
        json.dumps(
            verification
            or {"version": 1, "pass": True, "checks": {"decode": {"pass": True}}}
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "source": str(source.resolve()),
                        "status": "complete",
                        "deliverables": {
                            "cleaned": str(cleaned.resolve()),
                            "fcpxml": str(fcpxml.resolve()),
                            "cuts": str(cuts.resolve()),
                            "verification": str(verification_path.resolve()),
                        },
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    archive = root / "delivery.zip"
    completed = subprocess.run(
        ["bash", str(PACKAGER), str(manifest), str(archive)],
        capture_output=True,
        text=True,
    )
    return completed, archive, job


@unittest.skipUnless(
    all(shutil.which(tool) for tool in ("ffmpeg", "ffprobe", "zip", "unzip")),
    "packager test requires ffmpeg, ffprobe, zip, and unzip",
)
class PackageDeliverablesTests(unittest.TestCase):
    def assert_package_rejected(self, **kwargs) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as directory:
            completed, archive, _ = package_fixture(Path(directory), **kwargs)
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertFalse(archive.exists())
            return completed

    def test_archive_preserves_explicit_no_cuts_review_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            completed, archive, _ = package_fixture(
                Path(directory), timeline_plan=zero_cut_plan()
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with ZipFile(archive) as package:
                archived = json.loads(
                    package.read("entry-001/cut-plan.json").decode("utf-8")
                )
            self.assertEqual(archived["review_decision"], "no-cuts")

    def test_archive_rewrites_media_reference_and_removes_absolute_work_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            completed, archive, job = package_fixture(root)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            unavailable = root / "private-job-unavailable"
            job.rename(unavailable)
            extracted = root / "isolated-delivery"
            with ZipFile(archive) as package:
                package.extractall(extracted)

            entry = extracted / "entry-001"
            archived_xml = entry / "project.fcpxml"
            media_rep = ET.parse(archived_xml).find("./resources/asset/media-rep")
            self.assertIsNotNone(media_rep)
            uri = media_rep.attrib["src"]
            parsed = urlsplit(uri)
            self.assertEqual(parsed.scheme, "")
            self.assertEqual(parsed.netloc, "")
            resolved_media = (archived_xml.parent / unquote(parsed.path)).resolve()
            self.assertEqual(resolved_media, (entry / "cleaned.mp4").resolve())
            self.assertTrue(resolved_media.is_file())

            archived_plan = json.loads((entry / "cut-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(archived_plan["source"], "cleaned.mp4")
            self.assertEqual(archived_plan["selected_frames"], [1])
            self.assertEqual(archived_plan["candidates"][0]["frame"], 1)
            private_root = str(root.resolve())
            for relative in (
                "manifest.json", "entry-001/project.fcpxml",
                "entry-001/cut-plan.json", "entry-001/verification.json",
            ):
                self.assertNotIn(
                    private_root,
                    (extracted / relative).read_text(encoding="utf-8"),
                    relative,
                )
            for row in (extracted / "SHA256SUMS").read_text(encoding="ascii").splitlines():
                expected, relative = row.split("  ", 1)
                actual = hashlib.sha256((extracted / relative).read_bytes()).hexdigest()
                self.assertEqual(actual, expected, relative)

    def test_rejects_strict_cut_plan_schema_and_semantic_violations(self):
        invalid = []
        extra_root = candidate_plan()
        extra_root["working_directory"] = "/opt/package-secret/private-job"
        invalid.append(extra_root)
        extra_candidate = candidate_plan()
        extra_candidate["candidates"][0]["review_path"] = "review.png"
        invalid.append(extra_candidate)
        nonfinite = candidate_plan()
        nonfinite["candidates"][0]["confidence"] = math.nan
        invalid.append(nonfinite)
        infinite = candidate_plan()
        infinite["duration"] = math.inf
        invalid.append(infinite)
        unknown_evidence = candidate_plan()
        unknown_evidence["candidates"][0]["evidence"] = ["optical-flow"]
        invalid.append(unknown_evidence)
        out_of_range = candidate_plan()
        out_of_range["candidates"][0]["frame"] = 2
        invalid.append(out_of_range)
        incomplete = candidate_plan()
        incomplete["selected_frames"] = []
        invalid.append(incomplete)
        bad_reason = candidate_plan()
        bad_reason["selected_frames"] = []
        bad_reason["rejected_candidates"] = [{"frame": 1, "reason": "maybe"}]
        invalid.append(bad_reason)
        missing_no_cuts = zero_cut_plan()
        del missing_no_cuts["review_decision"]
        invalid.append(missing_no_cuts)
        invalid_no_cuts = candidate_plan()
        invalid_no_cuts["review_decision"] = "no-cuts"
        invalid.append(invalid_no_cuts)

        for index, plan in enumerate(invalid):
            with self.subTest(index=index):
                self.assert_package_rejected(packaged_plan=plan)

    def test_rejects_embedded_paths_in_cut_plan_and_nested_verification(self):
        plan = candidate_plan()
        plan["source"] = "redacted prefix /opt/package-secret/private.mp4 suffix"
        completed = self.assert_package_rejected(packaged_plan=plan)
        self.assertIn("absolute local path", completed.stderr)
        completed = self.assert_package_rejected(
            verification={
                "version": 1,
                "pass": True,
                "checks": {
                    "decode": {
                        "pass": True,
                        "values": {"note": r"prefix C:\\Users\\alice\\secret suffix"},
                    }
                },
            }
        )
        self.assertIn("absolute local path", completed.stderr)

        completed = self.assert_package_rejected(
            verification={
                "version": 1,
                "pass": True,
                "checks": {
                    "decode": {
                        "pass": True,
                        "values": {"note": COLON_PRIVATE_PATH},
                    }
                },
            }
        )
        self.assertIn("absolute local path", completed.stderr)

    def test_rejects_embedded_paths_in_every_xml_text_location(self):
        def replace(old, new):
            def mutate(path):
                text = path.read_text(encoding="utf-8")
                path.write_text(text.replace(old, new, 1), encoding="utf-8")
            return mutate

        cases = {
            "attribute-windows": replace(
                '<asset id="r2"',
                '<asset archiveNote="prefix C:\\Users\\alice\\secret suffix" id="r2"',
            ),
            "text-unc": replace(
                "<resources>",
                r"<resources><note>prefix \\server\share\secret suffix</note>",
            ),
            "comment-file-uri": replace(
                "<resources>",
                "<!-- prefix file:///opt/package-secret suffix --><resources>",
            ),
            "tail-unix": replace(
                " /><asset",
                " />prefix /opt/package-secret suffix<asset",
            ),
            "attribute-colon-unix": replace(
                '<asset id="r2"',
                f'<asset archiveNote="{COLON_PRIVATE_PATH}" id="r2"',
            ),
        }
        for name, mutator in cases.items():
            with self.subTest(name=name):
                completed = self.assert_package_rejected(xml_mutator=mutator)
                self.assertIn("absolute local path", completed.stderr)

    def test_colon_delimited_url_is_not_mistaken_for_a_local_path(self):
        def add_url_attribute(path):
            text = path.read_text(encoding="utf-8")
            path.write_text(
                text.replace(
                    '<asset id="r2"',
                    '<asset archiveNote="review:https://example.com/media.mp4" id="r2"',
                    1,
                ),
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as directory:
            completed, archive, _ = package_fixture(
                Path(directory),
                verification={
                    "version": 1,
                    "pass": True,
                    "checks": {
                        "decode": {
                            "pass": True,
                            "values": {"note": "review:https://example.com/media.mp4"},
                        }
                    },
                },
                xml_mutator=add_url_attribute,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(archive.is_file())

    def test_rejects_fcpxml_version_asset_reference_and_time_mismatches(self):
        def mutate_tree(callback):
            def mutate(path):
                root = ET.parse(path).getroot()
                callback(root)
                write_xml(path, root)
            return mutate

        def add_asset(root):
            resources = root.find("./resources")
            resources.append(copy.deepcopy(resources.find("asset")))

        def add_nested_wrong_ref(root):
            primary = root.find("./library/event/project/sequence/spine/asset-clip")
            nested = copy.deepcopy(primary)
            nested.set("ref", "missing")
            primary.append(nested)

        def add_nested_clip(root):
            primary = root.find("./library/event/project/sequence/spine/asset-clip")
            primary.append(copy.deepcopy(primary))

        cases = {
            "wrong-version": mutate_tree(lambda root: root.set("version", "1.9")),
            "multiple-assets": mutate_tree(add_asset),
            "wrong-ref": mutate_tree(
                lambda root: root.find(
                    "./library/event/project/sequence/spine/asset-clip"
                ).set("ref", "missing")
            ),
            "nested-wrong-ref": mutate_tree(add_nested_wrong_ref),
            "unexpected-nested-clip": mutate_tree(add_nested_clip),
            "wrong-duration": mutate_tree(
                lambda root: root.find(
                    "./library/event/project/sequence/spine/asset-clip"
                ).set("duration", "1/20s")
            ),
        }
        for name, mutator in cases.items():
            with self.subTest(name=name):
                self.assert_package_rejected(xml_mutator=mutator)


if __name__ == "__main__":
    unittest.main()
