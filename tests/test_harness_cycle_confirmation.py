#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import harness_cycle_confirmation  # noqa: E402
import harness_commands  # noqa: E402
from harness_cycle_commands import begin_stage, finish_stage, start_story_cycle  # noqa: E402
from harness_runtime import atomic_write_result, canonical_digest, default_result, load_result, result_path  # noqa: E402
from harness_timing import ledger_path, parse_time, read_events, utc_now  # noqa: E402


class HarnessCycleConfirmationTest(unittest.TestCase):
    def implicit_story(self, product: Path, task_id: str = "direct-start") -> Path:
        work_item = {"id": task_id, "provider": "noop"}
        result = default_result(task_id, initial_tier="lite", work_item=work_item)
        task = {
            "task_id": task_id, "scope": ["src"], "tier_floor": "lite",
            "work_item": task_id, "kind": "implementation", "change_reason": None,
            "primary_role": "lead-agent", "confirmation": "implicit-direct-start",
        }
        result["task"] = task
        result["binding_digest"] = canonical_digest(task)
        result["lifecycle"] = {"provider": "noop", "decision": "pass"}
        path = result_path(product, task_id)
        start_story_cycle(result, path, task_id, work_item)
        atomic_write_result(path, result)
        return path

    def args(
        self, product: Path, task_id: str = "direct-start", *, work_item_id: str | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            product_root=str(product), harness_root=str(ROOT), task_id=task_id,
            work_item=work_item_id or task_id, provider="noop", tier="lite", scope=["src"],
            kind="implementation",
        )

    def test_confirm_promotes_implicit_takeover_without_resetting_story_clock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = self.implicit_story(product)
            before = load_result(path)
            started_at = before["cycle"]["started_at"]
            deadline_at = before["cycle"]["deadline_at"]
            story_starts = [
                event for event in read_events(ledger_path(path))
                if event.get("span_id") == "story" and event.get("event") == "start"
            ]
            emitted = []
            with mock.patch.object(harness_cycle_confirmation, "dump_json", side_effect=emitted.append):
                harness_cycle_confirmation.cmd_confirm(self.args(product))
            updated = load_result(path)

        self.assertEqual(emitted[-1]["reason"], "STORY_CONFIRMATION_UPGRADED")
        self.assertEqual(updated["cycle"]["started_at"], started_at)
        self.assertEqual(updated["cycle"]["deadline_at"], deadline_at)
        self.assertTrue(updated["cycle"]["release_readback_enforced"])
        self.assertEqual(updated["cycle"]["current_stage"], "planning")
        self.assertEqual(updated["cycle"]["stages"]["takeover"]["status"], "pass")
        self.assertEqual(updated["cycle"]["stages"]["planning"]["status"], "active")
        self.assertEqual(updated["task"]["confirmation"], "explicit")
        self.assertEqual(updated["binding_digest"], canonical_digest(updated["task"]))
        self.assertEqual(len(story_starts), 1)

    def test_explicit_confirmation_clock_precedes_lifecycle_and_overrides_default(self) -> None:
        confirmed_at = utc_now()
        implicit_start = (
            parse_time(confirmed_at) + timedelta(seconds=5)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        expected_deadline = (
            parse_time(confirmed_at) + timedelta(minutes=30)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        order = []

        def confirmation_time() -> str:
            order.append("confirmation")
            return confirmed_at

        def lifecycle(_product: Path, _provider: str) -> dict:
            order.append("lifecycle")
            return {"provider": "noop", "decision": "pass", "reason": "LIFECYCLE_CAPABILITY_OK"}

        def baseline(_product: Path, path: Path, *, work_item_id: str) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            with (
                mock.patch.object(harness_cycle_confirmation, "now", side_effect=confirmation_time),
                mock.patch.object(harness_cycle_confirmation, "discover_lifecycle", side_effect=lifecycle),
                mock.patch.object(harness_cycle_confirmation, "policy_for", return_value="policy"),
                mock.patch.object(harness_cycle_confirmation, "capture_baseline", side_effect=baseline),
                mock.patch.object(harness_cycle_confirmation, "capture_usage_baseline"),
                mock.patch.object(harness_cycle_confirmation, "automatic_receipt", return_value={}),
                mock.patch.object(harness_cycle_confirmation, "write_phase_handoff", return_value={}),
                mock.patch.object(harness_cycle_confirmation, "dump_json"),
                mock.patch("harness_timing.utc_now", return_value=implicit_start),
            ):
                harness_cycle_confirmation.cmd_confirm(self.args(
                    product, "explicit-start", work_item_id="work-item-17",
                ))
            result = load_result(result_path(product, "explicit-start"))
            active_task = json.loads(
                (product / "harness-workspace/runs/active_task.json").read_text(
                    encoding="utf-8",
                ),
            )

        self.assertEqual(order[:2], ["confirmation", "lifecycle"])
        self.assertEqual(result["cycle"]["confirmed_at"], confirmed_at)
        self.assertEqual(result["cycle"]["started_at"], confirmed_at)
        self.assertEqual(result["cycle"]["deadline_at"], expected_deadline)
        self.assertNotEqual(result["cycle"]["started_at"], implicit_start)
        self.assertEqual(active_task["task_id"], "explicit-start")
        self.assertEqual(active_task["work_item_id"], "work-item-17")

    def test_confirm_blocks_after_implicit_story_advances_past_takeover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = self.implicit_story(product)
            result = load_result(path)
            self.assertEqual(finish_stage(
                result, path, "direct-start", "takeover",
                decision="pass", reason="STAGE_COMPLETED",
            )["decision"], "pass")
            self.assertEqual(begin_stage(
                result, path, "direct-start", "planning",
            )["decision"], "pass")
            atomic_write_result(path, result)
            before = path.read_bytes()
            emitted = []
            with mock.patch.object(harness_cycle_confirmation, "dump_json", side_effect=emitted.append):
                harness_cycle_confirmation.cmd_confirm(self.args(product))
            after = path.read_bytes()

        self.assertEqual(emitted[-1]["decision"], "block")
        self.assertEqual(emitted[-1]["reason"], "STORY_CONFIRMATION_TOO_LATE")
        self.assertEqual(before, after)

    def test_omitted_provider_uses_committed_binding_and_baseline_work_item(self) -> None:
        captured = []

        def baseline(_product: Path, path: Path, *, work_item_id: str) -> None:
            captured.append(work_item_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            args = self.args(product, "task-provider", work_item_id="WI-17")
            args.provider = ""
            with (
                mock.patch.object(
                    harness_cycle_confirmation, "committed_work_item_resolution",
                    return_value={
                        "status": "unique", "work_item": {"id": "WI-17", "provider": "teambition"},
                        "candidates": [],
                    },
                    create=True,
                ),
                mock.patch.object(
                    harness_cycle_confirmation, "discover_lifecycle",
                    return_value={"provider": "teambition", "decision": "pass", "reason": "OK"},
                ) as lifecycle,
                mock.patch.object(harness_cycle_confirmation, "policy_for", return_value="policy"),
                mock.patch.object(harness_cycle_confirmation, "capture_baseline", side_effect=baseline),
                mock.patch.object(harness_cycle_confirmation, "capture_usage_baseline"),
                mock.patch.object(harness_cycle_confirmation, "automatic_receipt", return_value={}),
                mock.patch.object(harness_cycle_confirmation, "write_phase_handoff", return_value={}),
                mock.patch.object(harness_cycle_confirmation, "dump_json"),
            ):
                harness_cycle_confirmation.cmd_confirm(args)

            result = load_result(result_path(product, "task-provider"))

        lifecycle.assert_called_once_with(product.resolve(), "teambition")
        self.assertEqual(result["work_item"]["provider"], "teambition")
        self.assertEqual(captured, ["WI-17"])

    def test_omitted_provider_discovers_project_provider_without_committed_binding(self) -> None:
        def baseline(_product: Path, path: Path, *, work_item_id: str) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            args = self.args(product, "task-provider")
            args.provider = ""
            args.tier = "strict"
            with (
                mock.patch.object(
                    harness_cycle_confirmation, "committed_work_item_resolution",
                    return_value={"status": "missing", "work_item": None, "candidates": []},
                ),
                mock.patch.object(
                    harness_cycle_confirmation, "discover_lifecycle",
                    return_value={"provider": "teambition", "decision": "pass", "reason": "OK"},
                ) as lifecycle,
                mock.patch.object(harness_cycle_confirmation, "policy_for", return_value="policy"),
                mock.patch.object(harness_cycle_confirmation, "capture_baseline", side_effect=baseline),
                mock.patch.object(harness_cycle_confirmation, "capture_usage_baseline"),
                mock.patch.object(harness_cycle_confirmation, "automatic_receipt", return_value={}),
                mock.patch.object(harness_cycle_confirmation, "write_phase_handoff", return_value={}),
                mock.patch.object(harness_cycle_confirmation, "dump_json"),
            ):
                harness_cycle_confirmation.cmd_confirm(args)

            result = load_result(result_path(product, "task-provider"))

        lifecycle.assert_called_once_with(product.resolve(), "")
        self.assertEqual(result["work_item"]["provider"], "teambition")

    def test_conflicting_active_task_blocks_second_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            active = product / "harness-workspace/runs/active_task.json"
            active.parent.mkdir(parents=True)
            active.write_text('{"task_id":"other-task","work_item_id":"OTHER"}\n', encoding="utf-8")
            emitted = []
            with mock.patch.object(
                harness_cycle_confirmation, "dump_json", side_effect=emitted.append,
            ):
                harness_cycle_confirmation.cmd_confirm(self.args(product, "second-task"))

        self.assertEqual(emitted[-1]["reason"], "ACTIVE_TASK_BINDING_CONFLICT")
        self.assertFalse(result_path(product, "second-task").exists())

    def test_start_serializes_shared_active_task_before_task_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp).resolve()
            args = SimpleNamespace(
                product_root=str(product), harness_root=str(ROOT), task_id="task-1",
                work_item="", kind="implementation", tier="lite", scope=["src"],
                reason="",
            )
            locked = []

            class Lock:
                def __init__(self, path):
                    self.path = path

                def __enter__(self):
                    locked.append(self.path)

                def __exit__(self, *_args):
                    return False

            with (
                mock.patch.object(
                    harness_commands, "committed_work_item_resolution",
                    return_value={"status": "missing"},
                ),
                mock.patch.object(
                    harness_commands, "resolve_start_work_item",
                    return_value={"decision": "pass", "work_item": None},
                ),
                mock.patch.object(harness_commands, "task_operation_lock", side_effect=Lock),
                mock.patch.object(harness_commands, "_cmd_start_locked", return_value=0),
            ):
                harness_commands.cmd_start(args)

        self.assertEqual(
            locked,
            [
                product / "harness-workspace/runs/active_task.json",
                result_path(product, "task-1"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
