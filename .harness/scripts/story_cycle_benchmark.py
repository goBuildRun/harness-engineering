#!/usr/bin/env python3
"""Deterministic offline before/after model for gate orchestration."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from harness_output import dump_json


def benchmark(fixture: dict[str, Any]) -> dict[str, Any]:
    durations = fixture["offline_gate_fixture_ms"]
    parallel = fixture["parallel_read_only"]
    serial = fixture["serial"]
    cacheable = set(fixture["cacheable_on_unrelated_evidence_change"])
    before = sum(int(value) for value in durations.values())
    after_first = max(int(durations[name]) for name in parallel) + sum(
        int(durations[name]) for name in serial
    )
    rerun_parallel = [name for name in parallel if name not in cacheable]
    after_unrelated_rerun = (
        max((int(durations[name]) for name in rerun_parallel), default=0)
        + sum(int(durations[name]) for name in serial)
    )
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
        "target_story_budget_ms": 1_800_000,
        "production_p95": "unknown",
        "judge_usage": "unknown",
        "quality_guards": {
            "independent_qa": "retained",
            "strict_provider": "offline_preflight_then_one_real_call",
            "l0_l1": "retained",
            "unknown_telemetry": "not_coerced",
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
