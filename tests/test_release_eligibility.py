#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".harness/scripts/release_eligibility.py"
sys.path.insert(0, str(SCRIPT.parent))

from harness_runtime import default_result  # noqa: E402


class ReleaseEligibilityTest(unittest.TestCase):
    def result(self) -> dict:
        result = default_result("task-1")
        result.update({
            "decision": "pass", "state": "validated",
            "subject": {"kind": "commit", "digest": "a" * 40},
            "work_item": {"id": "WI-42", "provider": "jira"},
            "policy_digest": "b" * 64, "binding_digest": "c" * 64,
        })
        result["invariants"] = {name: "pass" for name in result["invariants"]}
        return result

    def test_builder_emits_result_bound_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "result.json", Path(tmp) / "receipt.json"
            source.write_text(json.dumps(self.result()))
            completed = subprocess.run([
                "python3", str(SCRIPT), "--result", str(source), "--commit", "a" * 40,
                "--repository", "org/repo", "--required-run-id", "123", "--output", str(target),
            ], text=True, stdout=subprocess.PIPE, check=False)
            receipt = json.loads(target.read_text())
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(receipt["kind"], "release-eligibility")
        self.assertEqual(receipt["task_id"], "task-1")
        self.assertEqual(receipt["policy_digest"], "b" * 64)
        self.assertEqual(receipt["required_run_id"], "123")

    def test_builder_rejects_result_for_another_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "result.json", Path(tmp) / "receipt.json"
            source.write_text(json.dumps(self.result()))
            completed = subprocess.run([
                "python3", str(SCRIPT), "--result", str(source), "--commit", "d" * 40,
                "--repository", "org/repo", "--required-run-id", "123", "--output", str(target),
            ], text=True, stdout=subprocess.PIPE, check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(target.exists())

    def test_lite_result_without_work_item_can_be_release_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "result.json", Path(tmp) / "receipt.json"
            result = self.result()
            result["work_item"] = None
            source.write_text(json.dumps(result))
            completed = subprocess.run([
                "python3", str(SCRIPT), "--result", str(source), "--commit", "a" * 40,
                "--repository", "org/repo", "--required-run-id", "123", "--output", str(target),
            ], text=True, stdout=subprocess.PIPE, check=False)
            self.assertEqual(completed.returncode, 0)
            self.assertTrue(target.is_file())


if __name__ == "__main__":
    unittest.main()
