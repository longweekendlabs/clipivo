from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path

from core.naming import output_base_name, video_id
from core.timecode import format_remaining


class NamingTests(unittest.TestCase):
    when = datetime(2026, 10, 21, 13, 5, 9)
    youtube = Path("30 Dakika - Lojistik ｜ 6 Haziran 2024-cxbSL8_pOKk.mp4")

    def test_video_id_from_common_downloader_names(self) -> None:
        self.assertEqual(video_id(self.youtube.stem), "cxbSL8_pOKk")
        self.assertEqual(video_id("Some talk [dQw4w9WgXcQ]"), "dQw4w9WgXcQ")
        self.assertEqual(video_id("holiday video"), "")

    def test_schemes(self) -> None:
        self.assertEqual(output_base_name(self.youtube, "source", when=self.when), self.youtube.stem)
        self.assertEqual(
            output_base_name(self.youtube, "source_date", when=self.when), f"{self.youtube.stem}_20261021-130509"
        )
        self.assertEqual(output_base_name(self.youtube, "custom_id", "news", when=self.when), "news.cxbSL8_pOKk")
        self.assertEqual(
            output_base_name(self.youtube, "custom_id_date", "news anchor", when=self.when),
            "news_anchor.cxbSL8_pOKk.20261021-130509",
        )

    def test_without_an_id_the_video_name_stands_in_and_person_is_appended(self) -> None:
        self.assertEqual(
            output_base_name(Path("holiday video.mkv"), "custom_id", "", "Merve Ekinci", self.when),
            "clipivo.holiday_video_Merve_Ekinci",
        )


class RemainingTests(unittest.TestCase):
    def test_rounded_time_left(self) -> None:
        self.assertEqual(format_remaining(20_000), "under a minute")
        self.assertEqual(format_remaining(12 * 60_000), "12 min")
        self.assertEqual(format_remaining(125 * 60_000), "2 h 05 min")


if __name__ == "__main__":
    unittest.main()
