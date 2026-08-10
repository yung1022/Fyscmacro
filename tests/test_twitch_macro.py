#!/usr/bin/env python3
"""Unit tests that do not hit the network."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bot" / "twitch_macro.py"

spec = importlib.util.spec_from_file_location("twitch_macro", MODULE_PATH)
assert spec and spec.loader
twitch_macro = importlib.util.module_from_spec(spec)
sys.modules["twitch_macro"] = twitch_macro
spec.loader.exec_module(twitch_macro)


class NormalizeTests(unittest.TestCase):
    def test_oauth_prefix(self) -> None:
        self.assertEqual(twitch_macro.normalize_oauth("abc"), "oauth:abc")
        self.assertEqual(twitch_macro.normalize_oauth("oauth:abc"), "oauth:abc")

    def test_channel(self) -> None:
        self.assertEqual(twitch_macro.normalize_channel("#CoolStream"), "coolstream")
        self.assertEqual(twitch_macro.normalize_channel("CoolStream"), "coolstream")


class DryRunTests(unittest.TestCase):
    def test_dry_run_sends_expected_count(self) -> None:
        args = twitch_macro.parse_args(
            [
                "--channel",
                "someone",
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
        with mock.patch.object(twitch_macro.time, "sleep", return_value=None):
            code = twitch_macro.run(args)
        self.assertEqual(code, 0)

    def test_rejects_tiny_interval(self) -> None:
        args = twitch_macro.parse_args(
            [
                "--channel",
                "someone",
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
            twitch_macro.validate(args)


if __name__ == "__main__":
    unittest.main()
