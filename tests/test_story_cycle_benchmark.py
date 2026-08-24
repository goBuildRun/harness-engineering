#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from story_cycle_benchmark import benchmark  # noqa: E402


class StoryCycleBenchmarkTest(unittest.TestCase):
    def test_offline_fixture_measures_parallel_and_cache_reduction(self) -> None:
        fixture = json.loads((ROOT / "tests/fixtures/story-43-5-efficiency.json").read_text())
        result = benchmark(fixture)
        self.assertEqual(result["historical_story_wall_ms"], 34_476_120)
        self.assertGreaterEqual(result["offline_before_serial_ms"], 350)
        self.assertLess(result["offline_after_parallel_ms"], result["offline_before_serial_ms"])
        self.assertLess(
            result["offline_after_unrelated_rerun_ms"], result["offline_before_serial_ms"],
        )
        self.assertGreaterEqual(result["first_run_reduction"], 0.55)
        self.assertEqual(result["observed_cache_hits"], 4)
        self.assertEqual(result["orchestration_executor"], "execute_specs")
        self.assertEqual(result["production_p95"], "unknown")


if __name__ == "__main__":
    unittest.main()
