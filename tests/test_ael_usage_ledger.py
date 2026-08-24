#!/usr/bin/env python3
from __future__ import annotations

import unittest

from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ael_usage_ledger import aggregate_epic_usage, apply_story_usage, capture_usage_baseline  # noqa: E402


def receipt(input_tokens: int, output_tokens: int, *, session: str = "session-1") -> dict:
    return {
        "implementation": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "source": {"session_id": session, "usage_event_at": "2026-08-13T00:00:00Z"},
    }


class HarnessUsageLedgerTest(unittest.TestCase):
    def test_story_usage_is_delta_between_same_session_endpoints(self) -> None:
        result: dict = {}
        capture_usage_baseline(result, receipt(100, 20))
        apply_story_usage(result, receipt(175, 45))
        self.assertEqual(result["cost"]["story"]["input_tokens"], 75)
        self.assertEqual(result["cost"]["story"]["output_tokens"], 25)
        self.assertEqual(result["cost"]["story"]["status"], "exact")

    def test_missing_baseline_and_session_switch_fail_closed(self) -> None:
        missing: dict = {}
        apply_story_usage(missing, receipt(175, 45))
        self.assertEqual(missing["cost"]["story"]["status"], "baseline_missing")
        switched: dict = {}
        capture_usage_baseline(switched, receipt(100, 20, session="one"))
        apply_story_usage(switched, receipt(175, 45, session="two"))
        self.assertEqual(switched["cost"]["story"]["status"], "session_mismatch")

    def test_epic_aggregates_only_exact_bound_stories(self) -> None:
        rows = [
            {"task": {"epic_id": "epic-45"}, "cost": {"story": {"status": "exact", "input_tokens": 75, "output_tokens": 25}}},
            {"task": {"epic_id": "epic-45"}, "cost": {"story": {"status": "baseline_missing", "input_tokens": "unknown", "output_tokens": "unknown"}}},
            {"task": {"epic_id": "other"}, "cost": {"story": {"status": "exact", "input_tokens": 999, "output_tokens": 999}}},
        ]
        aggregate = aggregate_epic_usage("epic-45", rows)
        self.assertEqual(aggregate["input_tokens"], 75)
        self.assertEqual(aggregate["output_tokens"], 25)
        self.assertEqual(aggregate["exact_story_count"], 1)
        self.assertEqual(aggregate["incomplete_story_count"], 1)


if __name__ == "__main__":
    unittest.main()
