#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_runtime import default_result  # noqa: E402
from provider_lifecycle import build_receipt  # noqa: E402


class ProviderLifecycleTest(unittest.TestCase):
    def passing_result(self):
        result = default_result("task-1")
        result.update({
            "decision": "pass", "state": "validated",
            "subject": {"kind": "commit", "digest": "a" * 40},
            "work_item": {"id": "WI-42", "provider": "jira"},
            "policy_digest": "b" * 64, "binding_digest": "c" * 64,
        })
        result["invariants"] = {name: "pass" for name in result["invariants"]}
        return result

    def test_receipt_binds_separate_work_item_and_commit(self) -> None:
        receipt = build_receipt(
            self.passing_result(), event="merge", commit="a" * 40,
            repository="org/repo", run_id="123",
        )
        self.assertEqual(receipt["work_item_id"], "WI-42")
        self.assertEqual(receipt["commit_sha"], "a" * 40)
        self.assertEqual(receipt["check_status"], "success")
        self.assertEqual(receipt["task_id"], "task-1")
        self.assertEqual(receipt["policy_digest"], "b" * 64)
        self.assertEqual(len(receipt["result_digest"]), 64)
        for field in ("task_id", "policy_digest", "binding_digest", "result_digest"):
            self.assertIn(f'"{field}"', (SCRIPTS / "provider_lifecycle.py").read_text())

    def test_stale_subject_or_missing_work_item_blocks(self) -> None:
        with self.assertRaisesRegex(ValueError, "SUBJECT_MISMATCH"):
            build_receipt(
                self.passing_result(), event="merge", commit="b" * 40,
                repository="org/repo", run_id="123",
            )
        result = self.passing_result()
        result["work_item"] = None
        with self.assertRaisesRegex(ValueError, "WORK_ITEM_BINDING_MISSING"):
            build_receipt(
                result, event="merge", commit="a" * 40,
                repository="org/repo", run_id="123",
            )

    def test_failed_result_or_incomplete_provider_binding_blocks(self) -> None:
        result = self.passing_result()
        result["decision"] = "block"
        with self.assertRaisesRegex(ValueError, "RESULT_NOT_PASS"):
            build_receipt(
                result, event="merge", commit="a" * 40,
                repository="org/repo", run_id="123",
            )

    def test_missing_policy_or_binding_blocks_terminal_receipt(self) -> None:
        for field in ("policy_digest", "binding_digest"):
            result = self.passing_result()
            result[field] = ""
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "BINDING_MISSING"):
                build_receipt(
                    result, event="merge", commit="a" * 40,
                    repository="org/repo", run_id="123",
                )
        result = self.passing_result()
        result["work_item"] = {"id": "WI-42", "provider": ""}
        with self.assertRaisesRegex(ValueError, "WORK_ITEM_BINDING_MISSING"):
            build_receipt(
                result, event="merge", commit="a" * 40,
                repository="org/repo", run_id="123",
            )


if __name__ == "__main__":
    unittest.main()
