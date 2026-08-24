#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_review import build_checklist, load_and_validate_receipt  # noqa: E402


class AgentReviewTest(unittest.TestCase):
    def test_receipt_is_bound_to_diff_and_independent_reviewer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src.py").write_text("value = 1\n")
            checklist = build_checklist(root, ["src.py"])
            receipt = root / "receipt.json"
            receipt.write_text(json.dumps({
                "schema_version": 1,
                "subject_digest": checklist["subject_digest"],
                "reviewer_role": "qa-evaluator",
                "views": {name: "pass" for name in ("correctness", "security", "tests", "scope")},
                "findings": [],
                "decision": "pass",
            }))
            validated, error = load_and_validate_receipt(receipt, checklist)
            self.assertEqual(error, "")
            self.assertEqual(validated["decision"], "pass")

            (root / "src.py").write_text("value = 2\n")
            stale = build_checklist(root, ["src.py"])
            self.assertEqual(load_and_validate_receipt(receipt, stale)[1], "AGENT_REVIEW_SUBJECT_MISMATCH")

    def test_implementation_agent_cannot_self_sign(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checklist = build_checklist(root, [])
            receipt = root / "receipt.json"
            receipt.write_text(json.dumps({
                "schema_version": 1, "subject_digest": checklist["subject_digest"],
                "reviewer_role": "backend-agent",
                "views": {name: "pass" for name in ("correctness", "security", "tests", "scope")},
                "findings": [], "decision": "pass",
            }))
            self.assertEqual(
                load_and_validate_receipt(receipt, checklist)[1],
                "AGENT_REVIEW_INDEPENDENCE_INVALID",
            )


if __name__ == "__main__":
    unittest.main()
