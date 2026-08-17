#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_gates import (  # noqa: E402
    TIER_GATES, _run_gate_command, browser_required, committed_work_item, prepare_ci_task,
    run_gate_plan,
)


class HarnessGatesTest(unittest.TestCase):
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

    def test_ci_task_materializes_committed_planning_credential(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task = product / "harness-workspace" / "planning" / "tasks" / "task-1"
            task.mkdir(parents=True)
            (task / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass", "task_dir": "/stale/path",
                "work_item": {"id": "WI-1"},
            }))
            self.assertTrue(prepare_ci_task(ROOT, product, "task-1"))
            materialized = json.loads((
                product / "harness-workspace" / "runs" / "planning_gate_pass.json"
            ).read_text())
            self.assertEqual(materialized["task_dir"], str(task.resolve()))

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
                product / "harness-workspace/runs/planning_gate_pass.json"
            ).read_text())
            self.assertEqual(materialized["task_dir"], str(task.resolve()))

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
            receipt = Path(tmp) / "strict.json"
            receipt.write_text(json.dumps({
                "subject_digest": "subject-a",
                "browser_qa": {"decision": "pass"},
                "deployment": {"decision": "pass"},
                "rollback": {"decision": "pass"},
            }))
            completed = mock.Mock(returncode=0, stdout='{"decision":"pass","reason":"OK"}')
            credential = mock.Mock()
            credential.read_text.return_value = '{"decision":"pass"}'
            with mock.patch("harness_gates.run_process_group", return_value=completed), \
                    mock.patch("harness_gates.active_planning_gate_path", return_value=credential), \
                    mock.patch.dict(os.environ, {"HARNESS_STRICT_EVIDENCE": str(receipt)}):
                passed = run_gate_plan(
                    ROOT, ROOT, tier="strict", subject_digest="subject-a", policy_digest="policy",
                )
                stale = run_gate_plan(
                    ROOT, ROOT, tier="strict", subject_digest="subject-b", policy_digest="policy",
                )
            self.assertEqual(passed["checks"]["strict_evidence"]["decision"], "pass")
            self.assertEqual(stale["checks"]["strict_evidence"]["decision"], "block")
            self.assertEqual(stale["decision"], "block")


if __name__ == "__main__":
    unittest.main()
