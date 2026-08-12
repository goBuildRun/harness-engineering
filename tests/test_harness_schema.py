#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_runtime import default_result  # noqa: E402
from harness_schema import assert_result, validate_result  # noqa: E402


class HarnessSchemaTest(unittest.TestCase):
    def test_default_result_satisfies_schema(self) -> None:
        self.assertEqual(validate_result(default_result("task-1")), [])

    def test_invalid_state_tier_and_check_are_rejected(self) -> None:
        result = default_result("task-1")
        result["state"] = "done"
        result["tier"]["effective"] = "tiny"
        result["checks"]["tests"] = {"decision": "pass"}
        with self.assertRaisesRegex(ValueError, "RESULT_SCHEMA_INVALID"):
            assert_result(result)

    def test_complete_telemetry_cannot_contain_unknown(self) -> None:
        result = default_result("task-1")
        result["cost"]["telemetry_complete"] = True
        self.assertIn("cost.telemetry_complete_unknown", validate_result(result))

    def test_negative_or_boolean_cost_is_rejected(self) -> None:
        result = default_result("task-1")
        result["cost"]["harness"]["agent_calls"] = -1
        result["cost"]["harness"]["reruns"] = True
        issues = validate_result(result)
        self.assertIn("cost.harness.agent_calls", issues)
        self.assertIn("cost.harness.reruns", issues)

    def test_invalid_cache_hits_is_rejected(self) -> None:
        result = default_result("task-1")
        result["cost"]["harness"]["cache_hits"] = True
        self.assertIn("cost.harness.cache_hits", validate_result(result))


if __name__ == "__main__":
    unittest.main()
