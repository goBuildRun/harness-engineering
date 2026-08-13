#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".harness/scripts"))

from harness_metrics import summarize  # noqa: E402


class HarnessMetricsTest(unittest.TestCase):
    def test_unknown_baseline_never_fakes_rollout_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outcome = summarize(Path(tmp), ROOT / "tests/fixtures/lean-cost-baseline.json")
            self.assertEqual(outcome["decision"], "insufficient_data")
            self.assertIsNone(outcome["comparisons"]["duration_reduction"])

    def test_real_numeric_samples_are_aggregated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.json"
            baseline.write_text(json.dumps({"harness": {
                "context_chars": 1000, "gate_duration_ms": 1000, "default_evidence_types": 10,
            }}))
            results = root / "results"
            for index in range(5):
                task = results / str(index)
                task.mkdir(parents=True)
                (task / "result.json").write_text(json.dumps({
                    "tier": {"initial": "lite", "effective": "lite"}, "decision": "pass",
                    "checks": {str(i): {} for i in range(5)},
                    "cost": {"harness": {"context_chars": 500, "gate_duration_ms": 600,
                                                   "reruns": 0}},
                }))
            outcome = summarize(results, baseline)
            self.assertEqual(outcome["current"]["lite_samples"], 5)
            self.assertEqual(outcome["comparisons"]["context_reduction"], 0.5)
            self.assertEqual(outcome["comparisons"]["evidence_reduction"], 0.5)
            self.assertEqual(outcome["decision"], "pass")


if __name__ == "__main__":
    unittest.main()
