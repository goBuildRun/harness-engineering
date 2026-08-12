#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".harness" / "scripts"))

from harness_gc_context import build_gc_context  # noqa: E402


class HarnessGcContextTest(unittest.TestCase):
    def test_context_contains_real_diff_untracked_content_and_direct_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo, check=True)
            (repo / "lib.py").write_text("VALUE = 1\n")
            (repo / "main.py").write_text("import lib\nprint(lib.VALUE)\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            (repo / "main.py").write_text("import lib\nprint(lib.VALUE + 1)\n")
            (repo / "new.py").write_text("print('new')\n")
            result = {"task_id": "task-1", "policy_digest": "policy-a"}
            context, chars = build_gc_context(
                result, {"triggers": ["cross_module"]}, ["main.py", "new.py"], repo,
            )
            self.assertIn("+print(lib.VALUE + 1)", context["actual_diff"])
            self.assertIn("print('new')", context["actual_diff"])
            self.assertEqual(context["direct_dependencies"], ["lib.py"])
            self.assertGreater(chars, len(context["actual_diff"]))

    def test_context_over_budget_fails_without_truncating_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"HARNESS_GC_CONTEXT_MAX_CHARS": "10"}
        ):
            repo = Path(tmp)
            with self.assertRaisesRegex(ValueError, "GC_CONTEXT_BUDGET_EXCEEDED"):
                build_gc_context(
                    {"task_id": "task-1", "policy_digest": "policy-a"},
                    {"triggers": []}, [], repo,
                )


if __name__ == "__main__":
    unittest.main()
