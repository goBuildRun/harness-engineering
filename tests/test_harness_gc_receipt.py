#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_gc_receipt import build_receipt, store_receipt, verify_receipt  # noqa: E402


class HarnessGcReceiptTest(unittest.TestCase):
    def keys(self, root: Path) -> tuple[Path, Path]:
        key = root / "gc-authority"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        allowed = root / "allowed_signers"
        allowed.write_text(f"harness {key.with_suffix('.pub').read_text()}", encoding="utf-8")
        return key, allowed

    def test_signed_receipt_binds_context_and_single_agent_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            key, allowed = self.keys(root)
            context = {"task_id": "task-1", "actual_diff": "dead code", "scope": ["a.py"]}
            gc_result = {
                "decision": "pass", "role": "gc-sweeper", "independent": True,
                "task_id": "task-1", "subject_digest": "commit-a", "policy_digest": "policy-a",
                "findings": 1, "remediated": 1, "deferred_work_items": [],
                "mechanical_adjudication": [{
                    "trigger": "dead_code", "decision": "remediated",
                    "reason": "dead helper removed before receipt signing",
                }],
                "telemetry": {"agent_calls": 1, "context_chars": 42, "duration_ms": 12,
                              "provider": "compatible", "model": "gc-model"},
            }
            receipt = build_receipt(
                commit="commit-a", task_id="task-1", policy_digest="policy-a",
                context=context, triggers=["dead_code"], gc_result=gc_result, signing_key=key,
            )
            store_receipt(root, receipt)
            valid = verify_receipt(
                root, commit="commit-a", task_id="task-1", policy_digest="policy-a",
                context=context, triggers=["dead_code"], allowed_signers=allowed,
            )
            self.assertEqual(valid["decision"], "pass", valid)
            stale = verify_receipt(
                root, commit="commit-a", task_id="task-1", policy_digest="policy-a",
                context={**context, "actual_diff": "changed"}, triggers=["dead_code"],
                allowed_signers=allowed,
            )
            self.assertEqual(stale["reason"], "GC_RECEIPT_BINDING_MISMATCH")

            adjudications = gc_result.pop("mechanical_adjudication")
            with self.assertRaisesRegex(ValueError, "GC_RECEIPT_ADJUDICATION_INVALID"):
                build_receipt(
                    commit="commit-a", task_id="task-1", policy_digest="policy-a",
                    context=context, triggers=["dead_code"], gc_result=gc_result, signing_key=key,
                )
            gc_result["mechanical_adjudication"] = adjudications

            gc_result["telemetry"]["agent_calls"] = 2
            with self.assertRaisesRegex(ValueError, "GC_RECEIPT_RESULT_INVALID"):
                build_receipt(
                    commit="commit-a", task_id="task-1", policy_digest="policy-a",
                    context=context, triggers=["dead_code"], gc_result=gc_result, signing_key=key,
                )


if __name__ == "__main__":
    unittest.main()
