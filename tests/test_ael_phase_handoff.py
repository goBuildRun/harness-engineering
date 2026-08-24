#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ael_phase_handoff import MAX_CONTEXT_CHARS, build, write  # noqa: E402
from ael_runtime import default_result  # noqa: E402


class HarnessPhaseHandoffTest(unittest.TestCase):
    def test_handoff_is_bounded_digest_only_and_privacy_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_root = Path(tmp)
            (task_root / "context_index.json").write_text(
                json.dumps({"summary": "bounded", "full_text_included": False}),
                encoding="utf-8",
            )
            result = default_result("phase-task", initial_tier="strict")
            result["candidate"] = {"digest": "candidate-digest"}
            result["evidence_manifest"] = {"evidence_digest": "evidence-digest"}
            result["blockers"] = ["STRUCTURED_BLOCKER"]
            payload = write(result, task_root, "independent_qa")
            raw = (task_root / "phase-handoff.json").read_text(encoding="utf-8")
        self.assertLessEqual(len(raw), MAX_CONTEXT_CHARS)
        self.assertEqual(payload["admission"]["mode"], "digest-first")
        self.assertFalse(payload["admission"]["full_artifacts_included"])
        self.assertEqual(payload["candidate_digest"], "candidate-digest")
        self.assertNotIn("messages", raw)
        self.assertNotIn("prompt", raw.lower())

    def test_invalid_stage_remains_structured_instead_of_injecting_context(self) -> None:
        result = default_result("phase-task")
        payload = build(result, Path("/missing/task"), "not-a-stage")
        self.assertEqual(payload["stage_budget"]["decision"], "block")
        self.assertEqual(payload["stage_budget"]["reason"], "STAGE_NOT_ACTIVE")


if __name__ == "__main__":
    unittest.main()
