#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"
ADAPTER = FIXTURES / "provider_mock_adapter.py"
sys.path.insert(0, str(SCRIPTS))

import provider_attempt  # noqa: E402
import harness_provider_postcondition  # noqa: E402

from provider_attempt import (  # noqa: E402
    EVIDENCE_SCHEMA,
    _provider_readiness,
    _remaining_provider_seconds,
    run_once,
    validate_receipt,
)
from harness_provider_preflight import execute_preflight  # noqa: E402


class ProviderAttemptTest(unittest.TestCase):
    def command(self, adapter: Path = ADAPTER):
        return [
            sys.executable, str(adapter),
            "--subject", "subject", "--provider", "mock",
        ]

    def preflight(self):
        contract = json.loads((FIXTURES / "provider-offline-contract.json").read_text())
        return execute_preflight(contract, "subject", "mock", ADAPTER, self.command())

    def evidence(self, **overrides):
        return {
            "schema": EVIDENCE_SCHEMA,
            "decision": "pass",
            "subject_digest": "subject",
            "provider": "mock",
            **overrides,
        }

    def attempt(self, state, evidence, runner):
        return run_once(
            state, self.preflight(), "subject", "mock", evidence,
            evidence.name, self.command(), 1, runner, adapter_path=ADAPTER,
        )

    def test_one_shot_attempt_binds_preflight_and_semantic_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state, evidence = root / "attempt.json", root / "evidence.json"
            preflight = self.preflight()

            def runner(_command, **_kwargs):
                self.assertNotIn("HARNESS_PROVIDER_OFFLINE_TRACE", _kwargs["env"])
                self.assertEqual(_kwargs["env"]["HARNESS_PROVIDER_EXECUTION_MODE"], "real")
                evidence.write_text(json.dumps(self.evidence()))
                return SimpleNamespace(returncode=0)

            first = run_once(
                state, preflight, "subject", "mock", evidence,
                "evidence.json", self.command(), 1, runner, adapter_path=ADAPTER,
            )
            second = run_once(
                state, preflight, "subject", "mock", evidence,
                "evidence.json", self.command(), 1, runner, adapter_path=ADAPTER,
            )
            self.assertEqual(first["decision"], "pass")
            self.assertEqual(first["contract_digest"], preflight["contract_digest"])
            self.assertEqual(
                validate_receipt(
                    first, preflight, "subject", "mock", evidence,
                )["decision"],
                "pass",
            )
            self.assertEqual(second["reason"], "PROVIDER_ATTEMPT_LIMIT_EXCEEDED")

    def test_failure_consumes_attempt_and_provider_mismatch_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = self.preflight()
            failed = run_once(
                root / "attempt.json", preflight, "subject", "mock", root / "missing.json",
                "missing.json", self.command(), 1,
                lambda _command, **_kwargs: SimpleNamespace(returncode=1),
                adapter_path=ADAPTER,
            )
            failed["decision"] = "pass"
        self.assertEqual(
            validate_receipt(failed, preflight, "subject", "mock")["decision"], "block",
        )
        self.assertEqual(
            run_once(
                Path(tmp) / "other.json", preflight, "subject", "other", Path(tmp) / "x",
                "x", self.command(), 1, adapter_path=ADAPTER,
            )["decision"],
            "block",
        )

    def test_arbitrary_bytes_and_wrong_evidence_identity_fail_closed(self) -> None:
        for name, payload, reason in (
            ("bytes", '{"decision":"pass"}', "PROVIDER_EVIDENCE_SCHEMA_INVALID"),
            ("subject", json.dumps(self.evidence(subject_digest="other")), "PROVIDER_EVIDENCE_SUBJECT_MISMATCH"),
            ("provider", json.dumps(self.evidence(provider="other")), "PROVIDER_EVIDENCE_PROVIDER_MISMATCH"),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                evidence = root / "evidence.json"

                def runner(_command, **_kwargs):
                    evidence.write_text(payload)
                    return SimpleNamespace(returncode=0)

                receipt = self.attempt(root / "attempt.json", evidence, runner)
                self.assertEqual(receipt["decision"], "block")
                self.assertEqual(receipt["reason"], reason)

    def test_adapter_change_and_process_timeout_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = root / "mock_adapter.py"
            adapter.write_bytes(ADAPTER.read_bytes())
            command = self.command(adapter)
            contract = json.loads((FIXTURES / "provider-offline-contract.json").read_text())
            preflight = execute_preflight(contract, "subject", "mock", adapter, command)
            evidence = root / "evidence.json"

            def changing_runner(_command, **_kwargs):
                evidence.write_text(json.dumps(self.evidence()))
                adapter.write_text(adapter.read_text() + "\n# changed\n")
                return SimpleNamespace(returncode=0)

            changed = run_once(
                root / "changed.json", preflight, "subject", "mock", evidence,
                "evidence.json", command, 1, changing_runner, adapter_path=adapter,
            )
            timed_out = run_once(
                root / "timeout.json", self.preflight(), "subject", "mock", root / "missing.json",
                "missing.json", self.command(), 1,
                lambda _command, **_kwargs: (_ for _ in ()).throw(
                    subprocess.TimeoutExpired(_command, _kwargs["timeout"])
                ),
                adapter_path=ADAPTER,
            )
        self.assertEqual(changed["reason"], "PROVIDER_ADAPTER_CHANGED")
        self.assertEqual(timed_out["reason"], "PROVIDER_DEADLINE_EXCEEDED")

    def test_imported_helper_drift_blocks_before_real_provider_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "helper.py"
            adapter = root / "adapter.py"
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            adapter.write_text(
                "import helper\n"
                "from pathlib import Path\n"
                "exec(Path(%r).read_text())\n" % str(ADAPTER),
                encoding="utf-8",
            )
            command = self.command(adapter)
            contract = json.loads((FIXTURES / "provider-offline-contract.json").read_text())
            preflight = execute_preflight(contract, "subject", "mock", adapter, command)
            helper.write_text("VALUE = 2\n", encoding="utf-8")
            runner = mock.Mock()

            outcome = run_once(
                root / "attempt.json", preflight, "subject", "mock",
                root / "evidence.json", "evidence.json", command, 1,
                runner, adapter_path=adapter,
            )

        self.assertEqual(outcome["reason"], "PROVIDER_EXECUTION_DEPENDENCIES_CHANGED")
        runner.assert_not_called()

    def test_post_attempt_candidate_or_stage_drift_is_persisted_as_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "evidence.json"

            def runner(_command, **_kwargs):
                evidence.write_text(json.dumps(self.evidence()))
                return SimpleNamespace(returncode=0)

            receipt = run_once(
                root / "attempt.json", self.preflight(), "subject", "mock", evidence,
                "evidence.json", self.command(), 1, runner, adapter_path=ADAPTER,
                postcondition=lambda: {
                    "decision": "block", "reason": "PROVIDER_CANDIDATE_CHANGED_DURING_ATTEMPT",
                },
            )
            persisted = json.loads((root / "attempt.json").read_text(encoding="utf-8"))

        self.assertEqual(receipt["decision"], "block")
        self.assertEqual(receipt["reason"], "PROVIDER_CANDIDATE_CHANGED_DURING_ATTEMPT")
        self.assertEqual(persisted["decision"], "block")

    def test_late_precondition_block_prevents_provider_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = mock.Mock()
            receipt = run_once(
                root / "attempt.json", self.preflight(), "subject", "mock",
                root / "evidence.json", "evidence.json", self.command(), 1,
                runner, adapter_path=ADAPTER,
                precondition=lambda: {"decision": "block", "reason": "QA_EVIDENCE_STALE"},
            )

        self.assertEqual(receipt["reason"], "QA_EVIDENCE_STALE")
        runner.assert_not_called()

    def test_postcondition_rejects_authorization_result_mutation(self) -> None:
        result = {
            "task_id": "task-1", "work_item": {"id": "WI-1", "provider": "mock"},
            "task": {"task_id": "task-1"}, "policy_digest": "policy",
            "lifecycle": {"provider": "noop"}, "candidate": {"digest": "subject"},
            "cycle": {
                "current_stage": "deploy_provider",
                "stages": {"deploy_provider": {"status": "active", "attempts": [{}]}},
            },
        }
        with (
            mock.patch.object(harness_provider_postcondition, "load_result", return_value=result),
            mock.patch.object(
                harness_provider_postcondition, "load_candidate_snapshot",
                return_value={"snapshot": True},
            ),
            mock.patch.object(
                harness_provider_postcondition, "changed_since_baseline", return_value=[],
            ),
            mock.patch.object(
                harness_provider_postcondition, "bound_candidate",
                return_value={"decision": "pass", "candidate_digest": "subject"},
            ),
            mock.patch.object(
                harness_provider_postcondition, "budget_status", return_value={"decision": "pass"},
            ),
            mock.patch.object(
                harness_provider_postcondition, "stage_budget_status",
                return_value={"decision": "pass"},
            ),
        ):
            outcome = harness_provider_postcondition.validate(
                Path("/product"), Path("/result"), Path("/baseline"), "subject",
                expected_result_digest="different",
            )

        self.assertEqual(outcome["reason"], "PROVIDER_RESULT_CHANGED_DURING_ATTEMPT")

    def test_postcondition_rejects_cycle_budget_mutation(self) -> None:
        original = {
            "task_id": "task-1", "work_item": {"id": "WI-1", "provider": "mock"},
            "task": {"task_id": "task-1"}, "policy_digest": "policy",
            "lifecycle": {"provider": "mock"}, "candidate": {"digest": "subject"},
            "cycle": {
                "started_at": "2026-08-22T00:00:00.000Z",
                "last_observed_at": "2026-08-22T00:01:00.000Z",
                "total_budget_ms": 1_800_000,
                "stage_budgets_ms": {"deploy_provider": 300_000},
                "current_stage": "deploy_provider",
                "stages": {"deploy_provider": {"status": "active", "attempts": [{}]}},
            },
        }
        mutated = deepcopy(original)
        mutated["cycle"].update({
            "started_at": "2026-08-22T00:01:00.000Z",
            "last_observed_at": "2026-08-22T00:01:00.000Z",
            "total_budget_ms": 3_600_000,
            "stage_budgets_ms": {"deploy_provider": 3_600_000},
        })
        expected = harness_provider_postcondition.authorization_digest(original)
        with (
            mock.patch.object(
                harness_provider_postcondition, "load_result", return_value=mutated,
            ),
            mock.patch.object(
                harness_provider_postcondition, "load_candidate_snapshot", return_value={},
            ),
            mock.patch.object(
                harness_provider_postcondition, "changed_since_baseline", return_value=[],
            ),
            mock.patch.object(
                harness_provider_postcondition, "bound_candidate",
                return_value={"decision": "pass", "candidate_digest": "subject"},
            ),
            mock.patch.object(
                harness_provider_postcondition, "budget_status", return_value={"decision": "pass"},
            ),
            mock.patch.object(
                harness_provider_postcondition, "stage_budget_status",
                return_value={"decision": "pass"},
            ),
        ):
            outcome = harness_provider_postcondition.validate(
                Path("/product"), Path("/result"), Path("/baseline"), "subject",
                expected_result_digest=expected,
            )

        self.assertEqual(outcome["reason"], "PROVIDER_RESULT_CHANGED_DURING_ATTEMPT")

    def test_environment_drift_from_preflight_blocks_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "evidence.json"
            with mock.patch.dict(os.environ, {"PYTHONPATH": "one"}, clear=False):
                preflight = self.preflight()
            runner = mock.Mock()
            with mock.patch.dict(os.environ, {"PYTHONPATH": "two"}, clear=False):
                outcome = run_once(
                    root / "attempt.json", preflight, "subject", "mock", evidence,
                    "evidence.json", self.command(), 1, runner, adapter_path=ADAPTER,
                )

        self.assertEqual(outcome["reason"], "PROVIDER_EXECUTION_ENVIRONMENT_CHANGED")
        runner.assert_not_called()

    def test_dependency_hashing_consumes_the_provider_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = self.preflight()
            clock = [0.0]

            def dependency_digest(*_args, **_kwargs):
                clock[0] += 2.0
                return preflight["execution_dependency_digest"]

            runner = mock.Mock()
            with (
                mock.patch.object(provider_attempt.time, "monotonic", side_effect=lambda: clock[0]),
                mock.patch.object(
                    provider_attempt, "execution_dependency_digest",
                    side_effect=dependency_digest,
                ),
            ):
                outcome = run_once(
                    root / "attempt.json", preflight, "subject", "mock",
                    root / "evidence.json", "evidence.json", self.command(), 1,
                    runner, adapter_path=ADAPTER,
                )

        self.assertEqual(outcome["reason"], "PROVIDER_DEADLINE_EXCEEDED")
        runner.assert_not_called()

    def test_provider_requires_active_stage_state_and_attempt(self) -> None:
        result = {
            "cycle": {
                "current_stage": "deploy_provider",
                "stages": {"deploy_provider": {"status": "pass", "attempts": []}},
            },
        }
        with (
            mock.patch(
                "harness_provider_postcondition.budget_status",
                return_value={"decision": "pass"},
            ),
            mock.patch(
                "harness_provider_postcondition.stage_budget_status",
                return_value={"decision": "pass"},
            ),
        ):
            outcome, remaining = _provider_readiness(result, 30)

        self.assertEqual(outcome["reason"], "PROVIDER_STAGE_NOT_ACTIVE")
        self.assertEqual(remaining, 0)

    def test_stale_evidence_and_post_attempt_tampering_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = self.preflight()
            stale = root / "stale.json"
            stale.write_text(json.dumps(self.evidence()))
            stale_attempt = run_once(
                root / "stale-attempt.json", preflight, "subject", "mock", stale,
                "stale.json", self.command(), 1,
                lambda _command, **_kwargs: SimpleNamespace(returncode=0),
                adapter_path=ADAPTER,
            )

            fresh = root / "fresh.json"

            def runner(_command, **_kwargs):
                fresh.write_text(json.dumps(self.evidence(run=1)))
                return SimpleNamespace(returncode=0)

            receipt = run_once(
                root / "fresh-attempt.json", preflight, "subject", "mock", fresh,
                "fresh.json", self.command(), 1, runner, adapter_path=ADAPTER,
            )
            fresh.write_text(json.dumps(self.evidence(run=2)))
            tampered = validate_receipt(receipt, preflight, "subject", "mock", fresh)
        self.assertEqual(stale_attempt["reason"], "PROVIDER_EVIDENCE_STALE")
        self.assertEqual(tampered["decision"], "block")

    def test_provider_timeout_uses_minimum_stage_root_and_final_limit(self) -> None:
        observed = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
        observed_at = observed.isoformat().replace("+00:00", "Z")
        observed_ms = int(observed.timestamp() * 1000)
        base = {
            "cycle": {
                "started_at": (observed - timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
                "last_observed_at": (observed - timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
                "total_budget_ms": 100_000,
                "current_stage": "deploy_provider",
                "stage_budgets_ms": {"deploy_provider": 50_000},
                "stages": {
                    "deploy_provider": {
                        "status": "active", "wall_ms": 10_000,
                        "attempts": [{"started_epoch_ms": observed_ms - 20_000}],
                    },
                },
            },
        }
        final_limited = _remaining_provider_seconds(
            deepcopy(base), 15, at=observed_at, epoch_ms=observed_ms,
        )
        stage_limited = _remaining_provider_seconds(
            deepcopy(base), 60, at=observed_at, epoch_ms=observed_ms,
        )
        root_case = deepcopy(base)
        root_case["cycle"]["total_budget_ms"] = 25_000
        root_limited = _remaining_provider_seconds(
            root_case, 60, at=observed_at, epoch_ms=observed_ms,
        )
        self.assertEqual(final_limited, 15)
        self.assertEqual(stage_limited, 20)
        self.assertEqual(root_limited, 15)


if __name__ == "__main__":
    unittest.main()
