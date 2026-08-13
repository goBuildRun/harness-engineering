#!/usr/bin/env python3
"""Mechanical contract proving retained capabilities still have runtime carriers."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from harness_gates import TIER_GATES


CAPABILITIES: dict[str, dict[str, Any]] = {
    "openai-harness": {"mode": "embedded", "gates": {"lite": ["harness", "structure"]},
                       "files": [".harness/scripts/validate_harness.sh", ".harness/scripts/doc-gardening.sh"]},
    "bmad-planning": {"mode": "tier-triggered", "gates": {"standard": ["planning"]},
                      "files": [".harness/scripts/bmad_method_gate.py"]},
    "superpowers-tdd": {"mode": "embedded", "gates": {"lite": ["quality_lint", "quality_test"]},
                        "files": [".harness/scripts/quality_commands.py"]},
    "flow-x-knowledge": {"mode": "tier-triggered", "gates": {"standard": ["knowledge"]},
                         "files": [".harness/scripts/harness_knowledge.py", ".harness/scripts/harness_migration.py"]},
    "browser-qa": {"mode": "signal-triggered", "gates": {},
                   "files": [".harness/scripts/browser_qa.py", "tests/test_harness_gates.py",
                             "tests/test_browser_scenarios.py"]},
    "independent-qa": {"mode": "tier-triggered", "gates": {"standard": ["qa_evidence"]},
                       "files": [".harness/scripts/qa_evidence_check.py", ".harness/scripts/agent_review.py"]},
    "gc-sweeper": {"mode": "signal-triggered", "gates": {},
                   "files": [".harness/scripts/harness_gc_context.py", ".harness/scripts/ci_gc_review.py",
                             "tests/test_harness_gc_context.py", "tests/test_ci_gc_review.py"]},
    "brownfield-intake": {"mode": "explicit", "gates": {},
                          "files": [".harness/scripts/harness_intake.py"]},
    "work-item-provider": {"mode": "lifecycle-triggered", "gates": {},
                           "files": [".harness/scripts/work_item.py", ".harness/scripts/provider_lifecycle.py",
                                     "tests/test_provider_lifecycle.py"]},
    "commit-acceptance": {"mode": "authority-triggered", "gates": {},
                          "files": [".harness/scripts/harness_attestation.py",
                                    "tests/test_harness_attestation.py"]},
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
