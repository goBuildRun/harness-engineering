#!/usr/bin/env python3
"""Deterministic offline before/after model for gate orchestration."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import time
from pathlib import Path
from typing import Any

from harness_gate_execution import build_spec, execute_specs
from harness_output import dump_json


def benchmark(fixture: dict[str, Any]) -> dict[str, Any]:
    durations = fixture["offline_gate_fixture_ms"]
    parallel = set(fixture["parallel_read_only"])
    cacheable = set(fixture["cacheable_on_unrelated_evidence_change"])

    def specs(version: str):
        return [
            replace(
                build_spec(
                    name, ["offline-fixture", name],
                    input_digest=(f"stable:{name}" if name in cacheable else f"{version}:{name}"),
                    tier="strict", configured_timeout=120, remaining_budget_ms=10_000,
                ),
                parallel_safe=name in parallel,
            )
            for name in durations
        ]

    def runner(_command: list[str], name: str, _timeout: float):
        time.sleep(int(durations[name]) / 1000)
        return {"decision": "pass", "reason": "OFFLINE_GATE_PASS"}, 0

    def run(current_specs, previous=None, subject="candidate-v1"):
        started = time.monotonic()
        checks, hits = execute_specs(
            current_specs, runner=runner, previous_checks=previous or {},
            subject_digest=subject, policy_digest="offline-policy",
            remaining_budget_ms=10_000,
        )
        elapsed = int((time.monotonic() - started) * 1000)
        if any(check.get("decision") != "pass" for check in checks.values()):
            raise ValueError("offline gate failed")
        return elapsed, checks, hits

    first_specs = specs("candidate-v1")
    before, _serial_checks, _serial_hits = run([
        replace(spec, parallel_safe=False, cacheable=False) for spec in first_specs
    ])
    after_first, first_checks, _first_hits = run(first_specs)
    after_unrelated_rerun, _rerun_checks, rerun_hits = run(
        specs("candidate-v2"), first_checks, subject="candidate-v2",
    )
    stage_fixture = fixture.get("offline_story_stage_fixture_ms") or {
        "takeover": 30,
        "planning": 60,
        "implementation_test": 120,
        "independent_qa": 80,
        "deploy_provider": 70,
        "finalize": 40,
    }
    qa_units = fixture.get("offline_qa_unit_fixture_ms") or [20, 20, 20, 20, 20]
    if not isinstance(qa_units, list) or not qa_units:
        raise ValueError("offline_qa_unit_fixture_ms invalid")
    before_stages = {name: int(value) for name, value in stage_fixture.items()}
    after_stages = dict(before_stages)
    after_stages["independent_qa"] = max(int(value) for value in qa_units)
    before_full = sum(before_stages.values()) + before
    after_full = sum(after_stages.values()) + after_first
    full_budget = 1_800_000
    return {
        "decision": "pass",
        "reason": "OFFLINE_STORY_CYCLE_BENCHMARK_OK",
        "historical_story_wall_ms": fixture["audit_source"]["wall_ms"],
        "historical_gate_duration_ms": fixture["audit_source"]["gate_duration_ms"],
        "offline_before_serial_ms": before,
        "offline_after_parallel_ms": after_first,
        "offline_after_unrelated_rerun_ms": after_unrelated_rerun,
        "first_run_reduction": (before - after_first) / before,
        "unrelated_rerun_reduction": (before - after_unrelated_rerun) / before,
        "observed_cache_hits": rerun_hits,
        "offline_before_full_cycle_ms": before_full,
        "offline_after_full_cycle_ms": after_full,
        "offline_stage_wall_ms": {
            "before_serial": before_stages,
            "after_bounded": after_stages,
        },
        "offline_qa_units": len(qa_units),
        "offline_reruns": {"before": int(fixture["audit_source"].get("reruns") or 0), "after": 0},
        "retries": {"before": int(fixture["audit_source"].get("reruns") or 0), "after": 0},
        "agent_active_ms": "unknown",
        "telemetry": "unknown",
        "full_cycle_budget_ms": full_budget,
        "full_cycle_budget_pass": after_full <= full_budget,
        "orchestration_executor": "execute_specs",
        "target_story_budget_ms": 1_800_000,
        "production_p95": "unknown",
        "judge_usage": "unknown",
        "quality_guards": {
            "independent_qa": "retained",
            "qa_fanout": "bounded_parallel_stable_aggregation",
            "strict_provider": "offline_preflight_then_one_real_call",
            "l0_l1": "retained",
            "unknown_telemetry": "not_coerced",
            "provider_calls": 0,
            "production_deployments": 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    args = parser.parse_args()
    try:
        fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        if fixture.get("schema") != "harness-story-efficiency-fixture-v1":
            raise ValueError
        result = benchmark(fixture)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        result = {"decision": "block", "reason": "OFFLINE_STORY_CYCLE_FIXTURE_INVALID"}
    dump_json(result)
    return 0 if result["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
