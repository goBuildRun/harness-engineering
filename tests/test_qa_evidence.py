#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from qa_evidence_check import qa_candidates, report_passes, validate_qa_json  # noqa: E402
from task_workspace import activate_task, qa_evidence_path, task_workspace_dir  # noqa: E402
from workspace_paths import load_layout  # noqa: E402


class QaEvidenceTest(unittest.TestCase):
    def test_reports_accept_canonical_pass_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_report = Path(tmp) / "TEST.md"
            review_report = Path(tmp) / "REVIEW.md"
            test_report.write_text("# TEST\n\n- 结论：`pass`\n", encoding="utf-8")
            review_report.write_text("# REVIEW\n\n结论: PASS\n", encoding="utf-8")

            self.assertTrue(report_passes(test_report, "TEST"))
            self.assertTrue(report_passes(review_report, "REVIEW"))

    def test_reports_reject_noncanonical_pass_prose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "TEST.md"
            report.write_text("结论：pass after follow-up\n", encoding="utf-8")

            self.assertFalse(report_passes(report, "TEST"))

    def test_activation_rejects_invalid_gate_identity_without_external_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp) / "product"
            task = product / "ael-workspace/planning/tasks/unsafe-gate"
            task.mkdir(parents=True)
            (product / "ael-workspace/project.yaml").write_text(
                "product:\n  id: demo\nworkspace:\n  root: ael-workspace\n  planning: planning\n  runs: runs\n",
                encoding="utf-8",
            )
            (task / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass", "work_item": {"id": "../../escape"},
                "task_dir": str(task),
            }))
            layout = load_layout(ROOT, product)

            result = activate_task(layout, "")

            self.assertEqual(result, {"ok": False, "reason": "TASK_ID_INVALID"})
            with self.assertRaisesRegex(ValueError, "TASK_ID_INVALID"):
                task_workspace_dir(layout, "../../escape")
            self.assertFalse((product / "ael-workspace/escape").exists())

    def test_qa_sign_off_rejects_invalid_active_identity_without_external_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp) / "product"
            runs = product / "ael-workspace/runs"
            runs.mkdir(parents=True)
            (product / "ael-workspace/project.yaml").write_text(
                "product:\n  id: demo\nworkspace:\n  root: ael-workspace\n  runs: runs\n  evidence: evidence\n",
                encoding="utf-8",
            )
            (runs / "active_task.json").write_text(json.dumps({"work_item_id": "../../escape"}))

            completed = subprocess.run(
                ["bash", str(SCRIPTS / "qa_sign_off.sh"), "T1", "fail", "reject identity"],
                cwd=product, env={**os.environ, "AEL_PRODUCT_ROOT": str(product)},
                text=True, capture_output=True, check=True,
            )

            self.assertEqual(json.loads(completed.stdout)["reason"], "ACTIVE_TASK_ID_INVALID")
            self.assertFalse((product / "ael-workspace/escape").exists())

    def test_malformed_active_task_cannot_fall_back_to_legacy_qa_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp) / "product"
            runs = product / "ael-workspace/runs"
            runs.mkdir(parents=True)
            (product / "ael-workspace/project.yaml").write_text(
                "product:\n  id: demo\nworkspace:\n  root: ael-workspace\n  runs: runs\n  evidence: evidence\n",
                encoding="utf-8",
            )
            (runs / "active_task.json").write_text("[]")
            (runs / "qa_approved_T1.json").write_text(json.dumps({
                "decision": "pass", "task_id": "T1", "reviewer": "qa-evaluator",
                "structure_gate": "pass", "paths_reviewed": [],
            }))
            env = {**os.environ, "AEL_PRODUCT_ROOT": str(product)}

            sign_off = subprocess.run(
                ["bash", str(SCRIPTS / "qa_sign_off.sh"), "T1", "fail", "must block"],
                cwd=product, env=env, text=True, capture_output=True, check=True,
            )
            gate = subprocess.run(
                ["bash", str(SCRIPTS / "subagent-pr-gate.sh"), "T1"],
                cwd=product, env=env, text=True, capture_output=True, check=True,
            )
            layout = load_layout(ROOT, product)

            self.assertEqual(json.loads(sign_off.stdout)["reason"], "ACTIVE_TASK_INVALID")
            self.assertEqual(json.loads(gate.stdout)["decision"], "block")
            self.assertEqual(json.loads(gate.stdout)["reason"], "ACTIVE_TASK_ID_INVALID")
            with self.assertRaisesRegex(ValueError, "ACTIVE_TASK_INVALID"):
                qa_evidence_path(layout, "T1")

    def test_qa_sign_off_scopes_lean_runtime_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            workspace = product / "ael-workspace"
            runs = workspace / "runs"
            runs.mkdir(parents=True)
            (workspace / "project.yaml").write_text(
                "product:\n  id: demo\nworkspace:\n  root: ael-workspace\n  runs: runs\n  evidence: evidence\n",
                encoding="utf-8",
            )
            (runs / "active_task.json").write_text(
                json.dumps({"task_id": "WI-42", "activated_at": "2026-08-14T00:00:00Z"}),
                encoding="utf-8",
            )

            completed = subprocess.run(
                ["bash", str(SCRIPTS / "qa_sign_off.sh"), "T1", "fail", "expected rejection"],
                cwd=product,
                env={**os.environ, "AEL_PRODUCT_ROOT": str(product)},
                text=True,
                capture_output=True,
                check=True,
            )

            scoped = runs / "tasks/WI-42/qa_approved_T1.json"
            self.assertTrue(scoped.is_file(), completed.stdout + completed.stderr)
            self.assertFalse((runs / "qa_approved_T1.json").exists())
            self.assertEqual(json.loads(scoped.read_text())["work_item_id"], "WI-42")

    def test_qa_sign_off_does_not_append_duplicate_task_document_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            workspace = product / "ael-workspace"
            runs = workspace / "runs"
            task = workspace / "planning/tasks/task-WI-42"
            runs.mkdir(parents=True)
            task.mkdir(parents=True)
            (workspace / "project.yaml").write_text(
                "product:\n  id: demo\nworkspace:\n  root: ael-workspace\n"
                "  planning: planning\n  runs: runs\n  evidence: evidence\n",
                encoding="utf-8",
            )
            (runs / "active_task.json").write_text(
                json.dumps({"task_id": "WI-42"}), encoding="utf-8"
            )
            (runs / "planning_gate_pass.json").write_text(
                json.dumps({"task_dir": str(task)}), encoding="utf-8"
            )
            qa_document = task / "05-QA验收.md"
            qa_document.write_text("# QA\n", encoding="utf-8")

            subprocess.run(
                ["bash", str(SCRIPTS / "qa_sign_off.sh"), "T1", "fail", "portable"],
                cwd=product,
                env={**os.environ, "AEL_PRODUCT_ROOT": str(product)},
                text=True,
                capture_output=True,
                check=True,
            )

            text = qa_document.read_text(encoding="utf-8")
            self.assertEqual(text, "# QA\n")
            self.assertTrue((runs / "tasks/WI-42/qa_approved_T1.json").is_file())

    def test_work_item_never_falls_back_to_global_qa_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            (product / "ael-workspace").mkdir()
            (product / "ael-workspace/project.yaml").write_text(
                "product:\n  id: demo\nworkspace:\n  root: ael-workspace\n  runs: runs\n  evidence: evidence\n",
                encoding="utf-8",
            )
            layout = load_layout(ROOT, product)
            candidates = qa_candidates(layout, "WI-42", "T1")

        self.assertEqual(candidates, [layout.agent_workspace / "tasks/WI-42/qa_approved_T1.json"])

    def test_qa_receipt_must_bind_work_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipt = Path(tmp) / "qa.json"
            receipt.write_text(json.dumps({
                "decision": "pass", "task_id": "T1", "work_item_id": "WI-41",
                "reviewer": "qa-evaluator", "structure_gate": "pass", "paths_reviewed": [],
            }), encoding="utf-8")

            issues = validate_qa_json(receipt, "T1", "WI-42")

        self.assertTrue(any(issue.startswith("QA_WORK_ITEM_MISMATCH:") for issue in issues))

if __name__ == "__main__":
    unittest.main()
