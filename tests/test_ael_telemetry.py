#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ael_runtime import default_result  # noqa: E402
from ael_telemetry import apply_automatic_usage, apply_gc_telemetry, apply_usage_receipt, enforce_budget  # noqa: E402


class HarnessTelemetryTest(unittest.TestCase):
    def test_exact_receipt_populates_both_cost_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipt = Path(tmp) / "usage.json"
            receipt.write_text(json.dumps({
                "task_id": "cost-task", "subject_digest": "subject-a",
                "policy_digest": "policy-a", "provider": "openai", "model": "codex",
                "implementation": {"input_tokens": 10, "output_tokens": 4, "context_chars": 30, "agent_calls": 1},
                "harness": {"input_tokens": 3, "output_tokens": 1, "context_chars": 8, "agent_calls": 1},
                "source": {
                    "kind": "codex-rollout-token-count-v1", "session_id": "session-1",
                    "rollout_sha256": "a" * 64, "rollout_size_bytes": 42,
                    "usage_event_at": "2026-08-13T00:00:00Z", "cached_input_tokens": 6,
                    "reasoning_output_tokens": 2, "total_tokens": 14,
                    "prompt": "must not be persisted",
                },
            }))
            result = default_result("cost-task")
            self.assertTrue(apply_usage_receipt(
                result, task_id="cost-task", subject_digest="subject-a",
                policy_digest="policy-a", path=str(receipt),
            ))
            self.assertTrue(result["cost"]["telemetry_complete"])
            self.assertEqual(result["cost"]["implementation"]["input_tokens"], 10)
            self.assertEqual(result["cost"]["receipt"]["provider"], "openai")
            self.assertEqual(result["cost"]["receipt"]["source"]["session_id"], "session-1")
            self.assertNotIn("prompt", result["cost"]["receipt"]["source"])

    def test_stale_receipt_blocks_without_faking_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipt = Path(tmp) / "usage.json"
            receipt.write_text(json.dumps({
                "task_id": "old-task", "subject_digest": "old", "policy_digest": "old-policy",
                "provider": "openai", "model": "codex",
            }))
            result = default_result("cost-task")
            self.assertFalse(apply_usage_receipt(
                result, task_id="cost-task", subject_digest="new",
                policy_digest="new-policy", path=str(receipt),
            ))
            self.assertIn("USAGE_RECEIPT_BINDING_MISMATCH", result["blockers"])
            self.assertEqual(result["cost"]["implementation"]["input_tokens"], "unknown")

    @mock.patch("ael_telemetry.automatic_receipt")
    def test_automatic_usage_applies_when_explicit_receipt_is_absent(self, automatic) -> None:
        automatic.return_value = {
            "task_id": "cost-task", "subject_digest": "subject-a", "policy_digest": "policy-a",
            "provider": "openai", "model": "codex",
            "implementation": {"input_tokens": 12, "output_tokens": 3},
            "harness": {},
        }
        result = default_result("cost-task")
        self.assertTrue(apply_automatic_usage(
            result, task_id="cost-task", subject_digest="subject-a", policy_digest="policy-a",
        ))
        self.assertEqual(result["cost"]["implementation"]["input_tokens"], 12)

    def test_receipt_requires_provider_and_model_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipt = Path(tmp) / "usage.json"
            receipt.write_text(json.dumps({
                "task_id": "cost-task", "subject_digest": "subject-a",
                "policy_digest": "policy-a", "implementation": {}, "harness": {},
            }))
            result = default_result("cost-task")
            self.assertFalse(apply_usage_receipt(
                result, task_id="cost-task", subject_digest="subject-a",
                policy_digest="policy-a", path=str(receipt),
            ))
        self.assertIn("USAGE_RECEIPT_SOURCE_MISSING", result["blockers"])

    def test_unknown_receipt_fields_clear_stale_numeric_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipt = Path(tmp) / "usage.json"
            receipt.write_text(json.dumps({
                "task_id": "cost-task", "subject_digest": "subject-a",
                "policy_digest": "policy-a", "provider": "openai", "model": "codex",
                "implementation": {"input_tokens": 12, "output_tokens": 3},
                "harness": {"input_tokens": "unknown", "output_tokens": "unknown"},
            }))
            result = default_result("cost-task")
            result["cost"]["harness"]["input_tokens"] = 0
            result["cost"]["harness"]["output_tokens"] = 0
            apply_usage_receipt(result, task_id="cost-task", subject_digest="subject-a",
                                policy_digest="policy-a", path=str(receipt))
            self.assertEqual(result["cost"]["harness"]["input_tokens"], "unknown")
            self.assertEqual(result["cost"]["harness"]["output_tokens"], "unknown")

    def test_numeric_budget_excess_requires_approval(self) -> None:
        result = default_result("cost-task")
        result["cost"]["implementation"]["agent_calls"] = 2
        result["cost"]["harness"]["agent_calls"] = 1
        with mock.patch.dict(os.environ, {"AEL_BUDGET_AGENT_CALLS": "2"}):
            enforce_budget(result)
        self.assertIn("BUDGET_APPROVAL_REQUIRED", result["blockers"])

    def test_gc_receipt_telemetry_is_added_and_invalid_values_block(self) -> None:
        result = default_result("cost-task")
        self.assertTrue(apply_gc_telemetry(result, {"telemetry": {
            "agent_calls": 1, "context_chars": 123, "duration_ms": 45,
            "provider": "compatible", "model": "gc-model",
        }}))
        self.assertEqual(result["cost"]["harness"]["agent_calls"], 1)
        self.assertEqual(result["cost"]["harness"]["context_chars"], 123)
        self.assertEqual(result["cost"]["harness"]["gate_duration_ms"], 45)
        self.assertEqual(result["checks"]["code_health"]["agent"]["model"], "gc-model")
        self.assertFalse(apply_gc_telemetry(result, {"telemetry": {
            "agent_calls": True, "context_chars": 0, "duration_ms": 0,
        }}))
        self.assertIn("GC_TELEMETRY_INVALID", result["blockers"])


if __name__ == "__main__":
    unittest.main()
