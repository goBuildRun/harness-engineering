#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ci_binding import parse_binding  # noqa: E402


class CiBindingTest(unittest.TestCase):
    def test_parses_unique_binding(self) -> None:
        binding = parse_binding(
            "Summary\n\nHarness-Task: WI-42\nHarness-Scope: src/auth\nHarness-Tier: strict\n"
        )
        self.assertEqual(binding, {"task_id": "WI-42", "scope": "src/auth", "tier": "strict"})

    def test_missing_or_duplicate_binding_blocks(self) -> None:
        with self.assertRaisesRegex(ValueError, "CI_BINDING_MISSING"):
            parse_binding("no metadata")
        with self.assertRaisesRegex(ValueError, "CI_BINDING_AMBIGUOUS"):
            parse_binding("Harness-Task: A\nHarness-Task: B\nHarness-Scope: src\n")

    def test_scope_traversal_blocks(self) -> None:
        with self.assertRaisesRegex(ValueError, "CI_BINDING_SCOPE_INVALID"):
            parse_binding("Harness-Task: A\nHarness-Scope: ../src\n")


if __name__ == "__main__":
    unittest.main()
