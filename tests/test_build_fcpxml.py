import dataclasses
import contextlib
import errno
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build-fcpxml.py"
SPEC = importlib.util.spec_from_file_location("build_fcpxml", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

ANALYZER_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analyze-cuts.py"
ANALYZER_SPEC = importlib.util.spec_from_file_location("analyze_cuts_for_fcpxml", ANALYZER_SCRIPT)
ANALYZER = importlib.util.module_from_spec(ANALYZER_SPEC)
assert ANALYZER_SPEC.loader is not None
sys.modules[ANALYZER_SPEC.name] = ANALYZER
ANALYZER_SPEC.loader.exec_module(ANALYZER)


def parse_fcpx_time(value: str) -> Fraction:
    if not value.endswith("s"):
        raise ValueError(value)
    return Fraction(value[:-1])


def make_info(**overrides):
    values = {
        "width": 1920,
        "height": 1080,
        "fps": Fraction(30, 1),
        "frame_count": 300,
        "duration": Fraction(10, 1),
        "has_audio": True,
    }
    values.update(overrides)
    return MODULE.MediaInfo(**values)


def make_plan(source="cleaned.mp4", candidates=(75, 150), selected=(75,), rejected=(150,)):
    return {
        "version": 1,
        "source": source,
        "fps": 30.0,
        "frame_count": 300,
        "duration": 10.0,
        "candidates": [
            {
                "frame": frame,
                "time_seconds": frame / 30,
                "confidence": 1.0,
                "evidence": ["ydif"],
            }
            for frame in candidates
        ],
        "requires_visual_review": True,
        "selected_frames": list(selected),
        "rejected_candidates": [
            {"frame": frame, "reason": "no-visible-discontinuity"}
            for frame in rejected
        ],
    }


class RationalTimelineTests(unittest.TestCase):
    def test_media_info_public_contract_has_exactly_six_fields(self):
        self.assertEqual(
            [field.name for field in dataclasses.fields(MODULE.MediaInfo)],
            ["width", "height", "fps", "frame_count", "duration", "has_audio"],
        )

    def test_six_positional_argument_media_info_preserves_audio(self):
        info = MODULE.MediaInfo(
            1080, 1920, Fraction(30, 1), 300, Fraction(10, 1), True
        )
        root = ET.fromstring(
            MODULE.build_fcpxml(Path("cleaned.mp4"), info, [], "Public API")
        )
        asset = root.find("./resources/asset")
        self.assertEqual(asset.attrib["hasAudio"], "1")
        self.assertNotIn("audioSources", asset.attrib)
        self.assertNotIn("audioChannels", asset.attrib)
        self.assertNotIn("audioRate", asset.attrib)

    def test_project_is_vertical_1080x1920_and_asset_keeps_media_format(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        source = Path(directory.name) / "cleaned.mp4"
        source.write_bytes(b"media")
        for width, height in ((1920, 1080), (720, 1280), (608, 1080)):
            with self.subTest(media=f"{width}x{height}"):
                info = make_info(width=width, height=height)
                xml = MODULE.build_fcpxml(source, info, [75], "Vertical")
                root = ET.fromstring(xml)
                formats = {
                    item.attrib["id"]: item for item in root.findall("./resources/format")
                }
                sequence = root.find("./library/event/project/sequence")
                asset = root.find("./resources/asset")
                project = formats[sequence.attrib["format"]]
                media = formats[asset.attrib["format"]]
                self.assertEqual((project.attrib["width"], project.attrib["height"]), ("1080", "1920"))
                self.assertEqual((media.attrib["width"], media.attrib["height"]), (str(width), str(height)))
                MODULE.verify_fcpxml(xml, source, info, [75])

    def test_1080x1920_media_shares_the_project_format(self):
        info = make_info(width=1080, height=1920)
        xml = MODULE.build_fcpxml(Path("cleaned.mp4"), info, [75], "Vertical")
        root = ET.fromstring(xml)
        self.assertEqual(len(root.findall("./resources/format")), 1)
        self.assertEqual(root.find("./resources/asset").attrib["format"], "r1")
        self.assertEqual(
            root.find("./library/event/project/sequence").attrib["format"], "r1"
        )

    def test_verifier_rejects_non_vertical_project_format(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "cleaned.mp4"
            source.write_bytes(b"media")
            base = MODULE.build_fcpxml(source, make_info(), [75], "Review")
            mutations = (
                base.replace('width="1080" height="1920"', 'width="1920" height="1080"', 1),
                base.replace('<sequence format="r1"', '<sequence format="r3"', 1),
            )
            for xml in mutations:
                self.assertNotEqual(xml, base)
                with self.subTest(xml=xml[:200]), self.assertRaises(ValueError):
                    MODULE.verify_fcpxml(xml, source, make_info(), [75])

    def test_30fps_timeline_is_contiguous_and_covers_every_frame(self):
        xml = MODULE.build_fcpxml(
            Path("cleaned.mp4"), make_info(), [75, 150, 221], "Reviewed cuts"
        )
        root = ET.fromstring(xml)
        clips = root.findall("./library/event/project/sequence/spine/asset-clip")

        self.assertEqual(root.attrib["version"], "1.10")
        self.assertEqual(len(clips), 4)
        self.assertEqual(
            sum((parse_fcpx_time(c.attrib["duration"]) for c in clips), Fraction()),
            Fraction(10, 1),
        )
        expected = ["0s", "5/2s", "5s", "221/30s"]
        self.assertEqual([c.attrib["offset"] for c in clips], expected)
        self.assertEqual([c.attrib["start"] for c in clips], expected)

    def test_ntsc_frame_duration_and_times_are_exact_reduced_rationals(self):
        fps = Fraction(30000, 1001)
        self.assertEqual(MODULE.frames_to_time(1, fps), "1001/30000s")
        self.assertEqual(MODULE.frames_to_time(15000, fps), "1001/2s")
        self.assertEqual(MODULE.frames_to_time(0, fps), "0s")
        xml = MODULE.build_fcpxml(
            Path("ntsc.mp4"),
            make_info(
                fps=fps,
                frame_count=30000,
                duration=Fraction(1001, 1),
                has_audio=False,
            ),
            [1],
            "NTSC",
        )
        root = ET.fromstring(xml)
        self.assertEqual(root.find("./resources/format").attrib["frameDuration"], "1001/30000s")

    def test_media_uri_percent_encodes_spaces_ampersand_and_non_ascii(self):
        source = Path("媒体 & cleaned clip.mp4")
        xml = MODULE.build_fcpxml(source, make_info(), [], "事件 & review")
        root = ET.fromstring(xml)
        uri = root.find("./resources/asset/media-rep").attrib["src"]
        self.assertTrue(uri.startswith("file://"))
        self.assertIn("%20", uri)
        self.assertIn("%26", uri)
        self.assertIn("%E5%AA%92%E4%BD%93", uri)
        self.assertNotIn("媒体", uri)

    def test_public_asset_preserves_audio_reference_without_inventing_probe_metadata(self):
        root = ET.fromstring(MODULE.build_fcpxml(Path("a.mp4"), make_info(), [], "Audio"))
        asset = root.find("./resources/asset")
        clip = root.find("./library/event/project/sequence/spine/asset-clip")
        self.assertEqual(asset.attrib["hasAudio"], "1")
        self.assertNotIn("audioSources", asset.attrib)
        self.assertNotIn("audioChannels", asset.attrib)
        self.assertNotIn("audioRate", asset.attrib)
        self.assertEqual(clip.attrib["ref"], asset.attrib["id"])

    def test_probed_audio_uses_raw_asset_rate_and_sequence_enum(self):
        info = make_info()
        probe = MODULE._ProbeDetails(
            info, audio_channels=3, audio_sample_rate=44100, audio_sources=2
        )
        root = ET.fromstring(
            MODULE._build_fcpxml(Path("a.mp4"), info, [], "Audio", probe)
        )
        asset = root.find("./resources/asset")
        sequence = root.find("./library/event/project/sequence")
        self.assertEqual(asset.attrib["audioSources"], "2")
        self.assertEqual(asset.attrib["audioChannels"], "3")
        self.assertEqual(asset.attrib["audioRate"], "44100")
        self.assertEqual(sequence.attrib["audioRate"], "44.1k")

    def test_video_only_asset_omits_audio_metadata(self):
        info = make_info(has_audio=False)
        asset = ET.fromstring(MODULE.build_fcpxml(Path("a.mp4"), info, [], "Silent")).find(
            "./resources/asset"
        )
        self.assertNotIn("hasAudio", asset.attrib)
        self.assertNotIn("audioSources", asset.attrib)
        self.assertNotIn("audioChannels", asset.attrib)
        self.assertNotIn("audioRate", asset.attrib)

    def test_invalid_cut_lists_are_rejected(self):
        invalid = ([75, 75], [150, 75], [0], [-1], [300], [301], [1.5], [True])
        for cuts in invalid:
            with self.subTest(cuts=cuts), self.assertRaises(ValueError):
                MODULE.validate_cuts(cuts, 300)


class ReviewPlanTests(unittest.TestCase):
    def test_analyzer_generated_ntsc_float_plan_builds_exact_xml(self):
        fps = Fraction(30000, 1001)
        candidate_frame = 101
        plan = ANALYZER.make_plan(
            "cleaned.mp4",
            float(fps),
            300,
            [
                ANALYZER.Candidate(
                    candidate_frame,
                    candidate_frame / float(fps),
                    4.25,
                    ["keyframe", "ydif"],
                )
            ],
        )
        plan = json.loads(json.dumps(plan))
        plan["selected_frames"] = [candidate_frame]
        info = make_info(fps=fps, duration=Fraction(1001, 100))
        cuts = MODULE.validate_plan(plan, Path("cleaned.mp4"), info)
        xml = MODULE.build_fcpxml(Path("cleaned.mp4"), info, cuts, "NTSC review")
        clips = ET.fromstring(xml).findall(
            "./library/event/project/sequence/spine/asset-clip"
        )
        self.assertEqual(cuts, [candidate_frame])
        self.assertEqual(clips[0].attrib["duration"], "101101/30000s")
        self.assertEqual(clips[1].attrib["offset"], "101101/30000s")

    def test_plan_root_and_candidate_records_must_have_versioned_audit_shape(self):
        with self.assertRaises(ValueError):
            MODULE.validate_plan([], Path("cleaned.mp4"), make_info())
        for candidate in (75, {}, {"frame": True}):
            plan = make_plan()
            plan["candidates"] = [candidate]
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                MODULE.validate_plan(plan, Path("cleaned.mp4"), make_info())

    def test_complete_review_returns_selected_frames(self):
        self.assertEqual(
            MODULE.validate_plan(make_plan(), Path("cleaned.mp4"), make_info()), [75]
        )

    def test_incomplete_overlapping_extra_and_duplicate_decisions_are_rejected(self):
        variants = []
        variants.append(make_plan(rejected=()))
        variants.append(make_plan(selected=(75, 150), rejected=(150,)))
        variants.append(make_plan(selected=(75, 99), rejected=(150,)))
        variants.append(make_plan(selected=(75, 75), rejected=(150,)))
        variants.append(make_plan(candidates=(75, 75), selected=(75,), rejected=()))
        for plan in variants:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                MODULE.validate_plan(plan, Path("cleaned.mp4"), make_info())

    def test_malformed_rejection_records_and_reasons_are_rejected(self):
        for rejected in (
            [150],
            [{"frame": 150}],
            [{"frame": 150, "reason": "looks-bad"}],
            [{"frame": "150", "reason": "camera-shake"}],
            [{"frame": 150, "reason": ""}],
            [{"frame": 150, "reason": []}],
        ):
            plan = make_plan()
            plan["rejected_candidates"] = rejected
            with self.subTest(rejected=rejected), self.assertRaises(ValueError):
                MODULE.validate_plan(plan, Path("cleaned.mp4"), make_info())

    def test_requires_visual_review_rejects_incomplete_decisions(self):
        plan = make_plan(selected=(), rejected=())
        self.assertTrue(plan["requires_visual_review"])
        with self.assertRaisesRegex(ValueError, "exactly once"):
            MODULE.validate_plan(plan, Path("cleaned.mp4"), make_info())

    def test_requires_visual_review_rejects_all_candidates_rejected(self):
        plan = make_plan(selected=(), rejected=(75, 150))
        with self.assertRaisesRegex(ValueError, "select at least one"):
            MODULE.validate_plan(plan, Path("cleaned.mp4"), make_info())

    def test_genuine_zero_candidates_requires_explicit_no_cuts_decision(self):
        plan = make_plan(candidates=(), selected=(), rejected=())
        with self.assertRaises(ValueError):
            MODULE.validate_plan(plan, Path("cleaned.mp4"), make_info())
        plan["review_decision"] = "no-cuts"
        self.assertEqual(MODULE.validate_plan(plan, Path("cleaned.mp4"), make_info()), [])

        plan["selected_frames"] = [1]
        with self.assertRaises(ValueError):
            MODULE.validate_plan(plan, Path("cleaned.mp4"), make_info())

    def test_no_cuts_decision_is_invalid_when_candidates_exist(self):
        plan = make_plan()
        plan["review_decision"] = "no-cuts"
        with self.assertRaises(ValueError):
            MODULE.validate_plan(plan, Path("cleaned.mp4"), make_info())

    def test_source_basename_mismatch_requires_opt_in(self):
        plan = make_plan(source="original.mp4")
        with self.assertRaisesRegex(ValueError, "basename"):
            MODULE.validate_plan(plan, Path("cleaned.mp4"), make_info())
        self.assertEqual(
            MODULE.validate_plan(
                plan, Path("cleaned.mp4"), make_info(), accept_renamed_media=True
            ),
            [75],
        )

    def test_plan_version_fps_and_frame_count_must_match_probe(self):
        mutations = (
            ("version", 2),
            ("version", True),
            ("fps", 29.97),
            ("frame_count", 301),
        )
        for key, value in mutations:
            plan = make_plan()
            plan[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                MODULE.validate_plan(plan, Path("cleaned.mp4"), make_info())

    def test_candidate_audit_fields_and_plan_duration_are_strictly_validated(self):
        invalid_changes = (
            ("time_seconds", True),
            ("time_seconds", "2.5"),
            ("time_seconds", 99.0),
            ("confidence", True),
            ("confidence", "1.0"),
            ("confidence", float("inf")),
            ("confidence", -1),
            ("evidence", "ydif"),
            ("evidence", []),
            ("evidence", [1]),
            ("evidence", ["unknown"]),
        )
        for key, value in invalid_changes:
            plan = make_plan()
            plan["candidates"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                MODULE.validate_plan(plan, Path("cleaned.mp4"), make_info())
        for duration in (True, "10.0", [], -1, 10.5):
            plan = make_plan()
            plan["duration"] = duration
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                MODULE.validate_plan(plan, Path("cleaned.mp4"), make_info())

    def test_huge_rational_plan_fps_is_rejected_before_float_conversion(self):
        plan = make_plan()
        plan["fps"] = f"{10**100}/1"
        with self.assertRaisesRegex(ValueError, "too large"):
            MODULE.validate_plan(plan, Path("cleaned.mp4"), make_info())


class ProbeTests(unittest.TestCase):
    def payload(
        self,
        *,
        audio=True,
        audio_streams=None,
        format_duration="10.01",
        **video_changes,
    ):
        video = {
            "codec_type": "video",
            "width": 1920,
            "height": 1080,
            "avg_frame_rate": "30000/1001",
            "r_frame_rate": "30000/1001",
            "nb_read_frames": "300",
            "nb_frames": "300",
            "duration": "10.01",
            "start_time": "0",
            "time_base": "1/30000",
        }
        video.update(video_changes)
        streams = [video]
        if audio_streams is not None:
            streams.extend(audio_streams)
        elif audio:
            streams.append(
                {
                    "codec_type": "audio",
                    "channels": 2,
                    "sample_rate": "48000",
                    "start_time": "0",
                }
            )
        return json.dumps({"streams": streams, "format": {"duration": format_duration}})

    def test_ffprobe_parser_reads_exact_video_and_audio_metadata(self):
        probe = MODULE.parse_ffprobe_json(self.payload())
        info = probe.media
        self.assertEqual(info.width, 1920)
        self.assertEqual(info.height, 1080)
        self.assertEqual(info.fps, Fraction(30000, 1001))
        self.assertEqual(info.frame_count, 300)
        self.assertEqual(info.duration, Fraction("10.01"))
        self.assertTrue(info.has_audio)
        self.assertEqual(probe.audio_channels, 2)
        self.assertEqual(probe.audio_sample_rate, 48000)

    def test_ffprobe_parser_supports_video_without_audio(self):
        probe = MODULE.parse_ffprobe_json(self.payload(audio=False))
        info = probe.media
        self.assertFalse(info.has_audio)
        self.assertEqual(probe.audio_channels, 0)
        self.assertEqual(probe.audio_sample_rate, 0)
        self.assertEqual(probe.audio_sources, 0)

    def test_ffprobe_parser_rejects_vfr_unknown_and_zero_metadata(self):
        invalid_payloads = (
            self.payload(r_frame_rate="30/1"),
            self.payload(avg_frame_rate="0/0"),
            self.payload(nb_read_frames="N/A", nb_frames="N/A"),
            self.payload(width=0),
            self.payload(width=1920.5),
            self.payload(duration="0", format_duration="0"),
            "not json",
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                MODULE.parse_ffprobe_json(payload)

    def test_multiple_audio_streams_preserve_source_count_channels_and_rate(self):
        streams = [
            {"codec_type": "audio", "channels": 1, "sample_rate": "44100", "start_time": "0"},
            {"codec_type": "audio", "channels": 2, "sample_rate": "44100", "start_time": "0"},
        ]
        probe = MODULE.parse_ffprobe_json(self.payload(audio_streams=streams))
        self.assertEqual(probe.audio_sources, 2)
        self.assertEqual(probe.audio_channels, 3)
        self.assertEqual(probe.audio_sample_rate, 44100)

    def test_nonzero_video_or_audio_start_requires_timestamp_normalization(self):
        payloads = (
            self.payload(start_time="1/10"),
            self.payload(
                audio_streams=[
                    {
                        "codec_type": "audio",
                        "channels": 1,
                        "sample_rate": "48000",
                        "start_time": "1/100",
                    }
                ]
            ),
        )
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaisesRegex(
                ValueError, "normalize timestamps"
            ):
                MODULE.parse_ffprobe_json(payload)

    def test_integer_pts_reject_irregular_grid_and_wrong_count(self):
        regular = ["0", "1", "2", "3"]
        MODULE.validate_cfr_timestamps(regular, Fraction(30), 4, Fraction(1, 30))
        with self.assertRaisesRegex(ValueError, "variable-frame-rate"):
            MODULE.validate_cfr_timestamps(["0", "1", "3", "4"], Fraction(30), 4, Fraction(1, 30))
        with self.assertRaisesRegex(ValueError, "frame count"):
            MODULE.validate_cfr_timestamps(regular[:-1], Fraction(30), 4, Fraction(1, 30))

    def test_ntsc_coarse_integer_pts_quantization_is_accepted(self):
        pts = [str(round(index * 1001 / 30)) for index in range(300)]
        count = MODULE.validate_cfr_timestamps(
            pts, Fraction(30000, 1001), 300, Fraction(1, 1000)
        )
        self.assertEqual(count, 300)

    def test_timestamp_count_supplies_frame_count_when_headers_are_na(self):
        payload = self.payload(nb_read_frames="N/A", nb_frames="N/A")
        pts = [str(index * 1001) for index in range(300)]
        probe = MODULE.parse_ffprobe_json(payload, pts)
        self.assertEqual(probe.media.frame_count, 300)

    def test_malformed_format_shape_and_huge_fps_are_clean_value_errors(self):
        malformed = json.loads(self.payload())
        malformed["format"] = []
        with self.assertRaises(ValueError):
            MODULE.parse_ffprobe_json(json.dumps(malformed))
        with self.assertRaisesRegex(ValueError, "too large"):
            MODULE.parse_ffprobe_json(self.payload(avg_frame_rate=f"{10**100}/1"))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
    def test_real_nonzero_video_start_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "nonzero.mkv"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=red:s=64x64:r=10:d=1",
                    "-vf",
                    "setpts=PTS+1/TB",
                    "-copyts",
                    "-c:v",
                    "mpeg4",
                    str(source),
                ],
                check=True,
            )
            with self.assertRaisesRegex(ValueError, "normalize timestamps"):
                MODULE.probe_media(source)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
    def test_real_ntsc_coarse_timestamp_quantization_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ntsc.mkv"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=green:s=64x64:r=30000/1001:d=1",
                    "-c:v",
                    "mpeg4",
                    str(source),
                ],
                check=True,
            )
            probe = MODULE.probe_media(source)
            self.assertEqual(probe.media.fps, Fraction(30000, 1001))
            self.assertEqual(probe.media.frame_count, 30)
            self.assertEqual(probe.media.duration, Fraction(1001, 1000))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
    def test_real_multiple_audio_streams_and_44100_rate_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "multiple-audio.mkv"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=64x64:r=10:d=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=44100:duration=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=880:sample_rate=44100:duration=1",
                    "-map",
                    "0:v",
                    "-map",
                    "1:a",
                    "-map",
                    "2:a",
                    "-c:v",
                    "mpeg4",
                    "-c:a",
                    "pcm_s16le",
                    str(source),
                ],
                check=True,
            )
            probe = MODULE.probe_media(source)
            self.assertEqual(probe.audio_sources, 2)
            self.assertEqual(probe.audio_channels, 2)
            self.assertEqual(probe.audio_sample_rate, 44100)


class OutputSafetyAndVerificationTests(unittest.TestCase):
    def test_xml_has_doctype_and_verifies_all_invariants(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "cleaned.mp4"
            source.write_bytes(b"media")
            xml = MODULE.build_fcpxml(source, make_info(), [75, 150, 221], "Review")
            self.assertIn("<!DOCTYPE fcpxml>", xml)
            MODULE.verify_fcpxml(xml, source, make_info(), [75, 150, 221])

    def test_verifier_rejects_bad_root_gaps_zero_duration_and_audio_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "cleaned.mp4"
            source.write_bytes(b"media")
            base = MODULE.build_fcpxml(source, make_info(), [75], "Review")
            mutations = (
                base.replace('version="1.10"', 'version="1.9"', 1),
                base.replace('duration="10s"', 'duration="9s"', 1),
                base.replace('offset="5/2s"', 'offset="8/3s"', 1),
                base.replace('duration="5/2s"', 'duration="0s"', 1),
                base.replace("<!DOCTYPE fcpxml>", ""),
            )
            for xml in mutations:
                with self.subTest(xml=xml[:100]), self.assertRaises(ValueError):
                    MODULE.verify_fcpxml(xml, source, make_info(), [75])

    def test_direct_symlink_and_hardlink_output_aliases_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "cleaned.mp4"
            source.write_bytes(b"media")
            symlink = Path(directory) / "symlink.fcpxml"
            symlink.symlink_to(source)
            hardlink = Path(directory) / "hardlink.fcpxml"
            os.link(source, hardlink)
            for output in (source, symlink, hardlink):
                with self.subTest(output=output), self.assertRaises(ValueError):
                    MODULE.ensure_distinct_paths(source, output)

    def test_atomic_publication_never_overwrites_racing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "project.fcpxml"
            real_rename = MODULE._rename_noreplace_syscall
            raced = False

            def inject_race(source, destination):
                nonlocal raced
                if Path(destination) == output and not raced:
                    raced = True
                    output.write_text("competitor", encoding="utf-8")
                return real_rename(source, destination)

            with mock.patch.object(
                MODULE, "_rename_noreplace_syscall", side_effect=inject_race
            ):
                with self.assertRaisesRegex(ValueError, "retained"):
                    MODULE.atomic_write_noreplace(output, "ours")
            self.assertTrue(raced)
            self.assertEqual(output.read_text(encoding="utf-8"), "competitor")
            retained_stages = list(output.parent.glob(f".{output.name}.*.tmp"))
            self.assertEqual(len(retained_stages), 1)
            self.assertEqual(retained_stages[0].read_text(encoding="utf-8"), "ours")

    def test_atomic_publication_does_not_create_a_missing_parent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "missing"
            with self.assertRaises(FileNotFoundError):
                MODULE.atomic_write_noreplace(parent / "project.fcpxml", "ours")
            self.assertFalse(parent.exists())

    def test_parent_fsync_failure_quarantines_public_and_retains_owned_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "project.fcpxml"
            real_fsync = os.fsync
            calls = 0

            def fail_parent_fsync(descriptor):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected parent fsync failure")
                return real_fsync(descriptor)

            with mock.patch.object(MODULE.os, "fsync", side_effect=fail_parent_fsync):
                with self.assertRaisesRegex(ValueError, "recovery"):
                    MODULE._publish_and_verify(output, "ours", lambda written: None)
            self.assertFalse(output.exists())
            retained = [path for path in root.iterdir() if path.is_file()]
            self.assertGreaterEqual(len(retained), 2)
            self.assertEqual({path.read_text() for path in retained}, {"ours"})

    def test_readback_and_verification_failures_retain_recovery_artifacts(self):
        for failure in (OSError("read failed"), ValueError("verification failed")):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / "project.fcpxml"

                def verify(written):
                    if isinstance(failure, ValueError):
                        raise failure

                read_patch = (
                    mock.patch.object(MODULE.Path, "read_text", side_effect=failure)
                    if isinstance(failure, OSError)
                    else contextlib.nullcontext()
                )
                with read_patch, self.assertRaisesRegex(ValueError, "recovery"):
                    MODULE._publish_and_verify(output, "ours", verify)
                self.assertFalse(output.exists())
                retained = [path for path in root.iterdir() if path.is_file()]
                self.assertGreaterEqual(len(retained), 2)

    def test_concurrent_substitution_is_moved_whole_to_recovery_without_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "project.fcpxml"
            external = root / "external.txt"
            external.write_text("external", encoding="utf-8")

            def substitute_then_fail(written):
                self.assertEqual(written, "ours")
                output.unlink()
                external.rename(output)
                raise ValueError("verification failed")

            with self.assertRaisesRegex(ValueError, "recovery"):
                MODULE._publish_and_verify(output, "ours", substitute_then_fail)
            self.assertFalse(output.exists())
            contents = {
                path.read_text(encoding="utf-8")
                for path in root.iterdir()
                if path.is_file()
            }
            self.assertEqual(contents, {"ours", "external"})

    def test_stage_substitution_on_rename_failure_preserves_external_and_owned_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "project.fcpxml"
            displaced_owned = root / "displaced-owned.xml"
            former_stage = None

            def fail_after_stage_substitution(source, destination):
                nonlocal former_stage
                if Path(destination) == output:
                    former_stage = Path(source)
                    former_stage.rename(displaced_owned)
                    former_stage.write_text("external-stage", encoding="utf-8")
                    raise OSError(errno.EEXIST, "injected collision")
                return MODULE._rename_noreplace_syscall(source, destination)

            with mock.patch.object(
                MODULE,
                "_rename_noreplace_syscall",
                side_effect=fail_after_stage_substitution,
            ), self.assertRaisesRegex(ValueError, "owned recovery"):
                MODULE.atomic_write_noreplace(output, "ours")
            self.assertIsNotNone(former_stage)
            self.assertEqual(former_stage.read_text(encoding="utf-8"), "external-stage")
            self.assertEqual(displaced_owned.read_text(encoding="utf-8"), "ours")
            recoveries = list(root.glob(".project.fcpxml.recovery-owned-*"))
            self.assertEqual(len(recoveries), 1)
            self.assertEqual(recoveries[0].read_text(encoding="utf-8"), "ours")
            contents = {
                path.read_text(encoding="utf-8")
                for path in root.iterdir()
                if path.is_file()
            }
            self.assertEqual(contents, {"ours", "external-stage"})

    def test_external_stage_created_after_rename_is_never_touched_on_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "project.fcpxml"
            real_mkstemp = tempfile.mkstemp
            stages = []

            def capture_stage(*args, **kwargs):
                descriptor, name = real_mkstemp(*args, **kwargs)
                stages.append(Path(name))
                return descriptor, name

            def create_external_stage(written):
                self.assertEqual(written, "ours")
                stages[0].write_text("external-stage", encoding="utf-8")

            with mock.patch.object(
                MODULE.tempfile, "mkstemp", side_effect=capture_stage
            ):
                MODULE._publish_and_verify(output, "ours", create_external_stage)
            self.assertEqual(output.read_text(encoding="utf-8"), "ours")
            self.assertEqual(stages[0].read_text(encoding="utf-8"), "external-stage")

    def test_output_substitution_after_successful_verifier_is_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "project.fcpxml"
            displaced_owned = root / "displaced-owned.xml"
            external = root / "external.txt"
            external.write_text("external-output", encoding="utf-8")

            def substitute_then_return(written):
                self.assertEqual(written, "ours")
                output.rename(displaced_owned)
                external.rename(output)

            with self.assertRaisesRegex(ValueError, "owned recovery"):
                MODULE._publish_and_verify(output, "ours", substitute_then_return)
            self.assertFalse(output.exists())
            self.assertEqual(displaced_owned.read_text(encoding="utf-8"), "ours")
            owned_recoveries = list(root.glob(".project.fcpxml.recovery-owned-*"))
            public_recoveries = list(root.glob(".project.fcpxml.recovery-public-*"))
            self.assertEqual(len(owned_recoveries), 1)
            self.assertEqual(len(public_recoveries), 1)
            self.assertEqual(owned_recoveries[0].read_text(encoding="utf-8"), "ours")
            self.assertEqual(
                public_recoveries[0].read_text(encoding="utf-8"), "external-output"
            )
            contents = {
                path.read_text(encoding="utf-8")
                for path in root.iterdir()
                if path.is_file()
            }
            self.assertEqual(contents, {"ours", "external-output"})


class CliTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
    def test_synthetic_media_smoke_creates_verified_xml(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tiny cleaned.mp4"
            plan_path = root / "cut-plan.json"
            output = root / "project.fcpxml"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=160x90:r=10:d=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=48000:duration=1",
                    "-c:v",
                    "mpeg4",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(source),
                ],
                check=True,
            )
            plan = make_plan(
                source=source.name, candidates=(), selected=(), rejected=()
            )
            plan.update(
                {
                    "fps": 10.0,
                    "frame_count": 10,
                    "duration": 1.0,
                    "review_decision": "no-cuts",
                }
            )
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(source),
                    str(plan_path),
                    "--output",
                    str(output),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            xml = output.read_text(encoding="utf-8")
            self.assertIn("<!DOCTYPE fcpxml>", xml)
            root_xml = ET.fromstring(xml)
            self.assertEqual(root_xml.attrib["version"], "1.10")
            asset = root_xml.find("./resources/asset")
            sequence = root_xml.find("./library/event/project/sequence")
            self.assertEqual(asset.attrib["audioSources"], "1")
            self.assertEqual(asset.attrib["audioChannels"], "1")
            self.assertEqual(asset.attrib["audioRate"], "48000")
            self.assertEqual(sequence.attrib["audioRate"], "48k")

    def test_cli_expected_errors_have_no_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.mp4"
            plan = Path(directory) / "missing.json"
            output = Path(directory) / "project.fcpxml"
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), str(missing), str(plan), "--output", str(output)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertNotIn("Traceback", completed.stderr)
            self.assertFalse(output.exists())

    def test_cli_malformed_plan_data_is_clean_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "cleaned.mp4"
            source.write_bytes(b"media")
            probe = MODULE._ProbeDetails(make_info(), 2, 48000, 1)
            plans = []
            bad_reason = make_plan()
            bad_reason["rejected_candidates"][0]["reason"] = []
            plans.append(bad_reason)
            huge_fps = make_plan()
            huge_fps["fps"] = f"{10**100}/1"
            plans.append(huge_fps)
            bad_audit = make_plan()
            bad_audit["candidates"][0]["confidence"] = []
            plans.append(bad_audit)
            for index, plan in enumerate(plans):
                plan_path = root / f"plan-{index}.json"
                output = root / f"project-{index}.fcpxml"
                plan_path.write_text(json.dumps(plan), encoding="utf-8")
                stderr = io.StringIO()
                with mock.patch.object(
                    MODULE, "probe_media", return_value=probe
                ), mock.patch.object(
                    MODULE.shutil, "which", return_value="/usr/bin/ffprobe"
                ), contextlib.redirect_stderr(stderr):
                    status = MODULE.main(
                        [str(source), str(plan_path), "--output", str(output)]
                    )
                self.assertEqual(status, 1)
                self.assertNotIn("Traceback", stderr.getvalue())
                self.assertFalse(output.exists())

    def test_cli_malformed_ffprobe_format_is_clean_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "cleaned.mp4"
            source.write_bytes(b"media")
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(make_plan()), encoding="utf-8")
            output = root / "project.fcpxml"
            malformed = ProbeTests().payload()
            payload = json.loads(malformed)
            payload["format"] = []

            def malformed_probe(path):
                return MODULE.parse_ffprobe_json(json.dumps(payload))

            stderr = io.StringIO()
            with mock.patch.object(
                MODULE, "probe_media", side_effect=malformed_probe
            ), mock.patch.object(
                MODULE.shutil, "which", return_value="/usr/bin/ffprobe"
            ), contextlib.redirect_stderr(stderr):
                status = MODULE.main(
                    [str(source), str(plan_path), "--output", str(output)]
                )
            self.assertEqual(status, 1)
            self.assertNotIn("Traceback", stderr.getvalue())
            self.assertFalse(output.exists())

    @unittest.skipUnless(shutil.which("xmllint"), "xmllint not installed")
    def test_generated_xml_validates_with_apple_dtd_when_installed(self):
        candidates = []
        configured = os.environ.get("FCPXML_DTD")
        if configured:
            candidates.append(Path(configured))
        candidates.append(
            Path(
                "/Applications/Final Cut Pro.app/Contents/Frameworks/"
                "Flexo.framework/Versions/A/Resources/FCPXMLv1_10.dtd"
            )
        )
        candidates.append(
            Path(
                "/Library/Application Support/ProApps/FCPXML DTDs/"
                "FCPXMLv1_10.dtd"
            )
        )
        for final_cut_app in Path("/Applications").glob("Final Cut*.app"):
            candidates.extend(
                path
                for path in final_cut_app.rglob("FCPXMLv1_10.dtd")
                if "Interchange.framework" in str(path)
            )
        dtd = next((path for path in candidates if path.is_file()), None)
        if dtd is None:
            self.skipTest("Apple FCPXMLv1_10.dtd not installed")
        xml = MODULE.build_fcpxml(Path("cleaned.mp4"), make_info(), [75], "Review")
        completed = subprocess.run(
            ["xmllint", "--noout", "--dtdvalid", dtd.resolve().as_uri(), "-"],
            input=xml,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
