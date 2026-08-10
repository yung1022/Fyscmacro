#!/usr/bin/env python3
"""Unit tests that do not require real YouTube credentials."""

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
        self.assertEqual(
            youtube_macro.extract_video_id(
                "https://www.youtube.com/live/dQw4w9WgXcQ"
            ),
            "dQw4w9WgXcQ",
        )

    def test_channel_handle(self) -> None:
        self.assertEqual(
            youtube_macro.extract_channel_handle(
                "https://www.youtube.com/@SomeCreator"
            ),
            "SomeCreator",
        )
        self.assertEqual(
            youtube_macro.extract_channel_handle("@SomeCreator"), "SomeCreator"
        )


class ProtobufTests(unittest.TestCase):
    def test_send_message_params_stable(self) -> None:
        channel = "UCabcdefghijklmnopqrstuv"
        video = "dQw4w9WgXcQ"
        params = youtube_macro.send_message_params(channel, video)
        # B64Type.B2: base64(urlencode(base64(protobuf)))
        decoded = base64.b64decode(params).decode("ascii")
        inner = base64.b64decode(urllib.parse.unquote(decoded))
        # Must contain channel + video as utf-8 substrings inside protobuf
        self.assertIn(channel.encode(), inner)
        self.assertIn(video.encode(), inner)
        # Starts with field1 length-delimited
        self.assertEqual(inner[0], (1 << 3) | 2)

    def test_sapisidhash_format(self) -> None:
        token = youtube_macro.sapisidhash("test-sapisid")
        self.assertTrue(token.startswith("SAPISIDHASH "))
        ts, digest = token.split(" ", 1)[1].split("_", 1)
        self.assertTrue(ts.isdigit())
        self.assertEqual(len(digest), 40)


class CookieTests(unittest.TestCase):
    def test_pick_sapisid_prefers_sapisid(self) -> None:
        cookies = {
            "SAPISID": "a",
            "__Secure-3PAPISID": "b",
        }
        self.assertEqual(youtube_macro.pick_sapisid(cookies), "a")

    def test_pick_secure_fallback(self) -> None:
        cookies = {"__Secure-3PAPISID": "secure"}
        self.assertEqual(youtube_macro.pick_sapisid(cookies), "secure")

    def test_missing_sapisid(self) -> None:
        with self.assertRaises(youtube_macro.InnerTubeError):
            youtube_macro.pick_sapisid({"SID": "x"})


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

    def test_requires_cookies_when_not_dry_run(self) -> None:
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
        with self.assertRaises(SystemExit):
            youtube_macro.validate(args)


if __name__ == "__main__":
    unittest.main()
