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
FIXTURES = ROOT / "tests" / "fixtures"
ADAPTER = FIXTURES / "provider_mock_adapter.py"
sys.path.insert(0, str(SCRIPTS))

from provider_verifier_preflight import (  # noqa: E402
    argv_digest,
    execute_preflight,
    validate_receipt,
    verify,
)


class ProviderVerifierPreflightTest(unittest.TestCase):
    def contract(self):
        return json.loads((FIXTURES / "provider-offline-contract.json").read_text())

    def command(self):
        return [
            sys.executable, str(ADAPTER),
            "--subject", "subject", "--provider", "mock",
        ]

    def preflight(self):
        return execute_preflight(
            self.contract(), "subject", "mock", ADAPTER, self.command(),
        )

    def trace(self):
        return {
            "schema": "harness-provider-offline-trace-v2",
            "subject_digest": "subject",
            "provider": "mock",
            "offline": True,
            "network_calls": 0,
            "calls": ["provider.accept"],
        }

    def test_executes_offline_adapter_and_binds_truth_inputs(self) -> None:
        result = self.preflight()
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(result["provider"], "mock")
        self.assertEqual(result["network_calls"], 0)
        self.assertEqual(result["judge_usage"], "unknown")
        self.assertEqual(result["canonical_argv_digest"], argv_digest(self.command()))
        self.assertEqual(validate_receipt(result, "subject", "mock")["decision"], "pass")

    def test_forged_provider_or_digest_fails_binding(self) -> None:
        receipt = self.preflight()
        receipt["contract_digest"] = "a" * 64
        self.assertEqual(
            validate_receipt(receipt, "subject", "mock")["reason"],
            "PROVIDER_PREFLIGHT_RECEIPT_DIGEST_INVALID",
        )
        self.assertEqual(
            validate_receipt(self.preflight(), "subject", "other")["decision"], "block",
        )

    def test_adapter_must_be_part_of_canonical_argv(self) -> None:
        result = execute_preflight(
            self.contract(), "subject", "mock", ADAPTER,
            [sys.executable, "-c", "print('{}')"],
        )
        self.assertEqual(result["reason"], "PROVIDER_ADAPTER_ARGV_MISMATCH")

    def test_adapter_as_unused_argument_does_not_count_as_execution(self) -> None:
        def runner(_command, **_kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.trace()))

        result = execute_preflight(
            self.contract(), "subject", "mock", ADAPTER,
            [sys.executable, "-c", "print('ignored')", str(ADAPTER)], runner=runner,
        )
        self.assertEqual(result["reason"], "PROVIDER_ADAPTER_ARGV_MISMATCH")

    def test_adapter_change_during_offline_trace_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "mock_adapter.py"
            adapter.write_bytes(ADAPTER.read_bytes())
            command = [
                sys.executable, str(adapter),
                "--subject", "subject", "--provider", "mock",
            ]

            def runner(_command, **_kwargs):
                adapter.write_text(adapter.read_text() + "\n# changed\n")
                return SimpleNamespace(returncode=0, stdout=json.dumps(self.trace()))

            result = execute_preflight(
                self.contract(), "subject", "mock", adapter, command, runner=runner,
            )
        self.assertEqual(result["reason"], "PROVIDER_ADAPTER_CHANGED")

    def test_contract_and_executed_trace_fail_closed(self) -> None:
        contract = self.contract()
        contract["allowed_calls"] = []
        result = verify(
            contract, self.trace(), "subject", provider="mock",
            adapter_digest="a" * 64, canonical_argv_digest="b" * 64,
        )
        self.assertEqual(result["reason"], "PROVIDER_REQUIRED_NOT_ALLOWED")

        trace = self.trace()
        trace["network_calls"] = 1
        result = verify(
            self.contract(), trace, "subject", provider="mock",
            adapter_digest="a" * 64, canonical_argv_digest="b" * 64,
        )
        self.assertEqual(result["reason"], "PROVIDER_TRACE_NETWORK_ACTIVITY")


if __name__ == "__main__":
    unittest.main()
