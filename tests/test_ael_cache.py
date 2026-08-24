#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ael_cache import executed_check, reuse_check, tool_digest  # noqa: E402


class HarnessCacheTest(unittest.TestCase):
    def test_deterministic_gate_reuses_exact_fingerprint(self) -> None:
        cached = executed_check(
            decision="pass", fingerprint="fp", subject_digest="subject",
            policy_digest="policy", completed_at="2026-08-11T00:00:00Z",
        )
        reused = reuse_check(
            cached, gate="scope", fingerprint="fp", subject_digest="subject",
            policy_digest="policy",
        )
        self.assertEqual(reused["source"], "cache")
        self.assertEqual(reused["cached_from"], "2026-08-11T00:00:00Z")

    def test_agent_human_and_production_gates_never_reuse(self) -> None:
        cached = executed_check(
            decision="pass", fingerprint="fp", subject_digest="subject",
            policy_digest="policy", completed_at="now",
        )
        for gate in ("qa", "gc_agent", "production", "deployment", "rollback"):
            self.assertIsNone(reuse_check(
                cached, gate=gate, fingerprint="fp", subject_digest="subject",
                policy_digest="policy",
            ))

    def test_changed_subject_policy_or_tool_fingerprint_misses(self) -> None:
        cached = executed_check(
            decision="pass", fingerprint="fp-a", subject_digest="subject-a",
            policy_digest="policy-a", completed_at="now",
        )
        cases = (
            ("fp-b", "subject-a", "policy-a"),
            ("fp-a", "subject-b", "policy-a"),
            ("fp-a", "subject-a", "policy-b"),
        )
        for fingerprint, subject, policy in cases:
            self.assertIsNone(reuse_check(
                cached, gate="tier", fingerprint=fingerprint,
                subject_digest=subject, policy_digest=policy,
            ))

    def test_stale_check_never_reuses(self) -> None:
        cached = executed_check(
            decision="pass", fingerprint="fp", subject_digest="subject",
            policy_digest="policy", completed_at="now", stale=True,
        )
        self.assertIsNone(reuse_check(
            cached, gate="scope", fingerprint="fp", subject_digest="subject",
            policy_digest="policy",
        ))

    def test_tier_module_change_invalidates_runtime_and_scope_tool_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp)
            runtime = scripts / "ael_runtime.py"
            scope = scripts / "ael_scope.py"
            tier = scripts / "ael_tier.py"
            runtime.write_text("runtime = 1\n", encoding="utf-8")
            scope.write_text("scope = 1\n", encoding="utf-8")
            tier.write_text("tier = 1\n", encoding="utf-8")
            before = tool_digest([runtime, scope])
            tier.write_text("tier = 2\n", encoding="utf-8")
            after = tool_digest([runtime, scope])
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
