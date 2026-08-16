#!/usr/bin/env python3
"""Offline unit tests."""

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


class CookieTests(unittest.TestCase):
    def test_pick_sapisid(self) -> None:
        self.assertEqual(
            youtube_macro.pick_sapisid({"SAPISID": "a", "__Secure-3PAPISID": "b"}),
            "a",
        )

    def test_sapisidhash_format(self) -> None:
        token = youtube_macro.sapisidhash("test")
        self.assertTrue(token.startswith("SAPISIDHASH "))


class ChannelResolveTests(unittest.TestCase):
    def test_extract_from_owner_regex(self) -> None:
        html = '"videoOwnerRenderer":{"title":{"runs":[{"text":"Liliana"}]},"channelId":"UC6SnTw5Tr3b6IoJhwNsQdvg"}'
        cid = youtube_macro.extract_channel_id_from_watch_html(html)
        self.assertEqual(cid, "UC6SnTw5Tr3b6IoJhwNsQdvg")


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
            self.assertEqual(youtube_macro.run(args), 0)

    def test_requires_cookies_when_not_dry_run(self) -> None:
        args = youtube_macro.parse_args(
            ["--video", "dQw4w9WgXcQ", "--command", "!join", "--count", "1"]
        )
        with self.assertRaises(SystemExit):
            youtube_macro.validate(args)

    def test_api_errors_retry_until_duration(self) -> None:
        args = youtube_macro.parse_args(
            [
                "--video",
                "dQw4w9WgXcQ",
                "--command",
                "!join",
                "--interval-seconds",
                "1",
                "--duration-minutes",
                "1",
                "--cookies",
                "SAPISID=test; SID=x",
            ]
        )
        bot = mock.Mock()
        bot.resolve_ids.side_effect = youtube_macro.BotError("resolve fail", status=400)
        clock = {"t": 0.0}

        def mono() -> float:
            return clock["t"]

        def sleep(sec: float) -> None:
            clock["t"] += sec

        with mock.patch.object(youtube_macro, "YouTubeMacro", return_value=bot), mock.patch(
            "youtube_macro.time.monotonic", side_effect=mono
        ), mock.patch("youtube_macro.time.sleep", side_effect=sleep):
            code = youtube_macro.run(args)
        self.assertEqual(code, 0)
        self.assertGreaterEqual(bot.resolve_ids.call_count, 2)
        self.assertGreaterEqual(clock["t"], 60.0)


if __name__ == "__main__":
    unittest.main()
