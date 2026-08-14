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

from qa_evidence_check import qa_candidates, validate_qa_json  # noqa: E402
from workspace_paths import load_layout  # noqa: E402


class QaEvidenceTest(unittest.TestCase):
    def test_work_item_never_falls_back_to_global_qa_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            (product / "harness-workspace").mkdir()
            (product / "harness-workspace/project.yaml").write_text(
                "product:\n  id: demo\nworkspace:\n  root: harness-workspace\n  runs: runs\n  evidence: evidence\n",
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
