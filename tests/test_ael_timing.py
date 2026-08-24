#!/usr/bin/env python3
from __future__ import annotations

import json
from copy import deepcopy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ael_timing import (  # noqa: E402
    FINISH_RETRY_LIMIT,
    UNKNOWN,
    append_event,
    budget_status,
    initialize_cycle,
    end_span,
    read_events,
    register_finish_attempt,
    summarize_events,
)
from ael_cycle_commands import (  # noqa: E402
    begin_finish_span, begin_stage, close_story_cycle, finish_readiness, finish_stage,
    start_story_cycle,
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
        self.assertEqual(summary["unproven_tool_wait_ms"], 0)
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
        self.assertEqual(summary["tool_wait_union_ms"], 0)
        self.assertEqual(summary["unproven_tool_wait_ms"], UNKNOWN)

    def test_unknown_parent_preserves_overlapping_known_child_tool_wait_union(self) -> None:
        summary = summarize_events([
            {"event": "start", "span_id": "story", "stage": "story", "epoch_ms": 0},
            {"event": "start", "span_id": "a", "stage": "gate-a", "epoch_ms": 100},
            {"event": "start", "span_id": "b", "stage": "gate-b", "epoch_ms": 300},
            {"event": "end", "span_id": "a", "stage": "gate-a", "epoch_ms": 500,
             "tool_wait_ms": 400, "decision": "pass"},
            {"event": "end", "span_id": "b", "stage": "gate-b", "epoch_ms": 700,
             "tool_wait_ms": 400, "decision": "pass"},
            {"event": "end", "span_id": "story", "stage": "story", "epoch_ms": 1000,
             "tool_wait_ms": UNKNOWN, "decision": "pass"},
        ])
        self.assertEqual(summary["root_wall_ms"], 1000)
        self.assertEqual(summary["tool_wait_union_ms"], 600)
        self.assertEqual(summary["unproven_tool_wait_ms"], UNKNOWN)
        self.assertEqual(summary["agent_active_ms"], UNKNOWN)

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

    def test_append_fails_closed_on_partial_ledger_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            original = '{"schema":"harness-story-wall-clock-v1"'
            path.write_text(original, encoding="utf-8")

            with self.assertRaises(OSError):
                append_event(path, {
                    "task_id": "T1", "stage": "planning", "event": "start",
                    "reason": "SPAN_STARTED",
                })

            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_read_fails_closed_on_any_malformed_ledger_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            path.write_text(
                '{"schema":"harness-story-wall-clock-v1"}\nnot-json\n',
                encoding="utf-8",
            )
            with self.assertRaises(OSError):
                read_events(path)

    def test_read_fails_closed_on_schema_valid_event_with_invalid_timing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            path.write_text(json.dumps({
                "schema": "harness-story-wall-clock-v1",
                "task_id": "task-1", "stage": "story", "event": "start",
                "span_id": "story", "attempt": 1, "epoch_ms": "not-a-number",
            }) + "\n", encoding="utf-8")

            with self.assertRaises(OSError):
                read_events(path)

    def test_conflicting_end_replay_for_same_span_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            append_event(path, {
                "task_id": "task-1", "stage": "planning", "event": "start",
                "span_id": "span-1", "attempt": 1, "epoch_ms": 1,
                "reason": "SPAN_STARTED",
            })
            end_span(
                path, task_id="task-1", stage="planning", span_id="span-1",
                started_epoch_ms=1, ended_epoch_ms=2, decision="pass",
                reason="STAGE_COMPLETED", attempt=1,
            )
            with self.assertRaises(OSError):
                end_span(
                    path, task_id="task-1", stage="planning", span_id="span-1",
                    started_epoch_ms=1, ended_epoch_ms=3, decision="block",
                    reason="REPLAY_CONFLICT", attempt=1,
                )

    def test_orphan_start_span_blocks_stage_retry_accounting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            result = {"work_item": {"id": "WI-1"}}
            start_story_cycle(result, path, "task-1", result["work_item"])
            self.assertEqual(finish_stage(
                result, path, "task-1", "takeover", decision="pass",
                reason="STAGE_COMPLETED",
            )["decision"], "pass")
            append_event(path.parent / "wall-clock-ledger.jsonl", {
                "task_id": "task-1", "work_item_id": "WI-1", "stage": "planning",
                "event": "start", "span_id": "orphan", "attempt": 1,
                "parent_span_id": "story", "epoch_ms": 2, "reason": "SPAN_STARTED",
            })

            outcome = begin_stage(result, path, "task-1", "planning")

        self.assertEqual(outcome["decision"], "block")
        self.assertEqual(outcome["reason"], "STAGE_ORPHAN_SPAN_DETECTED")

    def test_story_start_recovers_an_orphan_takeover_span(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            ledger = path.parent / "wall-clock-ledger.jsonl"
            append_event(ledger, {
                "task_id": "task-1", "work_item_id": "WI-1", "stage": "story",
                "event": "start", "span_id": "story", "parent_span_id": "",
                "attempt": 1, "epoch_ms": 1000,
                "timestamp": "1970-01-01T00:00:01.000Z", "reason": "STORY_CONFIRMED",
            })
            append_event(ledger, {
                "task_id": "task-1", "work_item_id": "WI-1", "stage": "takeover",
                "event": "start", "span_id": "orphan-takeover", "parent_span_id": "story",
                "attempt": 1, "epoch_ms": 1001, "reason": "SPAN_STARTED",
            })
            result = {"work_item": {"id": "WI-1"}}

            start_story_cycle(result, path, "task-1", result["work_item"])
            events = read_events(ledger)

        takeover_starts = [
            event for event in events
            if event["event"] == "start" and event["stage"] == "takeover"
        ]
        self.assertEqual(len(takeover_starts), 1)
        self.assertEqual(
            result["cycle"]["stages"]["takeover"]["attempts"][0]["span_id"],
            "orphan-takeover",
        )

    def test_finish_span_reuses_orphan_with_same_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            result = {}
            cycle = initialize_cycle(result)
            cycle["stages"]["finalize"] = {
                "status": "active", "wall_ms": 0,
                "attempts": [{"attempt": 1, "started_epoch_ms": 1}],
            }
            cycle["current_stage"] = "finalize"
            cycle["finish_attempts"] = [{"attempt": 1, "input_digest": "input"}]
            append_event(path.parent / "wall-clock-ledger.jsonl", {
                "task_id": "task-1", "work_item_id": "WI-1", "stage": "finish",
                "event": "start", "span_id": "orphan-finish", "parent_span_id": "finalize",
                "attempt": 1, "epoch_ms": 10, "input_digest": "input",
                "reason": "SPAN_STARTED",
            })
            attempt = {"attempt": 2, "input_digest": "input"}
            cycle["finish_attempts"].append(dict(attempt))

            span_id, started = begin_finish_span(
                result, path, "task-1", "WI-1", attempt,
            )

        self.assertEqual((span_id, started), ("orphan-finish", 10))
        self.assertEqual(attempt["attempt"], 1)
        self.assertEqual(len(cycle["finish_attempts"]), 1)

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

    def test_clock_rollback_and_naive_timestamp_fail_closed(self) -> None:
        cycle = initialize_cycle({}, started_at="2026-08-22T00:00:00.000Z")
        cycle["last_observed_at"] = "2026-08-22T00:05:00.000Z"
        rollback = budget_status(cycle, at="2026-08-22T00:04:00.000Z")
        self.assertEqual(rollback["reason"], "STORY_CLOCK_ROLLBACK")
        cycle["started_at"] = "2026-08-22T00:00:00"
        invalid = budget_status(cycle, at="2026-08-22T00:04:00.000Z")
        self.assertEqual(invalid["reason"], "STORY_CLOCK_INVALID")

    def test_duplicate_end_event_is_integrity_block_not_exception(self) -> None:
        events = [
            {"event": "start", "span_id": "one", "stage": "qa", "epoch_ms": 1},
            {"event": "end", "span_id": "one", "stage": "qa", "epoch_ms": 2,
             "tool_wait_ms": 1, "decision": "pass"},
            {"event": "end", "span_id": "one", "stage": "qa", "epoch_ms": 3,
             "tool_wait_ms": 2, "decision": "pass"},
        ]
        summary = summarize_events(events)
        self.assertEqual(summary["ledger_integrity"], "block")
        self.assertEqual(summary["duplicate_end_span_ids"], ["one"])

    def test_stage_and_story_end_replay_after_result_write_failure_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            original = {"work_item": {"id": "WI-1"}}
            start_story_cycle(original, path, "task-1", original["work_item"])
            first = deepcopy(original)
            second = deepcopy(original)
            first_outcome = finish_stage(
                first, path, "task-1", "takeover",
                decision="pass", reason="STAGE_COMPLETED",
            )
            second_outcome = finish_stage(
                second, path, "task-1", "takeover",
                decision="pass", reason="STAGE_COMPLETED",
            )
            second["cycle"]["stages"]["takeover"]["wall_ms"] = (
                second["cycle"]["stage_budgets_ms"]["takeover"]
            )
            for result in (first, second):
                for stage in (
                    "planning", "implementation_test", "independent_qa",
                    "deploy_provider", "finalize",
                ):
                    result["cycle"]["stages"][stage] = {"status": "pass"}
                result["cycle"]["current_stage"] = ""
            first_close = close_story_cycle(first, path, "task-1", "WI-1")
            second_close = close_story_cycle(second, path, "task-1", "WI-1")
            summary = summarize_events(read_events(path.parent / "wall-clock-ledger.jsonl"))

        self.assertEqual(first_outcome["decision"], "pass")
        self.assertEqual(second_outcome["decision"], "pass")
        self.assertEqual(second_outcome["reason"], "STAGE_COMPLETED")
        self.assertEqual(first_close["decision"], "pass")
        self.assertEqual(second_close["decision"], "pass")
        self.assertEqual(summary["ledger_integrity"], "pass")
        self.assertEqual(summary["duplicate_end_span_ids"], [])

    def test_stage_end_replay_uses_persisted_outcome_after_budget_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            result = {"work_item": {"id": "WI-1"}}
            start_story_cycle(result, path, "task-1", result["work_item"])
            attempt = result["cycle"]["stages"]["takeover"]["attempts"][-1]
            end_span(
                path.parent / "wall-clock-ledger.jsonl",
                task_id="task-1", work_item_id="WI-1", stage="takeover",
                span_id=attempt["span_id"], started_epoch_ms=attempt["started_epoch_ms"],
                ended_epoch_ms=attempt["started_epoch_ms"] + 1,
                decision="pass", reason="STAGE_COMPLETED", attempt=1,
            )
            after_budget = (
                attempt["started_epoch_ms"]
                + result["cycle"]["stage_budgets_ms"]["takeover"] + 1
            ) / 1000
            with mock.patch("ael_cycle_stages.time.time", return_value=after_budget):
                outcome = finish_stage(
                    result, path, "task-1", "takeover",
                    decision="pass", reason="STAGE_COMPLETED",
                )

        self.assertEqual(outcome["decision"], "pass")
        self.assertEqual(outcome["reason"], "STAGE_COMPLETED")

    def test_story_end_replay_requires_matching_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            result = {"work_item": {"id": "WI-1"}}
            start_story_cycle(result, path, "task-1", result["work_item"])
            for stage in result["cycle"]["stages"].values():
                stage["status"] = "pass"
            for stage in ("planning", "implementation_test", "independent_qa", "deploy_provider", "finalize"):
                result["cycle"]["stages"][stage] = {"status": "pass"}
            result["cycle"]["current_stage"] = ""
            append_event(path.parent / "wall-clock-ledger.jsonl", {
                "task_id": "other-task", "work_item_id": "OTHER", "stage": "story",
                "event": "end", "span_id": "story", "attempt": 1,
                "epoch_ms": 2, "wall_ms": 1, "decision": "pass",
                "reason": "STORY_READY_TO_RELEASE",
            })

            outcome = close_story_cycle(result, path, "task-1", "WI-1")

        self.assertEqual(outcome["decision"], "block")
        self.assertEqual(outcome["reason"], "STORY_LEDGER_REPLAY_CONFLICT")

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

    def test_exact_stage_budget_boundary_is_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            result = {"work_item": {"id": "WI-1"}}
            start_story_cycle(result, path, "task-1", result["work_item"])
            attempt = result["cycle"]["stages"]["takeover"]["attempts"][-1]
            attempt["started_epoch_ms"] -= result["cycle"]["stage_budgets_ms"]["takeover"]
            outcome = finish_stage(
                result, path, "task-1", "takeover",
                decision="pass", reason="STAGE_COMPLETED",
            )
            end = read_events(path.parent / "wall-clock-ledger.jsonl")[-1]
        self.assertEqual(outcome["reason"], "STAGE_BUDGET_EXCEEDED")
        self.assertEqual(end["decision"], "block")

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
