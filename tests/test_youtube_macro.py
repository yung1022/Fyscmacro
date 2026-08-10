#!/usr/bin/env python3
"""Unit tests that do not hit the network."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bot" / "youtube_macro.py"

spec = importlib.util.spec_from_file_location("youtube_macro", MODULE_PATH)
assert spec and spec.loader
youtube_macro = importlib.util.module_from_spec(spec)
sys.modules["youtube_macro"] = youtube_macro
spec.loader.exec_module(youtube_macro)


class ParseTests(unittest.TestCase):
    def test_video_id_from_url(self) -> None:
        self.assertEqual(
            youtube_macro.extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(
            youtube_macro.extract_video_id("https://youtu.be/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(
            youtube_macro.extract_video_id("https://www.youtube.com/live/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(youtube_macro.extract_video_id("dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_channel_handle(self) -> None:
        self.assertEqual(
            youtube_macro.extract_channel_handle("https://www.youtube.com/@SomeCreator"),
            "SomeCreator",
        )
        self.assertEqual(youtube_macro.extract_channel_handle("@SomeCreator"), "SomeCreator")

    def test_channel_id(self) -> None:
        cid = "UCabcdefghijklmnopqrstuv"
        self.assertEqual(len(cid), 24)
        self.assertEqual(youtube_macro.extract_channel_id(cid), cid)
        self.assertEqual(
            youtube_macro.extract_channel_id(f"https://www.youtube.com/channel/{cid}"),
            cid,
        )


class DryRunTests(unittest.TestCase):
    def test_dry_run_sends_expected_count(self) -> None:
        args = youtube_macro.parse_args(
            [
                "--video",
                "dQw4w9WgXcQ",
                "--command",
                "!join",
                "--interval-seconds",
                "1",
                "--duration-minutes",
                "0",
                "--count",
                "3",
                "--dry-run",
            ]
        )
        with mock.patch.object(youtube_macro.time, "sleep", return_value=None):
            code = youtube_macro.run(args)
        self.assertEqual(code, 0)

    def test_rejects_tiny_interval(self) -> None:
        args = youtube_macro.parse_args(
            [
                "--channel",
                "@someone",
                "--command",
                "!join",
                "--interval-seconds",
                "0.5",
                "--count",
                "1",
                "--dry-run",
            ]
        )
        with self.assertRaises(SystemExit):
            youtube_macro.validate(args)

    def test_requires_video_or_channel(self) -> None:
        args = youtube_macro.parse_args(
            ["--command", "!join", "--count", "1", "--dry-run"]
        )
        with self.assertRaises(SystemExit):
            youtube_macro.validate(args)


if __name__ == "__main__":
    unittest.main()
