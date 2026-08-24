#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from capability_contract import CAPABILITIES, audit  # noqa: E402


class CapabilityContractTest(unittest.TestCase):
    def test_repository_satisfies_all_retained_capabilities(self) -> None:
        result = audit(ROOT)
        self.assertEqual(result["decision"], "pass", result["findings"])
        self.assertEqual(set(result["capabilities"]), set(CAPABILITIES))

    def test_missing_carrier_or_tier_gate_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(audit(Path(tmp))["reason"], "CAPABILITY_CONTRACT_BROKEN")
        degraded = dict(__import__("ael_gates").TIER_GATES)
        degraded["strict"] = tuple(gate for gate in degraded["strict"] if gate != "qa_evidence")
        with mock.patch("capability_contract.TIER_GATES", degraded):
            result = audit(ROOT)
        self.assertIn("independent-qa:gate:strict:qa_evidence", result["findings"])


if __name__ == "__main__":
    unittest.main()
