#!/usr/bin/env python3
"""Mechanical contract proving retained capabilities still have runtime carriers."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ael_gates import TIER_GATES


CAPABILITIES: dict[str, dict[str, Any]] = {
    "openai-harness": {"mode": "embedded", "gates": {"lite": ["harness", "structure"]},
                       "files": [".ael/scripts/validate_ael.sh", ".ael/scripts/doc-gardening.sh"]},
    "bmad-planning": {"mode": "tier-triggered", "gates": {"standard": ["planning"]},
                      "files": [".ael/scripts/bmad_method_gate.py"]},
    "superpowers-tdd": {"mode": "embedded", "gates": {"lite": ["quality_lint", "quality_test"]},
                        "files": [".ael/scripts/quality_commands.py"]},
    "flow-x-knowledge": {"mode": "tier-triggered", "gates": {"standard": ["knowledge"]},
                         "files": [".ael/scripts/ael_knowledge.py", ".ael/scripts/ael_migration.py"]},
    "browser-qa": {"mode": "signal-triggered", "gates": {},
                   "files": [".ael/scripts/browser_qa.py", "tests/test_ael_gates.py",
                             "tests/test_browser_scenarios.py"]},
    "independent-qa": {"mode": "tier-triggered", "gates": {"standard": ["qa_evidence"]},
                       "files": [".ael/scripts/qa_evidence_check.py", ".ael/scripts/agent_review.py"]},
    "gc-sweeper": {"mode": "signal-triggered", "gates": {},
                   "files": [".ael/scripts/ael_gc_context.py",
                             ".ael/scripts/ael_gc_receipt.py",
                             "tests/test_ael_gc_context.py", "tests/test_ael_gc_receipt.py"]},
    "brownfield-intake": {"mode": "explicit", "gates": {},
                          "files": [".ael/scripts/ael_intake.py"]},
    "work-item-provider": {"mode": "lifecycle-triggered", "gates": {},
                           "files": [".ael/scripts/work_item.py",
                                     ".ael/scripts/provider_lifecycle.py",
                                     ".ael/scripts/acceptance_trust.py",
                                     ".ael/scripts/acceptance_authority.py",
                                     "tests/test_provider_lifecycle.py"]},
    "commit-acceptance": {"mode": "authority-triggered", "gates": {},
                          "files": [".ael/scripts/ael_attestation.py",
                                    ".ael/scripts/ael_receive.py",
                                    ".ael/scripts/ael_enforced.py",
                                    "tests/test_ael_enforced.py"]},
}

TIER_INHERITANCE = {"lite": ("lite", "standard", "strict"),
                    "standard": ("standard", "strict"), "strict": ("strict",)}


def audit(root: Path) -> dict[str, Any]:
    findings: list[str] = []
    evidence: dict[str, dict[str, Any]] = {}
    for name, contract in CAPABILITIES.items():
        missing_files = [path for path in contract["files"] if not (root / path).is_file()]
        missing_gates = []
        for floor, gates in contract["gates"].items():
            for tier in TIER_INHERITANCE[floor]:
                missing_gates.extend(f"{tier}:{gate}" for gate in gates if gate not in TIER_GATES[tier])
        if missing_files:
            findings.extend(f"{name}:file:{path}" for path in missing_files)
        if missing_gates:
            findings.extend(f"{name}:gate:{gate}" for gate in missing_gates)
        evidence[name] = {
            "mode": contract["mode"], "files": contract["files"],
            "gates": contract["gates"], "decision": "block" if missing_files or missing_gates else "pass",
        }
    return {
        "decision": "block" if findings else "pass",
        "reason": "CAPABILITY_CONTRACT_BROKEN" if findings else "CAPABILITY_CONTRACT_OK",
        "capabilities": evidence, "findings": findings,
    }


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
    print(json.dumps(audit(root), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
