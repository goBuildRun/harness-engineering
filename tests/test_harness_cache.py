#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_cache import executed_check, reuse_check  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
