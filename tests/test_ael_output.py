#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from ael_output import dump_json  # noqa: E402


class FakeStdout(io.StringIO):
    def __init__(self, is_tty: bool) -> None:
        super().__init__()
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


class HarnessOutputTest(unittest.TestCase):
    def test_dump_json_stays_compact_when_captured(self) -> None:
        stdout = FakeStdout(False)
        with patch("sys.stdout", stdout):
            dump_json({"decision": "pass", "reason": "OK", "items": [1, 2]})

        text = stdout.getvalue()
        self.assertEqual(text.count("\n"), 1)
        self.assertEqual(json.loads(text)["decision"], "pass")

    def test_dump_json_pretty_prints_for_tty(self) -> None:
        stdout = FakeStdout(True)
        with patch("sys.stdout", stdout):
            dump_json({"decision": "pass", "reason": "OK", "items": [1, 2]})

        text = stdout.getvalue()
        self.assertGreater(text.count("\n"), 1)
        self.assertIn('  "decision": "pass"', text)
        self.assertEqual(json.loads(text)["items"], [1, 2])

    def test_harness_pretty_can_disable_tty_pretty(self) -> None:
        stdout = FakeStdout(True)
        with patch("sys.stdout", stdout), patch.dict("os.environ", {"AEL_PRETTY": "0"}):
            dump_json({"decision": "pass", "reason": "OK"})

        text = stdout.getvalue()
        self.assertEqual(text.count("\n"), 1)
        self.assertEqual(json.loads(text)["reason"], "OK")


if __name__ == "__main__":
    unittest.main()
