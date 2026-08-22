#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from provider_attempt import run_once, validate_receipt  # noqa: E402
from provider_verifier_preflight import verify  # noqa: E402


class ProviderAttemptTest(unittest.TestCase):
    def preflight(self):
        return verify({
            "schema": "harness-provider-call-contract-v1", "subject_digest": "subject",
            "required_calls": ["provider.accept"], "allowed_calls": ["provider.accept"],
        }, {
            "schema": "harness-provider-offline-trace-v1", "subject_digest": "subject",
            "offline": True, "calls": ["provider.accept"],
        }, "subject")

    def test_one_shot_attempt_binds_preflight_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state, evidence = root / "attempt.json", root / "evidence.json"
            preflight = self.preflight()

            def runner(_command, **_kwargs):
                evidence.write_text(json.dumps({"decision": "pass"}))
                return SimpleNamespace(returncode=0)

            first = run_once(
                state, preflight, "subject", "mock", evidence,
                "evidence.json", ["mock-provider"], 1, runner,
            )
            second = run_once(
                state, preflight, "subject", "mock", evidence,
                "evidence.json", ["mock-provider"], 1, runner,
            )
            self.assertEqual(first["decision"], "pass")
            self.assertEqual(
                validate_receipt(first, preflight, "subject", evidence)["decision"], "pass",
            )
            self.assertEqual(second["reason"], "PROVIDER_ATTEMPT_LIMIT_EXCEEDED")

    def test_failure_consumes_attempt_and_forgery_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = self.preflight()
            failed = run_once(
                root / "attempt.json", preflight, "subject", "mock", root / "missing.json",
                "missing.json", ["mock-provider"], 1,
                lambda _command, **_kwargs: SimpleNamespace(returncode=1),
            )
            failed["decision"] = "pass"
        self.assertEqual(validate_receipt(failed, preflight, "subject")["decision"], "block")

    def test_stale_evidence_and_post_attempt_tampering_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = self.preflight()
            stale = root / "stale.json"
            stale.write_text('{"decision":"pass"}')
            stale_attempt = run_once(
                root / "stale-attempt.json", preflight, "subject", "mock", stale,
                "stale.json", ["mock-provider"], 1,
                lambda _command, **_kwargs: SimpleNamespace(returncode=0),
            )

            fresh = root / "fresh.json"

            def runner(_command, **_kwargs):
                fresh.write_text('{"decision":"pass","run":1}')
                return SimpleNamespace(returncode=0)

            receipt = run_once(
                root / "fresh-attempt.json", preflight, "subject", "mock", fresh,
                "fresh.json", ["mock-provider"], 1, runner,
            )
            fresh.write_text('{"decision":"pass","run":2}')
            tampered = validate_receipt(receipt, preflight, "subject", fresh)
        self.assertEqual(stale_attempt["reason"], "PROVIDER_EVIDENCE_STALE")
        self.assertEqual(tampered["decision"], "block")


if __name__ == "__main__":
    unittest.main()
