#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_timing import (  # noqa: E402
    FINISH_RETRY_LIMIT,
    UNKNOWN,
    append_event,
    budget_status,
    initialize_cycle,
    read_events,
    register_finish_attempt,
    summarize_events,
)
from harness_cycle_commands import (  # noqa: E402
    begin_stage, close_story_cycle, finish_readiness, finish_stage, start_story_cycle,
)


class HarnessTimingTest(unittest.TestCase):
    def test_parallel_spans_do_not_double_count_root_wall(self) -> None:
        events = [
            {"schema": "harness-story-wall-clock-v1", "event": "start", "span_id": "a", "stage": "gate-a", "epoch_ms": 1000},
            {"schema": "harness-story-wall-clock-v1", "event": "start", "span_id": "b", "stage": "gate-b", "epoch_ms": 1100},
            {"schema": "harness-story-wall-clock-v1", "event": "end", "span_id": "a", "stage": "gate-a", "epoch_ms": 1500, "tool_wait_ms": 500, "decision": "pass", "reason": "OK"},
            {"schema": "harness-story-wall-clock-v1", "event": "end", "span_id": "b", "stage": "gate-b", "epoch_ms": 1700, "tool_wait_ms": 600, "decision": "pass", "reason": "OK"},
        ]
        summary = summarize_events(events)
        self.assertEqual(summary["root_wall_ms"], 700)
        self.assertEqual(summary["covered_wall_ms"], 700)
        self.assertEqual(summary["tool_wait_union_ms"], 700)
        self.assertEqual(summary["agent_active_ms"], UNKNOWN)

    def test_incomplete_span_stays_explicit_unknown(self) -> None:
        summary = summarize_events([{
            "schema": "harness-story-wall-clock-v1", "event": "start",
            "span_id": "open", "stage": "qa", "epoch_ms": 1000,
        }])
        self.assertEqual(summary["incomplete_span_ids"], ["open"])
        self.assertEqual(summary["root_wall_ms"], 0)

    def test_partial_tool_wait_is_not_promoted_to_full_span(self) -> None:
        summary = summarize_events([
            {"event": "start", "span_id": "one", "stage": "implementation_test", "epoch_ms": 0},
            {"event": "end", "span_id": "one", "stage": "implementation_test", "epoch_ms": 1000,
             "tool_wait_ms": 100, "decision": "pass"},
        ])
        self.assertEqual(summary["tool_wait_union_ms"], UNKNOWN)

    def test_ledger_redacts_unstructured_reason_and_ignores_extra_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            append_event(path, {
                "task_id": "T1", "stage": "planning", "event": "end",
                "reason": "secret body should not be stored", "prompt": "private",
            })
            item = read_events(path)[0]
        self.assertEqual(item["reason"], "UNSTRUCTURED_REASON_REDACTED")
        self.assertNotIn("prompt", item)
        self.assertNotIn("private", json.dumps(item))

    def test_budget_and_same_input_retry_limit_stop_before_third_attempt(self) -> None:
        result = {}
        initialize_cycle(result, started_at="2026-08-22T00:00:00.000Z")
        first = register_finish_attempt(result, "subject-a", at="2026-08-22T00:01:00.000Z")
        second = register_finish_attempt(result, "subject-a", at="2026-08-22T00:02:00.000Z")
        third = register_finish_attempt(result, "subject-a", at="2026-08-22T00:03:00.000Z")
        changed = register_finish_attempt(result, "subject-b", at="2026-08-22T00:04:00.000Z")
        self.assertEqual(first["attempt"], 1)
        self.assertEqual(second["attempt"], FINISH_RETRY_LIMIT)
        self.assertEqual(third["reason"], "FINISH_RETRY_LIMIT_EXCEEDED")
        self.assertEqual(changed["decision"], "pass")

    def test_total_budget_exhaustion_is_fail_closed(self) -> None:
        result = {}
        cycle = initialize_cycle(result, started_at="2026-08-22T00:00:00.000Z")
        status = budget_status(cycle, at="2026-08-22T00:30:00.000Z")
        attempt = register_finish_attempt(result, "subject", at="2026-08-22T00:30:00.000Z")
        self.assertEqual(status["reason"], "STORY_BUDGET_EXCEEDED")
        self.assertEqual(attempt["decision"], "block")
        self.assertEqual(result["cycle"]["finish_attempts"], [])

    def test_stage_dependencies_budget_and_story_end_are_mechanical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            result = {"work_item": {"id": "WI-1"}}
            start_story_cycle(result, path, "task-1", result["work_item"])
            self.assertEqual(
                begin_stage(result, path, "task-1", "planning")["reason"],
                "STAGE_ALREADY_ACTIVE",
            )
            self.assertEqual(
                finish_stage(
                    result, path, "task-1", "takeover",
                    decision="pass", reason="STAGE_COMPLETED",
                )["decision"],
                "pass",
            )
            self.assertEqual(
                begin_stage(result, path, "task-1", "implementation_test")["reason"],
                "STAGE_DEPENDENCY_INCOMPLETE",
            )
            for stage in (
                "planning", "implementation_test", "independent_qa", "deploy_provider", "finalize",
            ):
                self.assertEqual(begin_stage(result, path, "task-1", stage)["decision"], "pass")
                self.assertEqual(finish_stage(
                    result, path, "task-1", stage,
                    decision="pass", reason="STAGE_COMPLETED",
                )["decision"], "pass")
            before_finish = summarize_events(read_events(path.parent / "wall-clock-ledger.jsonl"))
            self.assertEqual(finish_readiness(result)["decision"], "pass")
            self.assertNotIn("root_wall_ms", result["cycle"])
            self.assertEqual(close_story_cycle(
                result, path, "task-1", "WI-1",
            )["reason"], "STORY_READY_TO_RELEASE")
            summary = summarize_events(read_events(path.parent / "wall-clock-ledger.jsonl"))
        self.assertEqual(before_finish["incomplete_span_ids"], ["story"])
        self.assertEqual(summary["incomplete_span_ids"], [])
        self.assertIn("root_wall_ms", result["cycle"])

    def test_stage_overrun_blocks_retry_progression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            result = {"work_item": {"id": "WI-1"}}
            start_story_cycle(result, path, "task-1", result["work_item"])
            attempt = result["cycle"]["stages"]["takeover"]["attempts"][-1]
            attempt["started_epoch_ms"] -= result["cycle"]["stage_budgets_ms"]["takeover"] + 1
            outcome = finish_stage(
                result, path, "task-1", "takeover",
                decision="pass", reason="STAGE_COMPLETED",
            )
        self.assertEqual(outcome["decision"], "block")
        self.assertEqual(outcome["reason"], "STAGE_BUDGET_EXCEEDED")

    def test_legacy_cycle_without_stage_enforcement_remains_finish_compatible(self) -> None:
        result = {}
        initialize_cycle(result)
        readiness = finish_readiness(result)
        with tempfile.TemporaryDirectory() as tmp:
            closure = close_story_cycle(
                result, Path(tmp) / "result.json", "legacy-task", "",
            )
        self.assertEqual(readiness["reason"], "STORY_STAGE_ORDER_NOT_ENFORCED")
        self.assertEqual(closure["decision"], "pass")
        self.assertNotIn("ended_at", result["cycle"])


if __name__ == "__main__":
    unittest.main()
