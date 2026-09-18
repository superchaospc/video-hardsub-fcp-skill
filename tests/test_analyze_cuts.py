import importlib.util
import errno
import fcntl
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analyze-cuts.py"
SPEC = importlib.util.spec_from_file_location("analyze_cuts", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AnalyzeCutsTests(unittest.TestCase):
    def test_isolated_peak_survives_but_persistent_motion_is_rejected(self):
        scores = [1, 1, 2, 18, 2, 1, 9, 10, 11, 10, 9, 1]
        self.assertEqual(MODULE.isolated_peaks(scores, minimum=8, ratio=2.5), [3])

    def test_isolated_peaks_uses_five_frame_median(self):
        scores = [1, 1, 20, 5, 5, 5, 1]
        self.assertEqual(MODULE.isolated_peaks(scores, minimum=8, ratio=3.0), [2])

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
        self.assertAlmostEqual(candidates[0].confidence, 2.0 + 3.0 + 19.0 / 12.0)

    def test_anchor_stays_on_cut_frame_despite_trailing_scene_noise(self):
        # Real evidence from a hard cut at frame 159: every strong signal sits on
        # 159, while weak scene scores follow it.  The window around 160 also
        # covers 159, so window support alone used to anchor the cut at 160.
        events = [
            MODULE.Evidence(159, "keyframe", 1.0),
            MODULE.Evidence(159, "scene", 0.39),
            MODULE.Evidence(159, "ydif", 40.77),
            MODULE.Evidence(160, "scene", 0.02),
            MODULE.Evidence(161, "scene", 0.03),
        ]
        candidates = MODULE.merge_evidence(events, cluster_frames=3)
        self.assertEqual([item.frame for item in candidates], [159])

    def test_weak_scene_chain_does_not_swallow_a_second_cut(self):
        # Weak scene scores every two frames chain 525..584 into one cluster;
        # both strong boundaries must still become candidates.
        events = [MODULE.Evidence(frame, "scene", 0.02) for frame in range(527, 583, 2)]
        events += [
            MODULE.Evidence(525, "scene", 0.115),
            MODULE.Evidence(525, "ydif", 12.5),
            MODULE.Evidence(584, "keyframe", 1.0),
            MODULE.Evidence(584, "scene", 0.086),
        ]
        candidates = MODULE.merge_evidence(events, cluster_frames=3)
        self.assertEqual([item.frame for item in candidates], [525, 584])

    def test_empty_evidence_produces_no_candidates(self):
        self.assertEqual(MODULE.merge_evidence([], cluster_frames=3), [])

    def test_cut_plan_requires_manual_review_before_xml(self):
        plan = MODULE.make_plan("source.mp4", 30.0, 300, [])
        self.assertTrue(plan["requires_visual_review"])
        self.assertEqual(plan["selected_frames"], [])
        self.assertEqual(plan["rejected_candidates"], [])

    def test_plan_json_shape_is_deterministic(self):
        candidate = MODULE.Candidate(15, 0.5, 4.25, ["keyframe", "scene"])
        plan = MODULE.make_plan("source.mp4", 30.0, 300, [candidate])
        self.assertEqual(
            plan,
            {
                "version": 1,
                "source": "source.mp4",
                "fps": 30.0,
                "frame_count": 300,
                "duration": 10.0,
                "candidates": [
                    {
                        "frame": 15,
                        "time_seconds": 0.5,
                        "confidence": 4.25,
                        "evidence": ["keyframe", "scene"],
                    }
                ],
                "requires_visual_review": True,
                "selected_frames": [],
                "rejected_candidates": [],
            },
        )

    def test_atomic_json_failure_never_deletes_substituted_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "plan.json"
            external = root / "external.tmp"
            external.write_text("external", encoding="utf-8")
            displaced = root / "owned-temp.recovered"
            real_syscall = MODULE._rename_noreplace_syscall
            real_report = MODULE._report_retained
            substituted = False

            def fail_json_publication(source, target):
                if Path(target) == destination:
                    raise OSError(errno.EIO, "injected publication failure")
                return real_syscall(source, target)

            def substitute_before_report(path, purpose):
                nonlocal substituted
                retained = Path(path)
                if purpose == "atomic JSON temporary":
                    substituted = True
                    retained.replace(displaced)
                    external.replace(retained)
                return real_report(retained, purpose)

            with mock.patch.object(
                MODULE, "_rename_noreplace_syscall", side_effect=fail_json_publication
            ), mock.patch.object(
                MODULE, "_report_retained", side_effect=substitute_before_report
            ):
                with self.assertRaises(MODULE.AnalysisError):
                    MODULE._atomic_json(destination, {"version": 1})

            contents = {
                item.read_text(encoding="utf-8")
                for item in root.iterdir()
                if item.is_file()
            }
            self.assertTrue(substituted, "test must inject at the live report boundary")
            self.assertIn("external", contents)
            self.assertTrue(displaced.is_file())

    def test_parse_fps_accepts_rational_and_decimal_values(self):
        self.assertAlmostEqual(MODULE.parse_fps("30000/1001"), 30000 / 1001)
        self.assertEqual(MODULE.parse_fps("29.97"), 29.97)

    def test_metadata_parser_accepts_frame_headers_and_lavfi_values(self):
        output = """
[Parsed_metadata_1 @ 0x1] frame:12 pts:12012 pts_time:0.4004
[Parsed_metadata_1 @ 0x1] lavfi.scene_score=0.081
[Parsed_metadata_1 @ 0x1] frame:19 pts:19019 pts_time:0.634
[Parsed_metadata_1 @ 0x1] lavfi.scene_score=0.100
"""
        events = MODULE.parse_metadata(output, "lavfi.scene_score", "scene", 30.0)
        self.assertEqual(events, [MODULE.Evidence(12, "scene", 0.081), MODULE.Evidence(19, "scene", 0.1)])

    def test_metadata_parser_ignores_malformed_values(self):
        output = """
frame:3 pts:3 pts_time:0.1
lavfi.signalstats.YDIF=not-a-number
lavfi.signalstats.YDIF=9.0
frame:broken pts_time:nope
lavfi.signalstats.YDIF=10.0
"""
        self.assertEqual(
            MODULE.parse_metadata(output, "lavfi.signalstats.YDIF", "ydif", 30.0),
            [MODULE.Evidence(3, "ydif", 9.0), MODULE.Evidence(3, "ydif", 10.0)],
        )

    def test_metadata_parser_uses_pts_time_when_select_renumbers_frames(self):
        output = """
frame:0 pts:120000 pts_time:4.0
lavfi.scene_score=0.2
"""
        self.assertEqual(
            MODULE.parse_metadata(output, "lavfi.scene_score", "scene", 30.0),
            [MODULE.Evidence(120, "scene", 0.2)],
        )

    def test_metadata_parser_consumes_a_one_pass_line_iterator(self):
        class OnePassLines:
            def __init__(self):
                self.iterations = 0

            def __iter__(self):
                self.iterations += 1
                if self.iterations > 1:
                    raise AssertionError("metadata input was iterated more than once")
                yield "frame:2 pts:2 pts_time:0.2\n"
                yield "lavfi.signalstats.YDIF=9.5\n"

        lines = OnePassLines()
        self.assertEqual(
            MODULE.parse_metadata(lines, "lavfi.signalstats.YDIF", "ydif", 10.0),
            [MODULE.Evidence(2, "ydif", 9.5)],
        )
        self.assertEqual(lines.iterations, 1)

    def test_iter_metadata_is_lazy(self):
        consumed = []

        def lines():
            consumed.append("header")
            yield "frame:2 pts:2 pts_time:0.2\n"
            consumed.append("value")
            yield "lavfi.signalstats.YDIF=9.5\n"

        events = MODULE.iter_metadata(lines(), "lavfi.signalstats.YDIF", "ydif", 10.0)
        self.assertEqual(consumed, [])
        self.assertEqual(next(events), MODULE.Evidence(2, "ydif", 9.5))
        self.assertEqual(consumed, ["header", "value"])

    def test_cli_ydif_has_a_rolling_peak_iterator(self):
        self.assertTrue(
            hasattr(MODULE, "iter_ydif_peak_events"),
            "CLI needs a constant-working-memory YDIF peak iterator",
        )

    def test_rolling_ydif_peak_iterator_does_not_prefetch_large_input(self):
        pulled = 0

        def events():
            nonlocal pulled
            for frame in range(100_000):
                pulled += 1
                score = 20.0 if frame == 2 else 1.0
                yield MODULE.Evidence(frame, "ydif", score)

        peaks = MODULE.iter_ydif_peak_events(
            events(), frame_count=100_000, minimum=8, ratio=2.5
        )
        self.assertEqual(next(peaks), MODULE.Evidence(2, "ydif", 20.0))
        self.assertLessEqual(pulled, 6, "rolling peak detection must retain only lookahead")

    def test_rolling_ydif_preserves_shortened_start_window(self):
        scores = [1.0, 20.0, 1.0, 1.0, 1.0, 1.0]
        events = [
            MODULE.Evidence(frame, "ydif", score)
            for frame, score in enumerate(scores)
        ]
        self.assertEqual(
            list(MODULE.iter_ydif_peak_events(events, len(scores), minimum=8, ratio=2.5)),
            [MODULE.Evidence(1, "ydif", 20.0)],
        )

    def test_rolling_ydif_flushes_shortened_end_window(self):
        scores = [1.0, 1.0, 1.0, 20.0, 1.0]
        events = [
            MODULE.Evidence(frame, "ydif", score)
            for frame, score in enumerate(scores)
        ]
        self.assertEqual(
            list(MODULE.iter_ydif_peak_events(events, len(scores), minimum=8, ratio=2.5)),
            [MODULE.Evidence(3, "ydif", 20.0)],
        )

    def test_probe_streams_keyframes_instead_of_capturing_full_output(self):
        payload = json.dumps(
            {
                "streams": [
                    {
                        "avg_frame_rate": "1/1",
                        "r_frame_rate": "1/1",
                        "nb_frames": "2",
                        "duration": "2",
                        "start_time": "0",
                        "time_base": "1/1000",
                    }
                ],
                "format": {"duration": "2"},
            }
        )
        completed = subprocess.CompletedProcess([], 0, stdout=payload, stderr="")

        def streamed_lines(command, description):
            joined = " ".join(command)
            if "nokey" in joined:
                return iter(["0.0\n"])
            if "best_effort_timestamp_time" in joined:
                return iter(["0.0\n", "1.0\n"])
            return iter(["0\n", "1000\n"])

        with mock.patch.object(MODULE, "_run", return_value=completed) as run_mock, \
            mock.patch.object(
                MODULE, "iter_command_stdout", side_effect=streamed_lines
            ) as stream_mock:
            _, keyframes = MODULE.probe_video(Path("source.mp4"))

        self.assertEqual(run_mock.call_count, 1, "keyframes must not use capture_output")
        self.assertEqual(stream_mock.call_count, 2)
        self.assertEqual(keyframes, [MODULE.Evidence(0, "keyframe", 1.0)])

    def test_scene_analysis_uses_streaming_stderr_iterator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            probe = mock.Mock(
                fps=30.0,
                frame_count=30,
                duration=1.0,
                start_time=0.0,
                width=160,
                height=90,
            )
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/tool"), \
                mock.patch.object(MODULE, "probe_video", return_value=(probe, [])), \
                mock.patch.object(MODULE, "_run", return_value=completed) as run_mock, \
                mock.patch.object(
                    MODULE, "iter_command_stderr", side_effect=[iter(()), iter(())]
                ) as stream_mock:
                MODULE.run_analysis(
                    source, root / "plan.json", root / "review", "fine", force=False
                )

            self.assertEqual(run_mock.call_count, 0, "scene metadata must not be captured")
            self.assertEqual(stream_mock.call_count, 2)

    def test_candidate_filter_removes_frame_zero_and_last_two_frames(self):
        candidates = [
            MODULE.Candidate(0, 0.0, 1.0, ["keyframe"]),
            MODULE.Candidate(1, 1 / 30, 1.0, ["keyframe"]),
            MODULE.Candidate(97, 97 / 30, 1.0, ["keyframe"]),
            MODULE.Candidate(98, 98 / 30, 1.0, ["keyframe"]),
            MODULE.Candidate(99, 99 / 30, 1.0, ["keyframe"]),
        ]
        self.assertEqual([item.frame for item in MODULE.filter_edge_candidates(candidates, 100)], [1, 97])

    def test_command_builders_include_required_filters(self):
        scene = MODULE.build_scene_command("clip.mp4")
        ydif = MODULE.build_ydif_command("clip.mp4")
        self.assertIn("select='gt(scene,0.012)'", " ".join(scene))
        self.assertIn("metadata=print", " ".join(scene))
        self.assertIn("signalstats,metadata=print", " ".join(ydif))
        self.assertIn("key=lavfi.signalstats.YDIF", " ".join(ydif))
        self.assertIn("-copyts", scene)
        self.assertIn("-copyts", ydif)

    def test_review_index_pages_landscape_five_candidates_per_page(self):
        candidates = [
            MODULE.Candidate(frame, frame / 30, 1.0, ["ydif"])
            for frame in range(10, 16)
        ]
        index = MODULE.make_review_index(candidates, 1920, 1080, 300)
        self.assertEqual([page["page"] for page in index["pages"]], [1, 2])
        self.assertEqual(len(index["pages"][0]["tiles"]), 5)
        self.assertEqual(
            index["pages"][1]["tiles"],
            [
                {
                    "tile": 1,
                    "frame": 15,
                    "before_frame": 14,
                    "context_frames": [13, 14, 15, 16],
                }
            ],
        )

    def test_review_index_pages_portrait_three_candidates_per_page(self):
        candidates = [
            MODULE.Candidate(frame, frame / 30, 1.0, ["ydif"])
            for frame in range(10, 14)
        ]
        index = MODULE.make_review_index(candidates, 720, 1280, 300)
        self.assertEqual([page["page"] for page in index["pages"]], [1, 2])
        self.assertEqual(len(index["pages"][0]["tiles"]), 3)
        self.assertEqual(len(index["pages"][1]["tiles"]), 1)

    def test_context_frames_clamp_at_media_boundaries(self):
        # A candidate at frame 1 has no frame -1; the window repeats frame 0
        # rather than shrinking, so every tile keeps a fixed column count.
        self.assertEqual(MODULE.context_frames_for(1, 300), [0, 0, 1, 2])
        self.assertEqual(MODULE.context_frames_for(299, 300), [297, 298, 299, 299])
        self.assertEqual(MODULE.context_frames_for(50, 300), [48, 49, 50, 51])

    def test_review_pipeline_writes_frames_in_requested_order(self):
        # Overlapping context windows repeat frames and step backwards relative to
        # decode order. The tiler must still receive them in requested order.
        requested = [8, 9, 10, 11, 10, 11, 12, 13]
        frame_size = 4
        payloads = {n: bytes([n]) * frame_size for n in range(16)}

        class FakeStdout:
            def __init__(self):
                self.frame = 0

            def read(self, size):
                if self.frame >= 16:
                    return b""
                data = payloads[self.frame][:size]
                self.frame += 1
                return data

            def close(self):
                pass

        written = []

        class FakeStdin:
            def write(self, data):
                written.append(data[0])

            def close(self):
                pass

        decoder = mock.Mock(stdout=FakeStdout(), wait=mock.Mock(return_value=0))
        tiler = mock.Mock(stdin=FakeStdin(), wait=mock.Mock(return_value=0))
        with mock.patch.object(
            MODULE.subprocess, "Popen", side_effect=[tiler, decoder]
        ):
            MODULE._run_review_pipeline(
                [["decoder"], ["tiler"]], requested, frame_size
            )
        self.assertEqual(written, requested)

    def test_context_frames_are_non_decreasing(self):
        for frame in (0, 1, 2, 150, 298, 299):
            window = MODULE.context_frames_for(frame, 300)
            self.assertEqual(window, sorted(window))
            self.assertEqual(len(window), MODULE.CONTEXT_COLUMNS)

    def test_review_decoding_uses_bounded_pipeline_as_candidates_grow(self):
        candidates = [
            MODULE.Candidate(frame, frame / 30, 1.0, ["ydif"])
            for frame in range(10, 50)
        ]
        many_candidates = [
            MODULE.Candidate(frame, frame / 30, 1.0, ["ydif"])
            for frame in range(10, 1010)
        ]
        commands = MODULE.build_review_commands(Path("clip.mp4"), candidates, Path("review"))
        many_commands = MODULE.build_review_commands(
            Path("clip.mp4"), many_candidates, Path("review")
        )
        self.assertEqual(len(commands), 2)
        self.assertEqual(len(many_commands), 2)
        command_text = " ".join(part for command in commands for part in command)
        many_command_text = " ".join(part for command in many_commands for part in command)
        self.assertEqual(command_text.count("-i clip.mp4"), 1)
        self.assertIn("rawvideo", command_text)
        self.assertLessEqual(len(many_command_text), len(command_text) + 8)

    def test_completed_review_must_partition_every_candidate_once(self):
        plan = MODULE.make_plan(
            "source.mp4", 30.0, 300, [MODULE.Candidate(10, 1 / 3, 1.0, ["ydif"])]
        )
        with self.assertRaisesRegex(MODULE.AnalysisError, "exactly once"):
            MODULE.validate_review_decisions(plan)
        plan["rejected_candidates"] = [{"frame": 10, "reason": "camera-shake"}]
        MODULE.validate_review_decisions(plan)

    def test_probe_parser_falls_back_to_duration_for_frame_count(self):
        payload = {
            "streams": [
                {
                    "avg_frame_rate": "24000/1001",
                    "r_frame_rate": "24000/1001",
                    "nb_frames": "N/A",
                    "duration": "N/A",
                    "start_time": "5.0",
                    "time_base": "1/1000",
                }
            ],
            "format": {"duration": "10.01"},
        }
        probe = MODULE.parse_probe_json(json.dumps(payload))
        self.assertAlmostEqual(probe.fps, 24000 / 1001)
        self.assertEqual(probe.frame_count, round(10.01 * 24000 / 1001))
        self.assertEqual(probe.duration, 10.01)
        self.assertEqual(probe.start_time, 5.0)
        self.assertEqual(probe.time_base, 0.001)

    def test_keyframes_are_normalized_by_nonzero_stream_start_time(self):
        events = MODULE.parse_keyframe_timestamps("5.0\n5.5\n", 30.0, start_time=5.0)
        self.assertEqual(
            events,
            [MODULE.Evidence(0, "keyframe", 1.0), MODULE.Evidence(15, "keyframe", 1.0)],
        )

    def test_vfr_probe_is_rejected_with_actionable_error(self):
        payload = {
            "streams": [
                {
                    "avg_frame_rate": "30/1",
                    "r_frame_rate": "60/1",
                    "nb_frames": "60",
                    "duration": "2.0",
                    "start_time": "0",
                }
            ],
            "format": {"duration": "2.0"},
        }
        with self.assertRaisesRegex(MODULE.AnalysisError, "variable-frame-rate|CFR"):
            MODULE.parse_probe_json(json.dumps(payload))

    def test_irregular_frame_timestamp_deltas_are_rejected_even_when_rates_match(self):
        timestamps = iter(["0.000000\n", "0.033333\n", "0.083333\n"])
        with self.assertRaisesRegex(MODULE.AnalysisError, "variable-frame-rate|CFR"):
            MODULE.validate_cfr_timestamps(
                timestamps,
                30.0,
                expected_frames=3,
                start_time=0.0,
                time_base=1 / 1000,
            )

    def test_cfr_timestamp_quantization_to_milliseconds_is_accepted(self):
        timestamps = iter(["0.000\n", "0.042\n", "0.083\n", "0.125\n", "0.167\n"])
        MODULE.validate_cfr_timestamps(
            timestamps,
            24000 / 1001,
            expected_frames=5,
            start_time=0.0,
            time_base=1 / 1000,
        )

    def test_cfr_validator_accepts_integer_pts_mode(self):
        self.assertIn(
            "integer_pts",
            inspect.signature(MODULE.validate_cfr_timestamps).parameters,
        )

    def test_integer_pts_accepts_half_tick_quantization(self):
        error = None
        try:
            MODULE.validate_cfr_timestamps(
                iter(["0\n", "42\n", "83\n", "125\n", "167\n"]),
                "24000/1001",
                expected_frames=5,
                time_base=1 / 1000,
                integer_pts=True,
            )
        except MODULE.AnalysisError as exc:
            error = exc
        self.assertIsNone(error, "correctly quantized integer PTS must be accepted")

    def test_integer_pts_rejects_irregular_30fps_cadence(self):
        with self.assertRaisesRegex(MODULE.AnalysisError, "variable-frame-rate|CFR"):
            MODULE.validate_cfr_timestamps(
                iter(["0\n", "32\n", "66\n", "98\n", "133\n"]),
                "30/1",
                expected_frames=5,
                time_base=1 / 1000,
                integer_pts=True,
            )

    def test_existing_output_or_review_directory_requires_force(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.touch()
            output = root / "cut-plan.json"
            output.write_text("existing", encoding="utf-8")
            with mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/tool"):
                with self.assertRaisesRegex(MODULE.AnalysisError, "--force"):
                    MODULE.run_analysis(source, output, root / "review", "fine", force=False)

            output.unlink()
            (root / "review").mkdir()
            with mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/tool"):
                with self.assertRaisesRegex(MODULE.AnalysisError, "--force"):
                    MODULE.run_analysis(source, output, root / "review", "fine", force=False)

    def test_force_rejects_source_output_aliases_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source-bytes")
            aliases = [source, root / "source-link.mp4", root / "source-hardlink.mp4"]
            aliases[1].symlink_to(source)
            os.link(source, aliases[2])
            probe = mock.Mock(fps=30.0, frame_count=30, duration=1.0, start_time=0.0)
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            for alias in aliases:
                with self.subTest(alias=alias.name):
                    with mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/tool"), \
                        mock.patch.object(MODULE, "probe_video", return_value=(probe, [])), \
                        mock.patch.object(MODULE, "_run", return_value=completed):
                        with self.assertRaisesRegex(MODULE.AnalysisError, "same file"):
                            MODULE.run_analysis(source, alias, root / "review", "fine", force=True)
                    self.assertEqual(source.read_bytes(), b"source-bytes")

    def test_failed_force_run_preserves_artifacts_and_retains_staging_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            output = root / "cut-plan.json"
            output.write_text("old-plan", encoding="utf-8")
            review = root / "review"
            review.mkdir()
            (review / "old.txt").write_text("old-review", encoding="utf-8")
            probe = mock.Mock(
                fps=30.0,
                frame_count=30,
                duration=1.0,
                start_time=0.0,
                width=160,
                height=90,
            )
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            shared = [
                mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/tool"),
                mock.patch.object(MODULE, "probe_video", return_value=(probe, [])),
                mock.patch.object(MODULE, "_run", return_value=completed),
                mock.patch.object(MODULE, "iter_command_stderr", return_value=iter(())),
            ]
            with shared[0], shared[1], shared[2], shared[3], mock.patch.object(
                MODULE, "render_review", side_effect=MODULE.AnalysisError("render failed")
            ):
                with self.assertRaisesRegex(MODULE.AnalysisError, "render failed"):
                    MODULE.run_analysis(source, output, review, "fine", force=True)
            self.assertEqual(output.read_text(encoding="utf-8"), "old-plan")
            self.assertEqual((review / "old.txt").read_text(encoding="utf-8"), "old-review")
            self.assertTrue(any("stage-" in item.name for item in root.iterdir()))
            self.assertTrue(any(item.name.endswith(".lock") for item in root.iterdir()))

            with mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/tool"), \
                mock.patch.object(MODULE, "probe_video", return_value=(probe, [])), \
                mock.patch.object(MODULE, "_run", return_value=completed), \
                mock.patch.object(MODULE, "iter_command_stderr", return_value=iter(())):
                plan = MODULE.run_analysis(source, output, review, "fine", force=True)
            self.assertEqual(plan["candidates"], [])
            self.assertTrue((review / "index.json").is_file())

    def test_no_force_publication_race_preserves_newly_created_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            output = root / "plan.json"
            review = root / "review"
            probe = mock.Mock(
                fps=30.0,
                frame_count=30,
                duration=1.0,
                start_time=0.0,
                width=160,
                height=90,
            )
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            original_render = MODULE.render_review

            def render_then_race(source_path, candidates, staged_review, **kwargs):
                result = original_render(source_path, candidates, staged_review, **kwargs)
                output.write_text("racer-plan", encoding="utf-8")
                review.mkdir()
                (review / "racer.txt").write_text("racer-review", encoding="utf-8")
                return result

            with mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/tool"), \
                mock.patch.object(MODULE, "probe_video", return_value=(probe, [])), \
                mock.patch.object(MODULE, "_run", return_value=completed), \
                mock.patch.object(MODULE, "iter_command_stderr", return_value=iter(())), \
                mock.patch.object(MODULE, "render_review", side_effect=render_then_race):
                with self.assertRaisesRegex(MODULE.AnalysisError, "appeared|--force"):
                    MODULE.run_analysis(source, output, review, "fine", force=False)
            self.assertEqual(output.read_text(encoding="utf-8"), "racer-plan")
            self.assertEqual(
                (review / "racer.txt").read_text(encoding="utf-8"), "racer-review"
            )
            self.assertTrue(any("stage-" in item.name for item in root.iterdir()))
            self.assertTrue(any(item.name.endswith(".lock") for item in root.iterdir()))

    def test_rename_noreplace_maps_eexist_to_actionable_error(self):
        with mock.patch.object(
            MODULE,
            "_rename_noreplace_syscall",
            side_effect=OSError(errno.EEXIST, "exists"),
        ):
            with self.assertRaisesRegex(MODULE.AnalysisError, "already exists|--force"):
                MODULE.rename_noreplace(Path("source"), Path("destination"))

    @unittest.skipUnless(sys.platform == "darwin" or sys.platform.startswith("linux"), "OS support")
    def test_rename_noreplace_real_file_and_directory_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_file = root / "source.txt"
            destination_file = root / "destination.txt"
            source_file.write_text("content", encoding="utf-8")
            MODULE.rename_noreplace(source_file, destination_file)
            self.assertFalse(source_file.exists())
            self.assertEqual(destination_file.read_text(encoding="utf-8"), "content")

            source_dir = root / "source-dir"
            destination_dir = root / "destination-dir"
            source_dir.mkdir()
            (source_dir / "item.txt").write_text("item", encoding="utf-8")
            MODULE.rename_noreplace(source_dir, destination_dir)
            self.assertFalse(source_dir.exists())
            self.assertEqual(
                (destination_dir / "item.txt").read_text(encoding="utf-8"), "item"
            )

    def _assert_immediate_noreplace_collision(self, target_kind):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            output = root / "plan.json"
            review = root / "review"
            probe = mock.Mock(
                fps=30.0,
                frame_count=30,
                duration=1.0,
                start_time=0.0,
                width=160,
                height=90,
            )
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            real_syscall = MODULE._rename_noreplace_syscall
            injected = False

            def collide_immediately(src, dst):
                nonlocal injected
                destination = Path(dst)
                collision_target = output if target_kind == "plan" else review
                if not injected and destination == collision_target:
                    injected = True
                    if target_kind == "plan":
                        output.write_text("external-plan", encoding="utf-8")
                    else:
                        review.mkdir()
                        (review / "external.txt").write_text(
                            "external-review", encoding="utf-8"
                        )
                return real_syscall(src, dst)

            with mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/tool"), \
                mock.patch.object(MODULE, "probe_video", return_value=(probe, [])), \
                mock.patch.object(MODULE, "_run", return_value=completed), \
                mock.patch.object(MODULE, "iter_command_stderr", return_value=iter(())), \
                mock.patch.object(
                    MODULE, "_rename_noreplace_syscall", side_effect=collide_immediately
                ):
                with self.assertRaisesRegex(MODULE.AnalysisError, "already exists|--force"):
                    MODULE.run_analysis(source, output, review, "fine", force=False)

            if target_kind == "plan":
                self.assertEqual(output.read_text(encoding="utf-8"), "external-plan")
                self.assertFalse(review.exists())
            else:
                self.assertFalse(output.exists(), "just-published plan was not rolled back")
                self.assertEqual(
                    (review / "external.txt").read_text(encoding="utf-8"),
                    "external-review",
                )
            self.assertTrue(source.is_file())
            self.assertTrue(any("stage-" in item.name for item in root.iterdir()))
            self.assertTrue(any(item.name.endswith(".lock") for item in root.iterdir()))

    def test_plan_collision_at_noreplace_syscall_preserves_external_target(self):
        self._assert_immediate_noreplace_collision("plan")

    def test_review_collision_at_noreplace_syscall_rolls_back_only_published_plan(self):
        self._assert_immediate_noreplace_collision("review")

    def test_review_collision_rollback_preserves_replacement_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            output = root / "plan.json"
            review = root / "review"
            external_plan = root / "external-plan.tmp"
            external_plan.write_text("external-plan", encoding="utf-8")
            probe = mock.Mock(
                fps=30.0,
                frame_count=30,
                duration=1.0,
                start_time=0.0,
                width=160,
                height=90,
            )
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            real_syscall = MODULE._rename_noreplace_syscall
            real_rollback = MODULE._rollback_published_plan

            def collide_review(src, dst):
                if Path(dst) == review and not review.exists():
                    review.mkdir()
                    (review / "external.txt").write_text("external", encoding="utf-8")
                return real_syscall(src, dst)

            def replace_plan_before_rollback(path, published_handle):
                self.assertFalse(published_handle.closed)
                os.fstat(published_handle.fileno())
                external_plan.replace(output)
                return real_rollback(path, published_handle)

            with mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/tool"), \
                mock.patch.object(MODULE, "probe_video", return_value=(probe, [])), \
                mock.patch.object(MODULE, "_run", return_value=completed), \
                mock.patch.object(MODULE, "iter_command_stderr", return_value=iter(())), \
                mock.patch.object(MODULE, "_rename_noreplace_syscall", side_effect=collide_review), \
                mock.patch.object(
                    MODULE, "_rollback_published_plan", side_effect=replace_plan_before_rollback
                ):
                with self.assertRaisesRegex(
                    MODULE.AnalysisError, "already exists|--force|recovery"
                ):
                    MODULE.run_analysis(source, output, review, "fine", force=False)

            self.assertFalse(output.exists())
            self.assertEqual(
                (review / "external.txt").read_text(encoding="utf-8"), "external"
            )
            recovered_contents = [
                item.read_text(encoding="utf-8")
                for item in root.glob(".plan.json.recovery-*")
                if item.is_file()
            ]
            self.assertIn("external-plan", recovered_contents)
            recovered_plans = [
                json.loads(content)
                for content in recovered_contents
                if content != "external-plan"
            ]
            self.assertEqual(len(recovered_plans), 1)
            self.assertEqual(recovered_plans[0]["source"], str(source))

    def test_no_force_rollback_never_deletes_substituted_recovery_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "plan.json"
            output.write_text("owned-plan", encoding="utf-8")
            external = root / "external.tmp"
            external.write_text("external-plan", encoding="utf-8")
            displaced_owned = root / "owned-plan.recovered"
            real_syscall = MODULE._rename_noreplace_syscall
            substituted = False

            def substitute_before_public_recovery(source, destination):
                nonlocal substituted
                if not substituted and ".recovery-public-" in Path(destination).name:
                    substituted = True
                    Path(source).replace(displaced_owned)
                    external.replace(source)
                return real_syscall(source, destination)

            with output.open("rb") as published_handle, mock.patch.object(
                MODULE,
                "_rename_noreplace_syscall",
                side_effect=substitute_before_public_recovery,
            ):
                MODULE._rollback_published_plan(output, published_handle)

            contents = {
                item.read_text(encoding="utf-8")
                for item in root.iterdir()
                if item.is_file()
            }
            self.assertTrue(substituted, "test must inject at the live rename boundary")
            self.assertIn("owned-plan", contents)
            self.assertIn("external-plan", contents)

    def test_force_rollback_quarantines_concurrent_review_and_restores_old_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged_plan = root / "staged-plan.json"
            staged_plan.write_text("new-plan", encoding="utf-8")
            staged_review = root / "staged-review"
            staged_review.mkdir()
            (staged_review / "new.txt").write_text("new-review", encoding="utf-8")
            output = root / "plan.json"
            output.write_text("old-plan", encoding="utf-8")
            review = root / "review"
            review.mkdir()
            (review / "old.txt").write_text("old-review", encoding="utf-8")
            external_review = root / "external-review"
            external_review.mkdir()
            (external_review / "external.txt").write_text(
                "external-review", encoding="utf-8"
            )
            displaced_new_review = root / "published-review.recovered"
            real_replace = os.replace
            real_syscall = MODULE._rename_noreplace_syscall

            def publish_then_fail(source, destination):
                if Path(source) == staged_plan and Path(destination) == output:
                    real_replace(review, displaced_new_review)
                    real_replace(external_review, review)
                    raise OSError("injected plan publication failure")
                return real_syscall(source, destination)

            with mock.patch.object(
                MODULE, "_rename_noreplace_syscall", side_effect=publish_then_fail
            ):
                with self.assertRaisesRegex(MODULE.AnalysisError, "recovery"):
                    MODULE._publish_artifacts(
                        staged_plan, staged_review, output, review, force=True
                    )

            self.assertEqual(output.read_text(encoding="utf-8"), "old-plan")
            self.assertEqual(
                (review / "old.txt").read_text(encoding="utf-8"), "old-review"
            )
            recovered_external = [
                item
                for item in root.glob(".review.recovery-*")
                if (item / "external.txt").is_file()
            ]
            self.assertEqual(len(recovered_external), 1)
            self.assertEqual(
                (recovered_external[0] / "external.txt").read_text(encoding="utf-8"),
                "external-review",
            )
            self.assertEqual(
                (displaced_new_review / "new.txt").read_text(encoding="utf-8"),
                "new-review",
            )

    def test_force_rollback_quarantines_reappeared_unpublished_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged_plan = root / "staged-plan.json"
            staged_plan.write_text("new-plan", encoding="utf-8")
            staged_review = root / "staged-review"
            staged_review.mkdir()
            (staged_review / "new.txt").write_text("new-review", encoding="utf-8")
            output = root / "plan.json"
            output.write_text("old-plan", encoding="utf-8")
            review = root / "review"
            review.mkdir()
            (review / "old.txt").write_text("old-review", encoding="utf-8")
            real_syscall = MODULE._rename_noreplace_syscall

            def reappear_before_plan_publish(source, destination):
                if Path(source) == staged_plan and Path(destination) == output:
                    output.write_text("external-plan", encoding="utf-8")
                    raise OSError(errno.EEXIST, "concurrent plan appeared")
                return real_syscall(source, destination)

            with mock.patch.object(
                MODULE,
                "_rename_noreplace_syscall",
                side_effect=reappear_before_plan_publish,
            ):
                with self.assertRaisesRegex(MODULE.AnalysisError, "recovery"):
                    MODULE._publish_artifacts(
                        staged_plan, staged_review, output, review, force=True
                    )

            self.assertEqual(output.read_text(encoding="utf-8"), "old-plan")
            self.assertEqual(
                (review / "old.txt").read_text(encoding="utf-8"), "old-review"
            )
            recovered_plans = [
                item.read_text(encoding="utf-8")
                for item in root.glob(".plan.json.recovery-*")
                if item.is_file()
            ]
            self.assertEqual(recovered_plans, ["external-plan"])

    def test_unsupported_noreplace_force_publication_preserves_public_artifacts(self):
        unsupported_errors = {
            errno.ENOSYS,
            errno.EINVAL,
            getattr(errno, "ENOTSUP", errno.ENOSYS),
        }
        for error_number in unsupported_errors:
            with self.subTest(error_number=error_number), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                staged_plan = root / "staged-plan.json"
                staged_plan.write_text("new-plan", encoding="utf-8")
                staged_review = root / "staged-review"
                staged_review.mkdir()
                output = root / "plan.json"
                output.write_text("old-plan", encoding="utf-8")
                review = root / "review"
                review.mkdir()
                (review / "old.txt").write_text("old-review", encoding="utf-8")

                with mock.patch.object(
                    MODULE,
                    "_rename_noreplace_syscall",
                    side_effect=OSError(error_number, "unsupported"),
                ):
                    with self.assertRaisesRegex(MODULE.AnalysisError, "unsupported"):
                        MODULE._publish_artifacts(
                            staged_plan, staged_review, output, review, force=True
                        )

                self.assertEqual(output.read_text(encoding="utf-8"), "old-plan")
                self.assertEqual(
                    (review / "old.txt").read_text(encoding="utf-8"), "old-review"
                )

    def test_unsupported_first_backup_does_not_quarantine_untouched_other_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged_plan = root / "staged-plan.json"
            staged_plan.write_text("new-plan", encoding="utf-8")
            staged_review = root / "staged-review"
            staged_review.mkdir()
            output = root / "plan.json"
            output.write_text("old-plan", encoding="utf-8")
            review = root / "review"
            review.mkdir()
            (review / "old.txt").write_text("old-review", encoding="utf-8")
            real_syscall = MODULE._rename_noreplace_syscall

            def unsupported_review_backup(source, destination):
                if Path(source) == review and ".backup-" in Path(destination).name:
                    raise OSError(errno.ENOSYS, "unsupported review filesystem")
                return real_syscall(source, destination)

            with mock.patch.object(
                MODULE,
                "_rename_noreplace_syscall",
                side_effect=unsupported_review_backup,
            ):
                with self.assertRaisesRegex(MODULE.AnalysisError, "unsupported"):
                    MODULE._publish_artifacts(
                        staged_plan, staged_review, output, review, force=True
                    )

            self.assertTrue(output.is_file(), "untouched plan must remain public")
            self.assertEqual(output.read_text(encoding="utf-8"), "old-plan")
            self.assertEqual(
                (review / "old.txt").read_text(encoding="utf-8"), "old-review"
            )
            self.assertFalse(any("recovery-" in item.name for item in root.iterdir()))

    def test_force_preflight_checks_every_parent_before_public_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_parent = root / "output-parent"
            review_parent = root / "review-parent"
            output_parent.mkdir()
            review_parent.mkdir()
            output = output_parent / "plan.json"
            output.write_text("old-plan", encoding="utf-8")
            review = review_parent / "review"
            review.mkdir()
            (review / "old.txt").write_text("old-review", encoding="utf-8")
            staged_plan = root / "staged-plan.json"
            staged_plan.write_text("new-plan", encoding="utf-8")
            staged_review = root / "staged-review"
            staged_review.mkdir()
            real_syscall = MODULE._rename_noreplace_syscall

            def unsupported_second_parent(source, destination):
                destination = Path(destination)
                if (
                    destination.parent.resolve() == review_parent.resolve()
                    and ".preflight-" in destination.name
                ):
                    raise OSError(errno.ENOTSUP, "unsupported review filesystem")
                return real_syscall(source, destination)

            with mock.patch.object(
                MODULE,
                "_rename_noreplace_syscall",
                side_effect=unsupported_second_parent,
            ):
                with self.assertRaisesRegex(MODULE.AnalysisError, "unsupported"):
                    MODULE._publish_artifacts(
                        staged_plan, staged_review, output, review, force=True
                    )

            self.assertEqual(output.read_text(encoding="utf-8"), "old-plan")
            self.assertEqual(
                (review / "old.txt").read_text(encoding="utf-8"), "old-review"
            )

    def test_no_force_preflight_checks_every_parent_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_parent = root / "output-parent"
            review_parent = root / "review-parent"
            output_parent.mkdir()
            review_parent.mkdir()
            output = output_parent / "plan.json"
            output.write_text("old-plan", encoding="utf-8")
            review = review_parent / "review"
            review.mkdir()
            (review / "old.txt").write_text("old-review", encoding="utf-8")
            staged_plan = root / "staged-plan.json"
            staged_plan.write_text("new-plan", encoding="utf-8")
            staged_review = root / "staged-review"
            staged_review.mkdir()
            real_syscall = MODULE._rename_noreplace_syscall
            public_moves = []
            reported = []

            def unsupported_second_parent(source, destination):
                source = Path(source)
                destination = Path(destination)
                if source in (staged_plan, staged_review):
                    public_moves.append((source, destination))
                if (
                    destination.parent.resolve() == review_parent.resolve()
                    and ".preflight-" in destination.name
                ):
                    raise OSError(errno.ENOTSUP, "unsupported review filesystem")
                return real_syscall(source, destination)

            with mock.patch.object(
                MODULE,
                "_rename_noreplace_syscall",
                side_effect=unsupported_second_parent,
            ), mock.patch.object(
                MODULE,
                "_report_retained",
                side_effect=lambda path, purpose: reported.append((Path(path), purpose)),
            ):
                with self.assertRaisesRegex(MODULE.AnalysisError, "unsupported"):
                    MODULE._publish_artifacts(
                        staged_plan, staged_review, output, review, force=False
                    )

            self.assertEqual(public_moves, [], "publication must not start before preflight")
            self.assertEqual(output.read_text(encoding="utf-8"), "old-plan")
            self.assertEqual(
                (review / "old.txt").read_text(encoding="utf-8"), "old-review"
            )
            self.assertTrue(staged_plan.is_file())
            self.assertTrue(staged_review.is_dir())
            self.assertTrue(reported, "retained preflight scratch paths must be reported")
            self.assertTrue(
                all(purpose == "no-replace preflight" for _, purpose in reported)
            )

    def test_second_stage_creation_failure_retains_first_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            output = root / "plan.json"
            review = root / "review"
            real_mkdtemp = tempfile.mkdtemp
            calls = 0

            def fail_second_stage(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("disk full")
                return real_mkdtemp(*args, **kwargs)

            with mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/tool"), \
                mock.patch.object(MODULE.tempfile, "mkdtemp", side_effect=fail_second_stage):
                with self.assertRaisesRegex(MODULE.AnalysisError, "Filesystem operation failed"):
                    MODULE.run_analysis(source, output, review, "fine", force=True)
            self.assertTrue(any("stage-" in item.name for item in root.iterdir()))
            self.assertTrue(source.is_file())

    def test_exclusive_lock_prevents_concurrent_overwrite_race(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            lock = root / ".plan.json.lock"
            descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/tool"):
                    with self.assertRaisesRegex(MODULE.AnalysisError, "Another analyzer"):
                        MODULE.run_analysis(
                            source, root / "plan.json", root / "review", "fine", force=True
                        )
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            self.assertEqual(source.read_bytes(), b"source")
            self.assertTrue(lock.is_file())

    def test_unlocked_persistent_lock_file_does_not_block(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "plan.json"
            review = root / "review"
            lock = root / ".plan.json.lock"
            lock.write_text("previous owner\n", encoding="utf-8")

            error = None
            try:
                with MODULE._artifact_locks(output, review):
                    self.assertTrue(lock.is_file())
            except MODULE.AnalysisError as exc:
                error = exc

            self.assertIsNone(error, "an unlocked persistent lock must be reusable")
            self.assertTrue(lock.is_file(), "persistent advisory lock must not be unlinked")
            self.assertEqual(
                lock.read_text(encoding="utf-8"),
                "previous owner\n",
                "advisory locking must not rewrite an existing lock pathname",
            )

    def test_lock_cleanup_never_deletes_substituted_external_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "plan.json"
            review = root / "review"
            lock = root / ".plan.json.lock"
            displaced_lock = root / "owned-lock.recovered"

            with MODULE._artifact_locks(output, review):
                lock.replace(displaced_lock)
                lock.write_text("external-lock", encoding="utf-8")

            self.assertTrue(lock.is_file(), "substituted external lock file must survive")
            self.assertEqual(lock.read_text(encoding="utf-8"), "external-lock")
            self.assertTrue(displaced_lock.is_file())

    def test_successful_force_cleanup_never_deletes_substituted_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged_plan = root / "staged-plan.json"
            staged_plan.write_text("new-plan", encoding="utf-8")
            staged_review = root / "staged-review"
            staged_review.mkdir()
            output = root / "plan.json"
            output.write_text("old-plan", encoding="utf-8")
            review = root / "review"
            review.mkdir()
            (review / "old.txt").write_text("old-review", encoding="utf-8")
            external = root / "external-backup"
            external.mkdir()
            (external / "external.txt").write_text("external", encoding="utf-8")
            displaced = root / "owned-backup.recovered"
            real_report = MODULE._report_retained
            substituted = False

            def substitute_before_report(path, purpose):
                nonlocal substituted
                cleanup_path = Path(path)
                if not substituted and purpose == "successful-force backup":
                    substituted = True
                    cleanup_path.replace(displaced)
                    external.replace(cleanup_path)
                return real_report(cleanup_path, purpose)

            with mock.patch.object(
                MODULE, "_report_retained", side_effect=substitute_before_report
            ):
                MODULE._publish_artifacts(
                    staged_plan, staged_review, output, review, force=True
                )

            self.assertTrue(substituted, "test must inject at the live report boundary")
            self.assertTrue(
                any(item.name == "external.txt" for item in root.rglob("external.txt")),
                "concurrent backup substitution must survive",
            )
            self.assertTrue(displaced.is_dir())

    def test_staging_cleanup_never_deletes_substituted_external_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            output = root / "plan.json"
            review = root / "review"
            external = root / "external-stage"
            external.mkdir()
            (external / "external-marker").write_text("external", encoding="utf-8")
            displaced = root / "owned-stage.recovered"
            probe = mock.Mock(
                fps=30.0,
                frame_count=30,
                duration=1.0,
                start_time=0.0,
                width=160,
                height=90,
            )
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            real_report = MODULE._report_retained
            substituted = False

            def substitute_before_report(path, purpose):
                nonlocal substituted
                cleanup_path = Path(path)
                if not substituted and purpose.endswith("staging"):
                    substituted = True
                    cleanup_path.replace(displaced)
                    external.replace(cleanup_path)
                return real_report(cleanup_path, purpose)

            with mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/tool"), \
                mock.patch.object(MODULE, "probe_video", return_value=(probe, [])), \
                mock.patch.object(MODULE, "_run", return_value=completed), \
                mock.patch.object(MODULE, "iter_command_stderr", return_value=iter(())), \
                mock.patch.object(
                    MODULE, "_report_retained", side_effect=substitute_before_report
                ):
                MODULE.run_analysis(source, output, review, "fine", force=False)

            self.assertTrue(substituted, "test must inject at the live report boundary")
            self.assertTrue(
                any(item.name == "external-marker" for item in root.rglob("external-marker")),
                "concurrent staging substitution must survive",
            )
            self.assertTrue(displaced.is_dir())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
    def test_synthetic_video_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "synthetic.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=red:s=160x90:r=10:d=0.5",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=160x90:r=10:d=0.5",
                    "-filter_complex",
                    "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                    "-map",
                    "[v]",
                    "-c:v",
                    "libx264",
                    "-g",
                    "30",
                    "-pix_fmt",
                    "yuv420p",
                    "-output_ts_offset",
                    "5",
                    "-y",
                    str(source),
                ],
                check=True,
            )
            output = root / "plan.json"
            review = root / "review"
            plan = MODULE.run_analysis(source, output, review, "fine", force=False)
            self.assertTrue(output.is_file())
            self.assertTrue((review / "index.json").is_file())
            self.assertTrue((review / "sheet-001.png").is_file())
            self.assertTrue(plan["candidates"])
            self.assertTrue(plan["requires_visual_review"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
    def test_review_pipeline_emits_partial_final_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=s=160x90:r=10:d=2",
                    "-pix_fmt",
                    "yuv420p",
                    "-y",
                    str(source),
                ],
                check=True,
            )
            candidates = [
                MODULE.Candidate(frame, frame / 10, 1.0, ["ydif"])
                for frame in range(1, 12)
            ]
            review = root / "review"
            MODULE.render_review(
                source, candidates, review, width=160, height=90, fps=10.0
            )
            self.assertGreater((review / "sheet-001.png").stat().st_size, 0)
            self.assertGreater((review / "sheet-002.png").stat().st_size, 0)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
    def test_real_23976_mkv_timestamp_quantization_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "quantized.mkv"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=s=160x90:r=24000/1001:d=1",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-y",
                    str(source),
                ],
                check=True,
            )
            plan = MODULE.run_analysis(
                source, root / "plan.json", root / "review", "fine", force=False
            )
            self.assertAlmostEqual(plan["fps"], 24000 / 1001)
            self.assertTrue((root / "review" / "index.json").is_file())

    def test_missing_ffmpeg_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.touch()
            with mock.patch.object(MODULE.shutil, "which", return_value=None):
                with self.assertRaisesRegex(MODULE.AnalysisError, "Install ffmpeg"):
                    MODULE.run_analysis(source, root / "plan.json", root / "review", "fine", force=False)

    def test_cli_error_explains_retained_hidden_artifact_cleanup(self):
        stderr = io.StringIO()
        with mock.patch.object(
            MODULE, "run_analysis", side_effect=MODULE.AnalysisError("failed")
        ), mock.patch.object(sys, "stderr", stderr):
            result = MODULE.main(
                [
                    "source.mp4",
                    "--output",
                    "plan.json",
                    "--review-dir",
                    "review",
                ]
            )

        self.assertEqual(result, 2)
        message = stderr.getvalue().lower()
        self.assertIn("retained hidden artifacts are deliberate", message)
        self.assertIn("manually inspect", message)
        self.assertIn("before deleting", message)


if __name__ == "__main__":
    unittest.main()
