#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from provider_verifier_preflight import validate_receipt, verify  # noqa: E402


class ProviderVerifierPreflightTest(unittest.TestCase):
    def contract(self):
        return {
            "schema": "harness-provider-call-contract-v1",
            "subject_digest": "subject",
            "required_calls": ["route.resolve", "answer.generate"],
            "allowed_calls": ["route.resolve", "answer.generate", "evidence.store"],
        }

    def trace(self):
        return {
            "schema": "harness-provider-offline-trace-v1",
            "subject_digest": "subject", "offline": True,
            "calls": ["route.resolve", "answer.generate"],
        }

    def test_offline_contract_passes_without_parameters_or_network(self) -> None:
        result = verify(self.contract(), self.trace(), "subject")
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(result["network_calls"], 0)
        self.assertEqual(result["judge_usage"], "unknown")
        self.assertEqual(validate_receipt(result, "subject")["decision"], "pass")

    def test_forged_or_tampered_receipt_fails_binding(self) -> None:
        receipt = verify(self.contract(), self.trace(), "subject")
        receipt["contract_digest"] = "a" * 64
        self.assertEqual(
            validate_receipt(receipt, "subject")["reason"],
            "PROVIDER_PREFLIGHT_RECEIPT_DIGEST_INVALID",
        )

    def test_required_not_allowed_is_detected_before_provider(self) -> None:
        contract = self.contract()
        contract["allowed_calls"] = ["answer.generate"]
        result = verify(contract, self.trace(), "subject")
        self.assertEqual(result["reason"], "PROVIDER_REQUIRED_NOT_ALLOWED")

    def test_missing_unexpected_or_repeated_calls_fail_closed(self) -> None:
        for calls, reason in (
            (["route.resolve"], "PROVIDER_REQUIRED_CALL_MISSING"),
            (["route.resolve", "answer.generate", "unknown.call"], "PROVIDER_UNEXPECTED_CALL"),
            (["route.resolve", "answer.generate", "answer.generate"], "PROVIDER_CALL_REPEATED"),
        ):
            trace = self.trace()
            trace["calls"] = calls
            self.assertEqual(verify(self.contract(), trace, "subject")["reason"], reason)


if __name__ == "__main__":
    unittest.main()
