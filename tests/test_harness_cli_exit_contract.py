#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import harness_gates  # noqa: E402


class HarnessCliExitContractTest(unittest.TestCase):
    def test_standalone_gate_plan_uses_decision_as_exit_status(self) -> None:
        with mock.patch.object(sys, "argv", ["harness_gates.py"]), mock.patch.object(
            harness_gates, "run_gate_plan",
            return_value={"decision": "block", "checks": {}, "missing": ["harness"]},
        ), mock.patch.object(harness_gates, "dump_json"):
            self.assertEqual(harness_gates.main(), 1)

    def test_block_decision_returns_one_and_pass_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            command = [
                "python3", str(SCRIPTS / "harness_runtime.py"),
                "--harness-root", str(ROOT), "--product-root", str(product),
            ]
            blocked = subprocess.run(
                [*command, "start", "task", "--kind", "hotfix", "--scope", "."],
                text=True, capture_output=True, check=False,
            )
            passed = subprocess.run(
                [*command, "start", "task", "--tier", "lite", "--scope", "."],
                text=True, capture_output=True, check=False,
            )
        self.assertEqual(json.loads(blocked.stdout)["decision"], "block")
        self.assertEqual(blocked.returncode, 1)
        self.assertEqual(json.loads(passed.stdout)["decision"], "pass")
        self.assertEqual(passed.returncode, 0)

    def test_standalone_efficiency_clis_align_block_decision_and_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            progress = product / "harness-workspace/evidence/progress"
            progress.mkdir(parents=True)
            (progress / "block-GROWTH-CAPTURE.md").write_text(
                "- **Release impact**: blocker\n", encoding="utf-8",
            )
            commands = (
                [
                    "python3", str(SCRIPTS / "harness_provider_preflight.py"),
                    "--contract", str(product / "missing-contract.json"),
                    "--adapter", str(product / "missing-adapter.py"),
                    "--expected-subject", "subject",
                    "--provider", "mock", "--", "missing-adapter",
                ],
                [
                    "python3", str(SCRIPTS / "harness_growth_release.py"),
                    "--harness-root", str(ROOT), "--product-root", str(product),
                ],
                [
                    "python3", str(SCRIPTS / "harness_lifecycle_preflight.py"),
                    "--product-root", str(product), "--provider", "jira",
                ],
                [
                    "python3", str(SCRIPTS / "story_cycle_benchmark.py"),
                    "--fixture", str(product / "missing-benchmark.json"),
                ],
            )
            completed = [
                subprocess.run(command, text=True, capture_output=True, check=False)
                for command in commands
            ]
        self.assertTrue(all(item.returncode == 1 for item in completed))
        self.assertTrue(all(json.loads(item.stdout)["decision"] == "block" for item in completed))


if __name__ == "__main__":
    unittest.main()
