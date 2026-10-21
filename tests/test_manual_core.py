from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import core.export as export_module
from core.export import date_tag, export_ranges
from core.filters import (
    VideoFilter,
    audio_filtergraph,
    create_filter,
    filtered_dimensions,
    video_filtergraph,
)
from core.models import ClipRange
from core.project_state import load_filters, load_ranges, save_filters, save_ranges
from core.timecode import format_timecode
from core.video_controller import VideoController
from core.video_probe import probe_video


class ManualCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="clipivo-tests-")
        cls.root = Path(cls.temporary.name)
        cls.video = cls.root / "source with spaces.mp4"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-g", "25", "-c:a", "aac", str(cls.video),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_timecode(self) -> None:
        self.assertEqual(format_timecode(3_723_004), "01:02:03.004")
        self.assertEqual(format_timecode(None), "--:--:--.---")
        self.assertEqual(date_tag(datetime(2026, 7, 24, 13, 14, 5)), "260724_1314_005")

    def test_probe_and_keyframes(self) -> None:
        info = probe_video(self.video)
        self.assertEqual((info.width, info.height), (640, 360))
        self.assertAlmostEqual(info.fps, 25.0)
        self.assertGreaterEqual(info.duration_ms, 3_900)
        self.assertEqual(info.keyframes_ms[:4], (0, 1000, 2000, 3000))
        self.assertTrue(any(stream.kind == "audio" for stream in info.streams))
        self.assertGreater(info.video_bitrate_kbps, 0)

    def test_exact_frames_reencode_at_high_quality(self) -> None:
        arguments = export_module._video_encoder_arguments("cpu", 1000)
        self.assertEqual(arguments[arguments.index("-crf") + 1], "16")
        self.assertEqual(arguments[arguments.index("-maxrate") + 1], "8000k")
        self.assertEqual(arguments[arguments.index("-profile:v") + 1], "high")
        compact = export_module._video_encoder_arguments("cpu", 3000)
        self.assertEqual(compact[compact.index("-maxrate") + 1], "12000k")
        nvenc = export_module._video_encoder_arguments("nvenc", 1000)
        self.assertEqual(nvenc[nvenc.index("-cq") + 1], "19")
        self.assertEqual(nvenc[nvenc.index("-preset") + 1], "p7")

    def test_original_quality_snaps_starts_to_keyframes_and_merges_overlaps(self) -> None:
        snapped = export_module.snap_to_keyframes(
            self.video, [ClipRange(1500, 1800), ClipRange(1900, 2600), ClipRange(3200, 3600)]
        )
        self.assertEqual([(clip.start_ms, clip.end_ms) for clip in snapped], [(1000, 2600), (3000, 3600)])
        outputs = export_ranges(self.video, snapped, self.root / "copy output", "copy", True, output_name="copy-test")
        self.assertEqual(outputs[0].name, "copy-test.mp4")

    def test_exact_frame_steps(self) -> None:
        controller = VideoController(self.video)
        try:
            first = controller.first_frame()
            second = controller.next_frame(first.timestamp_ms)
            back = controller.previous_frame(second.timestamp_ms)
        finally:
            controller.close()
        self.assertEqual(first.timestamp_ms, 0)
        self.assertEqual(second.timestamp_ms, 40)
        self.assertEqual(back.timestamp_ms, 0)

    def test_scrub_preview_uses_scaled_keyframes(self) -> None:
        controller = VideoController(self.video, keyframes_only=True, preview_max_width=320)
        try:
            preview = controller.frame_at(2_650)
        finally:
            controller.close()
        self.assertEqual((preview.width, preview.height), (320, 180))
        self.assertIn(preview.timestamp_ms, (1000, 2000))

    def test_project_state_is_outside_source_folder(self) -> None:
        data_home = self.root / "data"
        clips = [ClipRange(200, 900)]
        filters = [create_filter("vertical_flip")]
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(data_home)}):
            target = save_ranges(self.video, clips)
            save_filters(self.video, filters)
            restored = load_ranges(self.video)
            restored_filters = load_filters(self.video)
        self.assertTrue(target.is_relative_to(data_home))
        self.assertEqual([(item.start_ms, item.end_ms) for item in restored], [(200, 900)])
        self.assertEqual([item.kind for item in restored_filters], ["vertical_flip"])
        self.assertFalse(self.video.with_suffix(self.video.suffix + ".clipivo.json").exists())

    def test_separate_copy_and_accurate_stitched_export(self) -> None:
        clips = [ClipRange(0, 1000), ClipRange(2000, 3200)]
        copy_outputs = export_ranges(self.video, clips, self.root / "copy output", "copy", False)
        stitched_outputs = export_ranges(self.video, clips, self.root / "accurate output", "accurate", True)
        self.assertEqual(len(copy_outputs), 2)
        self.assertEqual(len(stitched_outputs), 1)
        self.assertRegex(copy_outputs[0].name, r"^source with spaces_\d{6}_\d{4}_\d{3}_001\.mp4$")
        self.assertNotIn("clip", copy_outputs[0].name)
        self.assertRegex(stitched_outputs[0].name, r"^source with spaces_\d{6}_\d{4}_\d{3}\.mp4$")
        for output in [*copy_outputs, *stitched_outputs]:
            self.assertTrue(output.is_file())
            info = probe_video(output, include_keyframes=False)
            self.assertTrue(any(stream.kind == "audio" for stream in info.streams))

    def test_source_copy_mp4_can_exclude_audio(self) -> None:
        outputs = export_ranges(
            self.video,
            [ClipRange(0, 1000)],
            self.root / "silent output",
            "copy",
            False,
            include_audio=False,
        )
        self.assertEqual(outputs[0].suffix, ".mp4")
        info = probe_video(outputs[0], include_keyframes=False)
        self.assertFalse(any(stream.kind == "audio" for stream in info.streams))

    def test_generated_batch_output_ends_with_character_name(self) -> None:
        outputs = export_ranges(
            self.video,
            [ClipRange(0, 800), ClipRange(1200, 2000)],
            self.root / "character output",
            "accurate",
            False,
            output_name="news.gvj96ngMgwY",
        )
        self.assertEqual([path.stem for path in outputs], ["news.gvj96ngMgwY_001", "news.gvj96ngMgwY_002"])

    def test_single_clip_can_use_an_explicit_output_filename(self) -> None:
        target = self.root / "chosen name.mp4"
        outputs = export_ranges(
            self.video,
            [ClipRange(0, 1000)],
            target.parent,
            "copy",
            False,
            target=target,
        )
        self.assertEqual(outputs, [target])
        self.assertTrue(target.is_file())

    def test_accurate_export_reports_encoded_time_progress(self) -> None:
        events = []
        export_ranges(
            self.video,
            [ClipRange(0, 3000)],
            self.root / "progress output",
            "accurate",
            False,
            progress=lambda done, total, text: events.append((done, total, text)),
        )
        self.assertTrue(any(0 < done < total for done, total, _text in events))
        self.assertEqual(events[-1][0], events[-1][1])
        self.assertTrue(any("%" in text for _done, _total, text in events))

    def test_auto_export_builds_nvenc_command_and_can_fallback_to_cpu(self) -> None:
        target = self.root / "gpu.mp4"
        with (
            patch.object(export_module, "nvenc_available", return_value=True),
            patch.object(export_module, "_run", side_effect=[export_module.ExportError("GPU failed"), None]) as run,
        ):
            export_ranges(
                self.video,
                [ClipRange(0, 1000)],
                target.parent,
                "accurate",
                False,
                target=target,
                video_encoder="auto",
            )
        self.assertIn("h264_nvenc", run.call_args_list[0].args[0])
        self.assertIn("libx264", run.call_args_list[1].args[0])

    def test_filter_chain_matches_avidemux_order_and_speed_semantics(self) -> None:
        filters = [
            VideoFilter("crop", {"left": 20, "right": 20, "top": 10, "bottom": 10}),
            VideoFilter("rotate", {"angle": 90}),
            VideoFilter("vertical_flip"),
            VideoFilter("contrast", {"contrast": 1.2, "brightness": 0.1}),
            VideoFilter("frame_rate", {"mode": "speed", "fps": 12.5, "blend": False}),
        ]
        graph = video_filtergraph(filters, 25.0)
        self.assertEqual(
            graph,
            "crop=iw-40:ih-20:20:10,transpose=clock,vflip,"
            "lutyuv=y='clip(val*1.20000+12.70000\\,0\\,255)':"
            "u='clip((val-128)*1.20000+128\\,0\\,255)':"
            "v='clip((val-128)*1.20000+128\\,0\\,255)',"
            "setpts=2.00000000*PTS,fps=12.500000",
        )
        self.assertEqual(audio_filtergraph(filters, 25.0), "atempo=0.50000000")
        self.assertEqual(filtered_dimensions(filters, 640, 360), (340, 600))

    def test_contrast_planes_and_color_balance_build_exportable_graphs(self) -> None:
        contrast = create_filter("contrast")
        contrast.params.update(contrast=1.16, brightness=-16 / 127, chroma_u=False)
        balance = create_filter("color_balance")
        balance.params.update(
            shadow_angle=30.0,
            shadow_shift=0.2,
            midtone_saturation=0.15,
            highlight_luma=-0.1,
        )
        graph = video_filtergraph([contrast, balance], 25.0)
        self.assertIn("lutyuv=y=", graph)
        self.assertNotIn(":u=", graph)
        self.assertIn(":v=", graph)
        self.assertIn("colorbalance=", graph)
        self.assertIn("curves=master=", graph)
        self.assertIn("eq=saturation=1.05000", graph)

        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(self.video), "-frames:v", "1", "-vf", graph,
                "-f", "null", "-",
            ],
            check=True,
        )

    def test_filtered_export_reencodes_and_changes_dimensions(self) -> None:
        filters = [
            VideoFilter("crop", {"left": 20, "right": 20, "top": 0, "bottom": 0}),
            VideoFilter("rotate", {"angle": 90}),
            VideoFilter("contrast", {"contrast": 1.1, "brightness": 0.05}),
        ]
        target = self.root / "filtered export.mp4"
        outputs = export_ranges(
            self.video,
            [ClipRange(0, 1000)],
            target.parent,
            "copy",
            False,
            target=target,
            filters=filters,
            source_fps=25.0,
        )
        self.assertEqual(outputs, [target])
        info = probe_video(target, include_keyframes=False)
        self.assertEqual((info.width, info.height), (360, 600))
        self.assertTrue(any(stream.kind == "audio" for stream in info.streams))

    def test_resample_keeps_duration_while_speed_mode_creates_slow_motion(self) -> None:
        clip = [ClipRange(0, 1000)]
        resampled = self.root / "resampled.mp4"
        slowed = self.root / "slowed.mp4"
        export_ranges(
            self.video,
            clip,
            self.root,
            "accurate",
            False,
            target=resampled,
            filters=[VideoFilter("frame_rate", {"mode": "resample", "fps": 12.5, "blend": False})],
            source_fps=25.0,
        )
        export_ranges(
            self.video,
            clip,
            self.root,
            "accurate",
            False,
            target=slowed,
            filters=[VideoFilter("frame_rate", {"mode": "speed", "fps": 12.5, "blend": False})],
            source_fps=25.0,
        )
        resampled_info = probe_video(resampled, include_keyframes=False)
        slowed_info = probe_video(slowed, include_keyframes=False)
        self.assertTrue(850 <= resampled_info.duration_ms <= 1200)
        self.assertTrue(1750 <= slowed_info.duration_ms <= 2200)
        self.assertTrue(any(stream.kind == "audio" for stream in slowed_info.streams))


if __name__ == "__main__":
    unittest.main()
