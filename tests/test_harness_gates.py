#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_gates import (  # noqa: E402
    TIER_GATES, _run_gate_command, browser_required, committed_work_item,
    committed_work_item_resolution, prepare_ci_task, run_gate_plan,
)
from harness_gate_work_items import validate_ci_planning_credential  # noqa: E402
from harness_provider_preflight import execute_preflight  # noqa: E402
from harness_execution_authority import AuthorityTrust, build_receipt, sign_receipt  # noqa: E402
from harness_provider_authority import (  # noqa: E402
    SANDBOX_CLAIMS, build_binding as build_provider_authority_binding,
    provider_input_digest, sandbox_input_digest,
)
from harness_strict_gate import COMPONENT_SCHEMA  # noqa: E402
from provider_attempt import EVIDENCE_SCHEMA, run_once as run_provider_once  # noqa: E402
from workspace_paths import active_planning_gate_path, load_layout  # noqa: E402


class HarnessGatesTest(unittest.TestCase):
    def authority(self, root: Path, principal: str) -> tuple[Path, AuthorityTrust]:
        root.mkdir(parents=True, exist_ok=True)
        key = root / "key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True,
        )
        public = key.with_suffix(".pub").read_text(encoding="utf-8")
        allowed = root / "allowed_signers"
        allowed.write_text(f"{principal} {public}", encoding="utf-8")
        fingerprint = subprocess.check_output(
            ["ssh-keygen", "-lf", str(key.with_suffix(".pub")), "-E", "sha256"],
            text=True,
        ).split()[1]
        return key, AuthorityTrust(allowed, fingerprint, principal)

    def strict_components(self, product: Path, subject: str) -> dict:
        components = {}
        for name, authority in {
            "browser_qa": "browser-qa",
            "deployment": "deployment-controller",
            "rollback": "rollback-verifier",
        }.items():
            relative = f"evidence/{name}.json"
            artifact = product / relative
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(json.dumps({"decision": "pass", "name": name}))
            components[name] = {
                "schema": COMPONENT_SCHEMA,
                "decision": "pass",
                "subject_digest": subject,
                "authority": authority,
                "evidence_ref": relative,
                "evidence_digest": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            }
        return components

    def test_gate_plan_deadline_covers_input_digest_before_execution(self) -> None:
        class AdvancingClock:
            def __init__(self) -> None:
                self.value = -0.3

            def __call__(self) -> float:
                self.value += 0.3
                return self.value

        runner = mock.Mock()
        with mock.patch("harness_gates.run_process_group", runner):
            result = run_gate_plan(
                ROOT, ROOT, tier="lite", subject_digest="subject", policy_digest="policy",
                remaining_budget_ms=500, clock=AdvancingClock(),
            )

        runner.assert_not_called()
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["reason"], "STAGE_BUDGET_EXCEEDED")
        self.assertTrue(all(
            check["source"] == "not_executed" and check["attempt"] == 0
            for check in result["checks"].values()
        ))

    def test_ci_result_resolves_work_item_separately_from_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task = product / "harness-workspace/planning/tasks/task-1"
            task.mkdir(parents=True)
            (task / "task.json").write_text(json.dumps({
                "task_id": "task-1", "work_item": {"id": "WI-42", "provider": "jira"},
            }))
            self.assertEqual(
                committed_work_item(ROOT, product, "task-1"),
                {"id": "WI-42", "provider": "jira"},
            )
            self.assertEqual(
                committed_work_item_resolution(ROOT, product, "task-1"),
                {"status": "unique", "work_item": {"id": "WI-42", "provider": "jira"},
                 "candidates": [{"source": "task-1/task.json", "id": "WI-42",
                                  "provider": "jira"}]},
            )

    def test_legacy_task_resolves_by_unique_work_item_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            tasks = product / "harness-workspace/planning/tasks"
            for name in ("2026-08-12-WI-42-story", "unrelated"):
                task = tasks / name
                task.mkdir(parents=True)
                work_item_id = "WI-42" if name.startswith("2026") else "WI-99"
                (task / "planning_gate_pass.json").write_text(json.dumps({
                    "decision": "pass",
                    "work_item": {"id": work_item_id, "provider": "jira"},
                }))
            self.assertEqual(
                committed_work_item(ROOT, product, "WI-42"),
                {"id": "WI-42", "provider": "jira"},
            )

            duplicate = tasks / "duplicate"
            duplicate.mkdir()
            (duplicate / "task.json").write_text(json.dumps({
                "work_item": {"id": "WI-42", "provider": "jira"},
            }))
            self.assertIsNone(committed_work_item(ROOT, product, "WI-42"))
            resolution = committed_work_item_resolution(ROOT, product, "WI-42")
            self.assertEqual(resolution["status"], "ambiguous")
            self.assertEqual([item["source"] for item in resolution["candidates"]], [
                "2026-08-12-WI-42-story/planning_gate_pass.json", "duplicate/task.json",
            ])

    def test_direct_task_and_legacy_work_item_duplicate_is_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            tasks = product / "harness-workspace/planning/tasks"
            direct = tasks / "execution-1"
            direct.mkdir(parents=True)
            (direct / "task.json").write_text(json.dumps({
                "work_item": {"id": "WI-42", "provider": "feishu"},
            }))
            legacy = tasks / "2026-WI-42-story"
            legacy.mkdir()
            (legacy / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass", "work_item": {"id": "WI-42", "provider": "feishu"},
            }))

            resolution = committed_work_item_resolution(ROOT, product, "execution-1")
            self.assertEqual(resolution["status"], "ambiguous")
            self.assertEqual([item["source"] for item in resolution["candidates"]], [
                "2026-WI-42-story/planning_gate_pass.json", "execution-1/task.json",
            ])

    def test_same_id_merges_provider_but_conflicting_providers_are_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task_id = "provider-merge"
            task = product / "harness-workspace/planning/tasks" / task_id
            task.mkdir(parents=True)
            (task / "task.json").write_text(json.dumps({
                "work_item": {"id": "WI-42", "provider": ""},
            }))
            gate = task / "planning_gate_pass.json"
            gate.write_text(json.dumps({
                "work_item": {"id": "WI-42", "provider": "feishu"},
            }))
            self.assertEqual(
                committed_work_item_resolution(ROOT, product, task_id)["work_item"],
                {"id": "WI-42", "provider": "feishu"},
            )

            gate.write_text(json.dumps({
                "work_item": {"id": "WI-42", "provider": "jira"},
            }))
            (task / "phase0_pass.json").write_text(json.dumps({
                "work_item": {"id": "WI-42", "provider": "feishu"},
            }))
            resolution = committed_work_item_resolution(ROOT, product, task_id)
            self.assertEqual(resolution["status"], "ambiguous")
            self.assertEqual([item["provider"] for item in resolution["candidates"]], [
                "feishu", "jira", "",
            ])

    def test_frontend_change_requires_browser_but_backend_change_does_not(self) -> None:
        self.assertTrue(browser_required(["frontend/components/Login.tsx"]))
        self.assertTrue(browser_required(["templates/index.html"]))
        self.assertFalse(browser_required(["services/api.py"]))

    def test_standard_frontend_without_url_blocks_browser_gate(self) -> None:
        completed = mock.Mock(returncode=0, stdout='{"decision":"pass","reason":"OK"}')
        credential = mock.Mock()
        credential.read_text.return_value = '{"decision":"pass"}'
        with mock.patch("harness_gates.run_process_group", return_value=completed), \
                mock.patch("harness_gates.active_planning_gate_path", return_value=credential), \
                mock.patch.dict(os.environ, {"CI": "true"}, clear=True):
            result = run_gate_plan(
                ROOT, ROOT, tier="standard", subject_digest="subject", policy_digest="policy",
                changed_files=["frontend/App.tsx"],
            )
        self.assertEqual(result["checks"]["browser_qa"]["decision"], "block")
        self.assertEqual(result["decision"], "block")

    def test_ci_preparation_failure_blocks_before_gate_execution(self) -> None:
        runner = mock.Mock()
        with mock.patch("harness_gates.prepare_ci_task", return_value=False), \
                mock.patch("harness_gates.run_process_group", runner):
            result = run_gate_plan(
                ROOT, ROOT, tier="standard", subject_digest="subject", policy_digest="policy",
                ci_task_id="missing-task",
            )
        runner.assert_not_called()
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["reason"], "CI_PLANNING_CREDENTIAL_INVALID")
        self.assertTrue(all(check["source"] == "not_executed" for check in result["checks"].values()))

    def test_stale_active_ci_credential_cannot_be_reused_for_another_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            runs = product / "harness-workspace/runs"
            runs.mkdir(parents=True)
            (runs / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass", "ci_task_id": "old-task",
                "task_dir": str(product / "harness-workspace/planning/tasks/old-task"),
                "work_item": {"id": "WI-OLD", "provider": "jira"},
            }))
            runner = mock.Mock()
            with mock.patch("harness_gates.run_process_group", runner):
                result = run_gate_plan(
                    ROOT, product, tier="standard", subject_digest="subject",
                    policy_digest="policy", ci_task_id="new-task",
                )
        runner.assert_not_called()
        self.assertEqual(result["reason"], "CI_PLANNING_CREDENTIAL_INVALID")

    def test_ci_task_materializes_committed_planning_credential(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task = product / "harness-workspace" / "planning" / "tasks" / "task-1"
            task.mkdir(parents=True)
            (task / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass", "task_dir": "/stale/path",
                "work_item": {"id": "WI-1", "provider": "jira"},
            }))
            self.assertTrue(prepare_ci_task(ROOT, product, "task-1"))
            materialized = json.loads((
                product / "harness-workspace/runs/ci/task-1/planning_gate_pass.json"
            ).read_text())
            self.assertEqual(materialized["schema"], "harness-ci-planning-environment-v1")
            self.assertEqual(materialized["task_dir"], str(task.resolve()))
            self.assertEqual(materialized["ci_task_id"], "task-1")
            self.assertEqual(materialized["work_item"], {"id": "WI-1", "provider": "jira"})
            self.assertEqual(
                materialized["committed_source_ref"],
                "harness-workspace/planning/tasks/task-1/planning_gate_pass.json",
            )
            self.assertEqual(
                materialized["committed_source_digest"],
                hashlib.sha256((task / "planning_gate_pass.json").read_bytes()).hexdigest(),
            )
            self.assertFalse((product / "harness-workspace/runs/planning_gate_pass.json").exists())
            self.assertFalse((product / "harness-workspace/runs/active_task.json").exists())
            self.assertEqual(
                validate_ci_planning_credential(ROOT, product, "task-1", materialized)["decision"],
                "pass",
            )

            (task / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass", "work_item": {"id": "WI-1", "provider": "jira"},
                "changed": True,
            }))
            self.assertEqual(
                validate_ci_planning_credential(ROOT, product, "task-1", materialized)["reason"],
                "CI_PLANNING_SOURCE_DIGEST_MISMATCH",
            )

    def test_ci_task_resolves_slug_directory_by_work_item_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task = product / "harness-workspace/planning/tasks/2026-08-14-WI-42-story"
            task.mkdir(parents=True)
            (task / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass", "work_item": {"id": "WI-42", "provider": "feishu"},
            }))

            self.assertTrue(prepare_ci_task(ROOT, product, "WI-42"))
            materialized = json.loads((
                product / "harness-workspace/runs/ci/WI-42/planning_gate_pass.json"
            ).read_text())
            self.assertEqual(materialized["task_dir"], str(task.resolve()))
            self.assertEqual(materialized["ci_task_id"], "WI-42")

    def test_two_ci_tasks_prepare_concurrently_without_shared_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            tasks = product / "harness-workspace/planning/tasks"
            for task_id in ("task-a", "task-b"):
                task = tasks / task_id
                task.mkdir(parents=True)
                (task / "planning_gate_pass.json").write_text(json.dumps({
                    "decision": "pass",
                    "work_item": {"id": f"WI-{task_id[-1].upper()}", "provider": "jira"},
                }))

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(
                    lambda task_id: prepare_ci_task(ROOT, product, task_id),
                    ("task-a", "task-b"),
                ))

            self.assertEqual(outcomes, [True, True])
            runs = product / "harness-workspace/runs"
            self.assertFalse((runs / "active_task.json").exists())
            self.assertFalse((runs / "planning_gate_pass.json").exists())
            for task_id in ("task-a", "task-b"):
                credential = runs / "ci" / task_id / "planning_gate_pass.json"
                self.assertTrue(credential.is_file())
                with mock.patch.dict(os.environ, {
                    "HARNESS_CI_TASK_ID": task_id,
                    "HARNESS_CI_PLANNING_GATE": str(credential),
                }, clear=True):
                    self.assertEqual(
                        active_planning_gate_path(load_layout(ROOT, product)), credential.resolve(),
                    )

            with mock.patch.dict(os.environ, {
                "HARNESS_CI_TASK_ID": "task-a",
                "HARNESS_CI_PLANNING_GATE": str(runs / "planning_gate_pass.json"),
            }, clear=True):
                selected = active_planning_gate_path(load_layout(ROOT, product))
                self.assertIn("/runs/ci/_invalid/", str(selected))

    def test_ci_preparation_rejects_symlinked_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product = root / "product"
            task = product / "harness-workspace/planning/tasks/task-1"
            task.mkdir(parents=True)
            (task / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass", "work_item": {"id": "WI-1", "provider": "jira"},
            }))
            runs = product / "harness-workspace/runs"
            runs.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            (runs / "ci").symlink_to(outside, target_is_directory=True)

            self.assertFalse(prepare_ci_task(ROOT, product, "task-1"))
            self.assertFalse((outside / "task-1/planning_gate_pass.json").exists())

    def test_ci_credential_binding_mismatches_block_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            tasks = product / "harness-workspace/planning/tasks"
            task = tasks / "task-1"
            other = tasks / "other-task"
            task.mkdir(parents=True)
            other.mkdir()
            (task / "task.json").write_text(json.dumps({
                "task_id": "task-1", "work_item": {"id": "WI-1", "provider": "jira"},
            }))
            (task / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass", "work_item": {"id": "WI-1", "provider": "jira"},
            }))
            self.assertTrue(prepare_ci_task(ROOT, product, "task-1"))
            base = json.loads((
                product / "harness-workspace/runs/ci/task-1/planning_gate_pass.json"
            ).read_text())
            variants = {
                "CI_PLANNING_TASK_ID_MISMATCH": {**base, "ci_task_id": "other-task"},
                "CI_PLANNING_TASK_DIR_MISMATCH": {**base, "task_dir": str(other.resolve())},
                "CI_PLANNING_WORK_ITEM_MISMATCH": {
                    **base, "work_item": {"id": "WI-2", "provider": "jira"},
                },
                "CI_PLANNING_PROVIDER_MISMATCH": {
                    **base, "work_item": {"id": "WI-1", "provider": "feishu"},
                },
            }
            credential_path = (
                product / "harness-workspace/runs/ci/task-1/planning_gate_pass.json"
            )
            for expected_reason, payload in variants.items():
                with self.subTest(reason=expected_reason):
                    credential_path.write_text(json.dumps(payload))
                    runner = mock.Mock()
                    with mock.patch("harness_gates.prepare_ci_task", return_value=True), \
                            mock.patch("harness_gates.run_process_group", runner):
                        result = run_gate_plan(
                            ROOT, product, tier="standard", subject_digest="subject",
                            policy_digest="policy", ci_task_id="task-1",
                        )
                    runner.assert_not_called()
                    self.assertEqual(result["reason"], expected_reason)

    def test_ci_gate_plan_propagates_task_specific_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task = product / "harness-workspace/planning/tasks/task-1"
            task.mkdir(parents=True)
            (task / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass", "work_item": {"id": "WI-1", "provider": "jira"},
            }))
            completed = mock.Mock(returncode=0, stdout='{"decision":"pass","reason":"OK"}')
            with mock.patch("harness_gates.run_process_group", return_value=completed) as runner:
                result = run_gate_plan(
                    ROOT, product, tier="standard", subject_digest="subject",
                    policy_digest="policy", ci_task_id="task-1",
                )

            self.assertEqual(result["decision"], "pass")
            expected = str((
                product / "harness-workspace/runs/ci/task-1/planning_gate_pass.json"
            ).resolve())
            self.assertTrue(runner.call_args_list)
            for call in runner.call_args_list:
                self.assertEqual(call.kwargs["env"]["HARNESS_CI_TASK_ID"], "task-1")
                self.assertEqual(call.kwargs["env"]["HARNESS_CI_PLANNING_GATE"], expected)

    def test_local_finish_task_rejects_stale_planning_credential(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            tasks = product / "harness-workspace/planning/tasks"
            current = tasks / "task-1"
            stale = tasks / "task-2"
            current.mkdir(parents=True)
            stale.mkdir()
            (current / "task.json").write_text(json.dumps({
                "task_id": "task-1", "work_item": {"id": "WI-1", "provider": "jira"},
            }))
            (stale / "task.json").write_text(json.dumps({
                "task_id": "task-2", "work_item": {"id": "WI-2", "provider": "jira"},
            }))
            credential = mock.Mock()
            credential.read_text.return_value = json.dumps({
                "decision": "pass", "task_dir": str(stale),
                "work_item": {"id": "WI-2", "provider": "jira"},
            })
            runner = mock.Mock()
            with mock.patch(
                "harness_gates.active_planning_gate_path", return_value=credential,
            ), mock.patch("harness_gates.run_process_group", runner):
                result = run_gate_plan(
                    ROOT, product, tier="standard", subject_digest="subject",
                    policy_digest="policy", task_id="task-1", work_item_id="WI-1",
                    work_item_provider="jira",
                )

        runner.assert_not_called()
        self.assertEqual(result["reason"], "CI_PLANNING_TASK_DIR_MISMATCH")

    def test_lite_omits_planning_qa_and_strict_evidence(self) -> None:
        gates = set(TIER_GATES["lite"])
        self.assertNotIn("planning", gates)
        self.assertNotIn("qa_evidence", gates)
        self.assertNotIn("strict_evidence", gates)
        self.assertIn("quality_test", gates)

    def test_standard_requires_planning_and_qa(self) -> None:
        gates = set(TIER_GATES["standard"])
        self.assertIn("planning", gates)
        self.assertIn("qa_evidence", gates)
        self.assertNotIn("strict_evidence", gates)

    def test_standard_qa_failure_blocks_shared_result(self) -> None:
        def completed(argv, **_kwargs):
            decision = "block" if "qa_evidence_check.sh" in " ".join(argv) else "pass"
            return mock.Mock(returncode=0, stdout=json.dumps({
                "decision": decision, "reason": "QA_MISSING" if decision == "block" else "OK",
            }))

        credential = mock.Mock()
        credential.read_text.return_value = '{"decision":"pass"}'
        with mock.patch("harness_gates.run_process_group", side_effect=completed), \
                mock.patch("harness_gates.active_planning_gate_path", return_value=credential), \
                mock.patch.dict(os.environ, {"CI": "true"}):
            result = run_gate_plan(
                ROOT, ROOT, tier="standard", subject_digest="subject", policy_digest="policy",
            )
        self.assertEqual(result["checks"]["qa_evidence"]["decision"], "block")
        self.assertEqual(result["decision"], "block")

    def test_gate_timeout_fails_closed_with_gate_identity(self) -> None:
        credential = mock.Mock()
        credential.read_text.return_value = '{"decision":"pass"}'
        expired = subprocess.TimeoutExpired(["bash", "gate.sh"], 7, output="partial output")

        def started(*_args, **_kwargs):
            if not getattr(started, "called", False):
                started.called = True
                raise expired
            return mock.Mock(returncode=0, stdout='{"decision":"pass","reason":"OK"}')

        with mock.patch("harness_gates.run_process_group", side_effect=started) as runner, \
                mock.patch("harness_gates.active_planning_gate_path", return_value=credential), \
                mock.patch.dict(os.environ, {
                    "CI": "true", "HARNESS_GATE_TIMEOUT_SECONDS": "7",
                }, clear=True):
            result = run_gate_plan(
                ROOT, ROOT, tier="standard", subject_digest="subject", policy_digest="policy",
            )
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["checks"]["harness"]["decision"], "block")
        self.assertEqual(
            result["checks"]["harness"]["reason"],
            "GATE_TIMEOUT: gate=harness; timeout=7s; log=partial output",
        )
        self.assertEqual(runner.call_args_list[0].kwargs["timeout"], 7)

    def test_gate_timeout_terminates_descendants_before_they_can_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "late-write"
            payload, returncode = _run_gate_command(
                [
                    "bash", "-c",
                    '(trap "" TERM; sleep 3; touch "$1") >/dev/null 2>&1 & wait',
                    "_", str(marker),
                ],
                gate="integration", cwd=ROOT, env=dict(os.environ), timeout=1,
            )
            time.sleep(1.5)
            self.assertFalse(marker.exists())
        self.assertEqual(returncode, 124)
        self.assertEqual(payload["decision"], "block")

    def test_knowledge_timeout_is_not_hidden_by_autosync(self) -> None:
        calls = []

        def gate_result(_command, *, gate, **_kwargs):
            calls.append(gate)
            if gate == "knowledge":
                return {"decision": "block", "reason": "GATE_TIMEOUT: gate=knowledge; timeout=7s"}, 124
            return {"decision": "pass", "reason": "OK"}, 0

        credential = mock.Mock()
        credential.read_text.return_value = '{"decision":"pass"}'
        with mock.patch("harness_gates._run_gate_command", side_effect=gate_result), \
                mock.patch("harness_gates.active_planning_gate_path", return_value=credential), \
                mock.patch.dict(os.environ, {}, clear=True):
            result = run_gate_plan(
                ROOT, ROOT, tier="standard", subject_digest="subject", policy_digest="policy",
            )
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["checks"]["knowledge"]["decision"], "block")
        self.assertNotIn("knowledge_autosync", calls)

    def test_strict_requires_subject_bound_production_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            receipt = product / "strict.json"
            receipt.write_text(json.dumps({
                "subject_digest": "subject-a",
                **self.strict_components(product, "subject-a"),
            }))
            completed = mock.Mock(returncode=0, stdout='{"decision":"pass","reason":"OK"}')
            credential = mock.Mock()
            credential.read_text.return_value = '{"decision":"pass"}'
            with mock.patch("harness_gates.run_process_group", return_value=completed), \
                    mock.patch("harness_gates.active_planning_gate_path", return_value=credential), \
                    mock.patch.dict(os.environ, {"HARNESS_STRICT_EVIDENCE": str(receipt)}):
                passed = run_gate_plan(
                    ROOT, product, tier="strict", subject_digest="subject-a", policy_digest="policy",
                )
                stale = run_gate_plan(
                    ROOT, product, tier="strict", subject_digest="subject-b", policy_digest="policy",
                )
            self.assertEqual(passed["checks"]["strict_evidence"]["decision"], "pass")
            self.assertEqual(stale["checks"]["strict_evidence"]["decision"], "block")
            self.assertEqual(stale["decision"], "block")

    def test_strict_receipt_decode_failure_is_structured_block(self) -> None:
        credential = mock.Mock()
        credential.read_text.return_value = '{"decision":"pass"}'
        decode_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
        with mock.patch("harness_gates.run_process_group", return_value=mock.Mock(
            returncode=0, stdout='{"decision":"pass","reason":"OK"}',
        )), mock.patch("harness_gates.active_planning_gate_path", return_value=credential), \
                mock.patch("harness_gates._strict_evidence", side_effect=decode_error):
            result = run_gate_plan(
                ROOT, ROOT, tier="strict", subject_digest="subject", policy_digest="policy",
            )

        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["checks"]["strict_evidence"]["reason"], "STRICT_EVIDENCE_INVALID")

    def test_synthetic_canary_cannot_satisfy_declared_real_provider_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            receipt = Path(tmp) / "strict.json"
            base_receipt = {
                "subject_digest": "subject-a",
                **self.strict_components(product, "subject-a"),
            }
            receipt.write_text(json.dumps({
                **base_receipt,
                "provider_acceptance": {
                    "decision": "pass", "provider_mode": "synthetic",
                    "synthetic_only": True, "evidence_ref": "canary.json",
                },
            }))
            completed = mock.Mock(returncode=0, stdout='{"decision":"pass","reason":"OK"}')
            credential = mock.Mock()
            credential.read_text.return_value = json.dumps({
                "decision": "pass",
                "work_item": {
                    "provider": "mock",
                    "production_evidence": {"provider_mode": "real_required"},
                },
            })
            with mock.patch("harness_gates.run_process_group", return_value=completed), \
                    mock.patch("harness_gates.active_planning_gate_path", return_value=credential), \
                    mock.patch.dict(os.environ, {"HARNESS_STRICT_EVIDENCE": str(receipt)}):
                synthetic = run_gate_plan(
                    ROOT, product, tier="strict", subject_digest="subject-a", policy_digest="policy",
                )
                forged = {
                    **base_receipt,
                    "provider_preflight": {
                        "schema": "harness-provider-preflight-receipt-v1",
                        "decision": "pass", "subject_digest": "subject-a",
                        "offline": True, "network_calls": 0,
                        "contract_digest": "a" * 64,
                        "trace_digest": "b" * 64,
                        "verifier_digest": "c" * 64,
                    },
                    "provider_acceptance": {
                        "decision": "pass", "provider_mode": "real",
                        "synthetic_only": False,
                        "evidence_ref": "evidence/production/provider-receipt.json",
                    },
                }
                receipt.write_text(json.dumps(forged))
                forged_result = run_gate_plan(
                    ROOT, product, tier="strict", subject_digest="subject-a", policy_digest="policy",
                )
                adapter = ROOT / "tests/fixtures/provider_mock_adapter.py"
                adapter_command = [
                    sys.executable, str(adapter),
                    "--subject", "subject-a", "--provider", "mock",
                ]
                forged["provider_preflight"] = execute_preflight({
                    "schema": "harness-provider-call-contract-v2",
                    "subject_digest": "subject-a", "provider": "mock",
                    "required_calls": ["provider.accept"],
                    "allowed_calls": ["provider.accept"],
                }, "subject-a", "mock", adapter, adapter_command)
                provider_evidence = Path(tmp) / "provider-evidence.json"

                def write_provider_evidence(_command, **_kwargs):
                    provider_evidence.write_text(json.dumps({
                        "schema": EVIDENCE_SCHEMA, "decision": "pass",
                        "subject_digest": "subject-a", "provider": "mock",
                    }))
                    return mock.Mock(returncode=0)

                attempt_receipt = run_provider_once(
                    Path(tmp) / "provider-attempt.json", forged["provider_preflight"],
                    "subject-a", "mock", provider_evidence,
                    "provider-evidence.json", adapter_command, 1,
                    write_provider_evidence, adapter_path=adapter,
                )
                forged["provider_acceptance"].update({
                    "provider": "mock",
                    "attempt_receipt": attempt_receipt,
                    "evidence_ref": "provider-evidence.json",
                    "preflight_receipt_digest": forged["provider_preflight"]["receipt_digest"],
                    "attempt_receipt_digest": attempt_receipt["receipt_digest"],
                    "contract_digest": attempt_receipt["contract_digest"],
                    "adapter_digest": attempt_receipt["adapter_digest"],
                    "canonical_argv_digest": attempt_receipt["canonical_argv_digest"],
                    "evidence_digest": attempt_receipt["evidence_digest"],
                    "attempt_verifier_digest": attempt_receipt["attempt_verifier_digest"],
                })
                receipt.write_text(json.dumps(forged))
                unsigned_real = run_gate_plan(
                    ROOT, product, tier="strict", subject_digest="subject-a", policy_digest="policy",
                )
                sandbox_key, sandbox_trust = self.authority(
                    product / "sandbox-authority", "harness-network-sandbox",
                )
                provider_key, provider_trust = self.authority(
                    product / "provider-authority", "harness-provider-response",
                )
                issued = datetime.now(timezone.utc)
                sandbox_authority = sign_receipt(build_receipt(
                    authority="network-sandbox", action="provider-preflight",
                    subject_digest="subject-a", provider="mock",
                    input_digest=sandbox_input_digest(forged["provider_preflight"]),
                    output_digest=forged["provider_preflight"]["trace_digest"],
                    claims=SANDBOX_CLAIMS, issued_at=issued,
                    expires_at=issued + timedelta(minutes=5),
                ), sandbox_key)
                provider_authority = sign_receipt(build_receipt(
                    authority="provider-response", action="provider-execution",
                    subject_digest="subject-a", provider="mock",
                    input_digest=provider_input_digest(
                        forged["provider_preflight"], attempt_receipt,
                    ),
                    output_digest=attempt_receipt["evidence_digest"],
                    claims={
                        "evidence_ref": attempt_receipt["evidence_ref"],
                        "evidence_schema": attempt_receipt["evidence_schema"],
                        "response_authoritative": True,
                    },
                    issued_at=issued, expires_at=issued + timedelta(minutes=5),
                ), provider_key)
                forged["provider_authorities"] = build_provider_authority_binding(
                    preflight=forged["provider_preflight"], attempt=attempt_receipt,
                    sandbox_authority=sandbox_authority,
                    provider_authority=provider_authority,
                    sandbox_trust=sandbox_trust, provider_trust=provider_trust,
                )
                receipt.write_text(json.dumps(forged))
                authority_trust = {
                    "network-sandbox": sandbox_trust,
                    "provider-response": provider_trust,
                }
                with mock.patch(
                    "harness_strict_gate.trust_from_installation",
                    side_effect=lambda _root, authority, _principal: authority_trust[authority],
                ):
                    real = run_gate_plan(
                        ROOT, product, tier="strict", subject_digest="subject-a",
                        policy_digest="policy",
                    )
                    provider_evidence.write_text('{"decision":"pass","tampered":true}')
                    tampered = run_gate_plan(
                        ROOT, product, tier="strict", subject_digest="subject-a",
                        policy_digest="policy",
                    )

            self.assertEqual(
                synthetic["checks"]["strict_evidence"]["reason"],
                "STRICT_PROVIDER_PREFLIGHT_REQUIRED",
            )
            self.assertEqual(synthetic["decision"], "block")
            self.assertEqual(
                forged_result["checks"]["strict_evidence"]["reason"],
                "STRICT_PROVIDER_PREFLIGHT_REQUIRED",
            )
            self.assertEqual(
                unsigned_real["checks"]["strict_evidence"]["reason"],
                "STRICT_PROVIDER_AUTHORITY_REQUIRED",
            )
            self.assertEqual(real["checks"]["strict_evidence"]["decision"], "pass")
            self.assertEqual(tampered["checks"]["strict_evidence"]["decision"], "block")


if __name__ == "__main__":
    unittest.main()
