#!/usr/bin/env python3
"""Offline unit tests (no live YouTube login)."""

from __future__ import annotations

import base64
import importlib.util
import sys
import unittest
import urllib.parse
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
            youtube_macro.extract_video_id(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            ),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(
            youtube_macro.extract_video_id("https://youtu.be/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_channel_handle(self) -> None:
        self.assertEqual(
            youtube_macro.extract_channel_handle("@SomeCreator"), "SomeCreator"
        )


class ProtobufTests(unittest.TestCase):
    def test_send_message_params_stable(self) -> None:
        channel = "UCabcdefghijklmnopqrstuv"
        video = "dQw4w9WgXcQ"
        params = youtube_macro.send_message_params(channel, video)
        decoded = base64.b64decode(params).decode("ascii")
        inner = base64.b64decode(urllib.parse.unquote(decoded))
        self.assertIn(channel.encode(), inner)
        self.assertIn(video.encode(), inner)


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

    def test_no_secrets_required_for_validate(self) -> None:
        args = youtube_macro.parse_args(
            [
                "--video",
                "dQw4w9WgXcQ",
                "--command",
                "!join",
                "--count",
                "1",
            ]
        )
        # Should not raise — auth happens at runtime via phone login
        youtube_macro.validate(args)


class AuthHelperTests(unittest.TestCase):
    def test_gh_notice_prints(self) -> None:
        with mock.patch("builtins.print") as mocked:
            youtube_macro.gh_notice("Phone login", "Open url")
            mocked.assert_called()
            args = mocked.call_args[0][0]
            self.assertIn("::notice", args)


if __name__ == "__main__":
    unittest.main()
