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
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_runtime import atomic_write_result, default_result  # noqa: E402
from qa_evidence_binding import prepare_bundle, receipt_binding  # noqa: E402
from qa_evidence_check import validate_qa_json  # noqa: E402
from workspace_paths import load_layout  # noqa: E402
from worktree_baseline import capture_baseline, changed_since_baseline  # noqa: E402


class QaEvidenceBindingTest(unittest.TestCase):
    def fixture(self, root: Path):
        product = root / "product"
        workspace = product / "harness-workspace"
        task = workspace / "planning/tasks/demo"
        runs = workspace / "runs"
        task.mkdir(parents=True)
        runs.mkdir(parents=True)
        (workspace / "project.yaml").write_text(
            "product:\n  id: demo\n  name: Demo\nworkspace:\n  root: harness-workspace\n  planning: planning\n  runs: runs\n  evidence: evidence\nwork_item:\n  provider: noop\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=product, check=True)
        subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=product, check=True)
        subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=product, check=True)
        (product / "source.py").write_text("before\n")
        (product / ".gitignore").write_text("harness-workspace/runs/\n")
        subprocess.run(["git", "add", "."], cwd=product, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=product, check=True)
        (runs / "active_task.json").write_text(json.dumps({"task_id": "WI-42"}))
        (runs / "planning_gate_pass.json").write_text(json.dumps({
            "decision": "pass", "task_dir": str(task),
            "work_item": {"id": "WI-42", "provider": "noop"},
        }))
        task_root = runs / "tasks/WI-42"
        capture_baseline(product, task_root / "worktree_baseline.json", work_item_id="WI-42")
        result = default_result("WI-42", work_item={"id": "WI-42", "provider": "noop"})
        result["cost"]["story_usage_baseline"] = {"session_id": "implementer"}
        atomic_write_result(task_root / "result.json", result)
        (product / "source.py").write_text("after\n")
        layout = load_layout(ROOT, product)
        layout.test_reports_dir.mkdir(parents=True)
        layout.review_reports_dir.mkdir(parents=True)
        (layout.test_reports_dir / "WI-42-T1-TEST.md").write_text("结论：`pass`\n")
        (layout.review_reports_dir / "WI-42-T1-REVIEW.md").write_text("结论：`pass`\n")
        return product, layout, task_root, changed_since_baseline(
            product, task_root / "worktree_baseline.json",
        )

    def test_shared_bundle_is_reused_and_receipt_is_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, layout, task_root, paths = self.fixture(Path(tmp))
            runner = lambda _harness, _product: {"decision": "pass", "reason": "TEST_PASS"}
            first = prepare_bundle(ROOT, product, "WI-42", paths, runner)
            second = prepare_bundle(ROOT, product, "WI-42", paths, runner)
            binding = receipt_binding(ROOT, product, "WI-42", "T1", "reviewer", paths)
            receipt = {
                "task_id": "T1", "work_item_id": "WI-42", "decision": "pass",
                "reviewer": "qa-evaluator", "structure_gate": "pass",
                "paths_reviewed": paths, **binding["binding"],
            }
            path = task_root / "qa_approved_T1.json"
            path.write_text(json.dumps(receipt))
            issues = validate_qa_json(path, "T1", "WI-42", layout)
        self.assertEqual(first["reason"], "QA_BUNDLE_PREPARED")
        self.assertEqual(second["reason"], "QA_BUNDLE_REUSED")
        self.assertEqual(issues, [])

    def test_same_session_self_sign_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, _layout, _task_root, paths = self.fixture(Path(tmp))
            prepare_bundle(
                ROOT, product, "WI-42", paths,
                lambda _harness, _product: {"decision": "pass"},
            )
            result = receipt_binding(
                ROOT, product, "WI-42", "T1", "implementer", paths,
            )
        self.assertEqual(result["reason"], "QA_INDEPENDENCE_UNPROVEN")

    def test_source_change_invalidates_old_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, layout, task_root, paths = self.fixture(Path(tmp))
            prepare_bundle(
                ROOT, product, "WI-42", paths,
                lambda _harness, _product: {"decision": "pass"},
            )
            binding = receipt_binding(ROOT, product, "WI-42", "T1", "reviewer", paths)
            receipt = {
                "task_id": "T1", "work_item_id": "WI-42", "decision": "pass",
                "reviewer": "qa-evaluator", "structure_gate": "pass",
                "paths_reviewed": paths, **binding["binding"],
            }
            path = task_root / "qa_approved_T1.json"
            path.write_text(json.dumps(receipt))
            (product / "source.py").write_text("changed again\n")
            issues = validate_qa_json(path, "T1", "WI-42", layout)
        self.assertTrue(any(item.startswith("QA_SUBJECT_MISMATCH:") for item in issues))

    def test_failed_mechanical_gate_and_unknown_implementer_cannot_sign(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, _layout, task_root, paths = self.fixture(Path(tmp))
            blocked = prepare_bundle(
                ROOT, product, "WI-42", paths,
                lambda _harness, _product: {"decision": "block", "reason": "TEST_BLOCK"},
            )
            result = json.loads((task_root / "result.json").read_text())
            result["cost"]["story_usage_baseline"]["session_id"] = "unknown"
            atomic_write_result(task_root / "result.json", result)
            prepare_bundle(
                ROOT, product, "WI-42", paths,
                lambda _harness, _product: {"decision": "pass"},
            )
            unknown = receipt_binding(ROOT, product, "WI-42", "T1", "reviewer", paths)
        self.assertEqual(blocked["decision"], "block")
        self.assertEqual(unknown["reason"], "QA_INDEPENDENCE_UNPROVEN")


if __name__ == "__main__":
    unittest.main()
