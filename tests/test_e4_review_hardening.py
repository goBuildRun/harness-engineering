#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ael_cycle_release  # noqa: E402
import ael_gc_runner  # noqa: E402
from ael_cycle_stages import STAGE_ORDER, close_story_cycle, start_story_cycle  # noqa: E402
from ael_gc_runner import invoke_gc_once, read_gc_result  # noqa: E402
from ael_release_readback import validate as validate_readback  # noqa: E402
from ael_runtime import canonical_digest, result_path  # noqa: E402


class LifecycleReadbackHardeningTest(unittest.TestCase):
    @staticmethod
    def closed_result(path: Path) -> dict:
        result = {
            "task_id": "task-1", "decision": "pass", "state": "ready_to_release",
            "blockers": [], "policy_digest": "policy",
            "task": {"confirmation": "explicit"},
            "candidate": {"digest": "candidate"},
            "work_item": {"id": "wi-1", "provider": "noop"},
            "lifecycle": {"provider": "noop", "decision": "pass"},
        }
        start_story_cycle(result, path, "task-1", result["work_item"])
        for stage in STAGE_ORDER:
            result["cycle"]["stages"][stage] = {"status": "pass"}
        result["cycle"]["current_stage"] = ""
        result["cycle"]["canonical_finish"] = {
            "decision": "pass", "input_digest": "finish",
        }
        closure = close_story_cycle(result, path, "task-1", "wi-1")
        if closure["decision"] != "pass":
            raise AssertionError(closure)
        result["lifecycle_readback"] = {
            "provider": "noop", "candidate_digest": "candidate", "commit": "a" * 40,
        }
        return result

    def test_external_caller_authored_receipt_is_rejected(self) -> None:
        receipt = {
            "schema": "harness-lifecycle-readback-v1",
            "decision": "pass",
            "reason": "EXTERNAL_READY_TO_RELEASE_READBACK",
            "task_id": "task-1",
            "work_item_id": "wi-1",
            "provider": "feishu",
            "status": "ready_to_release",
            "readback": "pass",
            "candidate_digest": "candidate",
            "commit": "a" * 40,
            "completed_at": "2026-08-22T00:00:00.000Z",
        }
        receipt["receipt_digest"] = canonical_digest(receipt)

        outcome = validate_readback(
            receipt, task_id="task-1", work_item_id="wi-1", provider="feishu",
            candidate_digest="candidate", commit="a" * 40,
        )

        self.assertEqual(outcome["reason"], "LIFECYCLE_TRUSTED_READBACK_REQUIRED")

    def test_repeated_successful_release_ready_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            result = self.closed_result(path)
            with (
                mock.patch.object(ael_cycle_release, "load_result", return_value=result),
                mock.patch.object(ael_cycle_release, "dump_json") as emitted,
                mock.patch.object(
                    ael_cycle_release, "committed_candidate",
                    return_value={"decision": "pass", "commit": "a" * 40},
                ),
                mock.patch.object(
                    ael_cycle_release, "validate_readback",
                    return_value={"decision": "pass", "reason": "LIFECYCLE_READBACK_VALID"},
                ),
                mock.patch.object(
                    ael_cycle_release, "verify_attestation",
                    return_value={
                        "decision": "pass", "reason": "ATTESTATION_VALID",
                        "result": {
                            **result,
                            "state": "validated",
                            "cycle": {
                                **result["cycle"],
                                "ended_at": None,
                            },
                        },
                    },
                ) as verify,
                mock.patch.object(
                    ael_cycle_release, "validate_current_release_evidence",
                    return_value={"decision": "pass", "reason": "RELEASE_EVIDENCE_CURRENT"},
                    create=True,
                ),
                mock.patch.object(ael_cycle_release, "finish_stage") as finish,
                mock.patch.object(ael_cycle_release, "atomic_write_result") as write,
                mock.patch.object(
                    ael_cycle_release, "load_candidate_snapshot", return_value={"snapshot": True},
                ),
            ):
                ael_cycle_release.cmd_release_ready(
                    SimpleNamespace(
                        product_root=str(product), task_id="task-1", commit="HEAD", receipt="",
                    ),
                    refresh_assurance=mock.Mock(),
                )

        self.assertEqual(emitted.call_args.args[0]["reason"], "STORY_ALREADY_READY_TO_RELEASE")
        self.assertEqual(result["state"], "ready_to_release")
        finish.assert_not_called()
        write.assert_not_called()
        verify.assert_called_once()

    def test_lifecycle_provider_is_part_of_attested_release_binding(self) -> None:
        attested = {
            "decision": "pass", "state": "validated", "task_id": "task-1",
            "work_item": {"id": "wi-1", "provider": "feishu"},
            "lifecycle": {"provider": "feishu", "decision": "pass"},
        }
        live = {
            **attested,
            "lifecycle": {"provider": "noop", "decision": "pass"},
        }

        outcome = ael_cycle_release._attestation_result_status(
            live, {"result": attested},
        )

        self.assertEqual(outcome["reason"], "ATTESTATION_RESULT_BINDING_MISMATCH")

    def test_failed_ready_replay_revokes_persisted_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            result = self.closed_result(path)
            with (
                mock.patch.object(ael_cycle_release, "load_result", return_value=result),
                mock.patch.object(
                    ael_cycle_release, "_guarded_release_integrity",
                    return_value={"decision": "block", "reason": "RELEASE_EVIDENCE_MANIFEST_STALE"},
                ),
                mock.patch.object(ael_cycle_release, "atomic_write_result") as write,
                mock.patch.object(ael_cycle_release, "dump_json"),
            ):
                ael_cycle_release.cmd_release_ready(
                    SimpleNamespace(
                        product_root=str(product), task_id="task-1", commit="HEAD", receipt="",
                    ),
                    refresh_assurance=mock.Mock(),
                )

        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["state"], "blocked")
        self.assertIn("RELEASE_EVIDENCE_MANIFEST_STALE", result["blockers"])
        write.assert_called_once_with(path.resolve(), result)

    def test_ready_replay_requires_a_verified_closed_story_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            result = {
                "task_id": "task-1", "decision": "pass", "state": "ready_to_release",
                "blockers": [], "task": {"confirmation": "explicit"},
                "candidate": {"digest": "candidate"},
                "work_item": {"id": "wi-1", "provider": "noop"},
            }
            start_story_cycle(result, path, "task-1", result["work_item"])
            for stage in STAGE_ORDER[:-1]:
                result["cycle"]["stages"][stage] = {"status": "pass"}
            result["cycle"]["stages"]["finalize"] = {"status": "active"}
            result["cycle"]["current_stage"] = "finalize"
            result["cycle"]["ended_at"] = "2026-08-22T00:10:00.000Z"
            with (
                mock.patch.object(ael_cycle_release, "load_result", return_value=result),
                mock.patch.object(
                    ael_cycle_release, "_guarded_release_integrity",
                    return_value={"decision": "pass", "commit": "a" * 40},
                ),
                mock.patch.object(
                    ael_cycle_release, "validate_readback",
                    return_value={"decision": "pass", "reason": "LIFECYCLE_READBACK_VALID"},
                ),
                mock.patch.object(ael_cycle_release, "atomic_write_result") as write,
                mock.patch.object(ael_cycle_release, "dump_json") as emitted,
                mock.patch.object(ael_cycle_release, "finish_stage") as finish,
            ):
                ael_cycle_release.cmd_release_ready(
                    SimpleNamespace(
                        product_root=str(product), task_id="task-1", commit="HEAD", receipt="",
                    ),
                    refresh_assurance=mock.Mock(),
                )

        self.assertEqual(emitted.call_args.args[0]["decision"], "block")
        self.assertNotEqual(emitted.call_args.args[0]["reason"], "STORY_ALREADY_READY_TO_RELEASE")
        self.assertEqual(result["state"], "blocked")
        finish.assert_not_called()
        write.assert_called_once()

    def test_failed_closed_replay_can_recover_without_refinalizing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            result = self.closed_result(path)
            with (
                mock.patch.object(ael_cycle_release, "load_result", return_value=result),
                mock.patch.object(
                    ael_cycle_release, "_guarded_release_integrity",
                    side_effect=[
                        {"decision": "block", "reason": "RELEASE_EVIDENCE_MANIFEST_STALE"},
                        {"decision": "pass", "commit": "a" * 40},
                    ],
                ),
                mock.patch.object(
                    ael_cycle_release, "validate_readback",
                    return_value={"decision": "pass", "reason": "LIFECYCLE_READBACK_VALID"},
                ),
                mock.patch.object(ael_cycle_release, "atomic_write_result"),
                mock.patch.object(ael_cycle_release, "dump_json") as emitted,
                mock.patch.object(ael_cycle_release, "finish_stage") as finish,
            ):
                args = SimpleNamespace(
                    product_root=str(product), task_id="task-1", commit="HEAD", receipt="",
                )
                ael_cycle_release.cmd_release_ready(args, refresh_assurance=mock.Mock())
                ael_cycle_release.cmd_release_ready(args, refresh_assurance=mock.Mock())

        self.assertEqual(emitted.call_args.args[0]["reason"], "STORY_ALREADY_READY_TO_RELEASE")
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(result["state"], "ready_to_release")
        self.assertEqual(result["blockers"], [])
        finish.assert_not_called()

    def test_transient_ledger_failure_on_closed_replay_can_recover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            result = self.closed_result(path)
            with (
                mock.patch.object(ael_cycle_release, "load_result", return_value=result),
                mock.patch.object(
                    ael_cycle_release, "_closed_cycle_status",
                    side_effect=[
                        {"decision": "block", "reason": "STORY_LEDGER_INVALID"},
                        {"decision": "pass", "reason": "STORY_CYCLE_CLOSED"},
                    ],
                ),
                mock.patch.object(
                    ael_cycle_release, "_guarded_release_integrity",
                    return_value={"decision": "pass", "commit": "a" * 40},
                ),
                mock.patch.object(
                    ael_cycle_release, "validate_readback",
                    return_value={"decision": "pass", "reason": "LIFECYCLE_READBACK_VALID"},
                ),
                mock.patch.object(ael_cycle_release, "atomic_write_result"),
                mock.patch.object(ael_cycle_release, "dump_json") as emitted,
            ):
                args = SimpleNamespace(
                    product_root=str(product), task_id="task-1", commit="HEAD", receipt="",
                )
                ael_cycle_release.cmd_release_ready(args, refresh_assurance=mock.Mock())
                ael_cycle_release.cmd_release_ready(args, refresh_assurance=mock.Mock())

        self.assertEqual(emitted.call_args.args[0]["reason"], "STORY_ALREADY_READY_TO_RELEASE")
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(result["state"], "ready_to_release")

    def test_release_ledger_failure_is_persisted_as_structured_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            result = {
                "task_id": "task-1", "decision": "pass", "state": "validated",
                "blockers": [], "policy_digest": "policy",
                "work_item": {"id": "wi-1", "provider": "noop"},
                "lifecycle": {"provider": "noop", "decision": "pass"},
                "candidate": {"digest": "candidate"},
                "cycle": {"canonical_finish": {"decision": "pass", "input_digest": "finish"}},
            }
            with (
                mock.patch.object(ael_cycle_release, "load_result", return_value=result),
                mock.patch.object(
                    ael_cycle_release, "budget_status", return_value={"decision": "pass"},
                ),
                mock.patch.object(
                    ael_cycle_release, "stage_budget_status", return_value={"decision": "pass"},
                ),
                mock.patch.object(
                    ael_cycle_release, "_guarded_release_integrity",
                    return_value={"decision": "pass", "commit": "a" * 40},
                ),
                mock.patch.object(
                    ael_cycle_release, "validate_readback", return_value={"decision": "pass"},
                ),
                mock.patch.object(
                    ael_cycle_release, "finish_stage", side_effect=OSError("invalid ledger"),
                ),
                mock.patch.object(ael_cycle_release, "atomic_write_result") as write,
                mock.patch.object(ael_cycle_release, "dump_json") as emitted,
            ):
                ael_cycle_release.cmd_release_ready(
                    SimpleNamespace(
                        product_root=str(product), task_id="task-1", commit="HEAD", receipt="",
                    ),
                    refresh_assurance=mock.Mock(),
                )

        self.assertEqual(emitted.call_args.args[0]["reason"], "STORY_LEDGER_INVALID")
        self.assertEqual(result["state"], "blocked")
        write.assert_called_once()


class GCRunnerHardeningTest(unittest.TestCase):
    @staticmethod
    def base_result() -> dict:
        return {
            "task_id": "task-1",
            "policy_digest": "policy",
            "blockers": [],
            "cost": {"harness": {"agent_calls": 0, "context_chars": 0, "gate_duration_ms": 0}},
        }

    def test_gc_uses_process_group_runner_for_descendant_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.base_result()
            mechanical = {
                "subject_digest": "subject", "triggers": [], "finding_details": [],
            }
            with (
                mock.patch.dict(os.environ, {"AEL_GC_AGENT_ARGV": '["gc-agent"]'}),
                mock.patch.object(
                    ael_gc_runner, "build_gc_context", return_value=({}, 2),
                ),
                mock.patch.object(
                    ael_gc_runner, "run_process_group",
                    return_value=SimpleNamespace(returncode=1), create=True,
                ) as runner,
            ):
                invoke_gc_once(
                    result, root, mechanical, ["feature.py"], root, timeout_ms=10,
                )

        runner.assert_called_once()

    def test_non_object_gc_json_is_structured_invalid_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gc_result.json").write_text("[{}]\n", encoding="utf-8")
            outcome = read_gc_result(root)

        self.assertEqual(outcome, {"decision": "block", "reason": "GC_RESULT_INVALID"})

    def test_gc_process_start_failure_is_attributed_without_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.base_result()
            mechanical = {
                "subject_digest": "subject", "triggers": [], "finding_details": [],
            }
            with (
                mock.patch.dict(os.environ, {"AEL_GC_AGENT_ARGV": '["missing-gc-agent"]'}),
                mock.patch.object(ael_gc_runner, "build_gc_context", return_value=({}, 2)),
                mock.patch.object(ael_gc_runner, "run_process_group", side_effect=OSError("missing")),
            ):
                outcome = invoke_gc_once(
                    result, root, mechanical, ["feature.py"], root, timeout_ms=10,
                )

        self.assertIsNone(outcome)
        self.assertIn("GC_RUNNER_INVALID", result["blockers"])


if __name__ == "__main__":
    unittest.main()
