#!/usr/bin/env python3
"""CLI adapter for the one-shot Provider attempt runtime."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable

from ael_execution_authority import trust_from_installation
from ael_output import dump_json


def run_cli(attempt_for_task: Callable[..., dict[str, Any]]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-root", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--preflight", required=True)
    parser.add_argument("--expected-subject", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--evidence-ref", required=True)
    parser.add_argument("--sandbox-authority-receipt", required=True)
    parser.add_argument("--provider-authority-receipt", required=True)
    parser.add_argument("--authority-binding-output", default="")
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    product = Path(args.product_root).resolve()
    harness = Path(__file__).resolve().parents[2]
    try:
        sandbox_trust = trust_from_installation(
            harness, "network-sandbox", "harness-network-sandbox",
        )
        provider_trust = trust_from_installation(
            harness, "provider-response", "harness-provider-response",
        )
        outcome = attempt_for_task(
            product, args.task_id, preflight_path=Path(args.preflight),
            expected_subject=args.expected_subject, provider=args.provider,
            adapter_path=Path(args.adapter), evidence_ref=args.evidence_ref,
            command=command, timeout_seconds=args.timeout_seconds,
            sandbox_authority_path=Path(args.sandbox_authority_receipt),
            provider_authority_path=Path(args.provider_authority_receipt),
            authority_binding_path=(
                Path(args.authority_binding_output)
                if args.authority_binding_output else None
            ),
            sandbox_trust=sandbox_trust,
            provider_trust=provider_trust,
            authority_required=True,
        )
    except ValueError as exc:
        outcome = {"decision": "block", "reason": str(exc)}
    dump_json(outcome)
    return 0 if outcome.get("decision") == "pass" else 1
