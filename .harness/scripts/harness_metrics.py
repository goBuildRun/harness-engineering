#!/usr/bin/env python3
"""Read-only rollout metrics derived from canonical task results."""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from harness_output import dump_json


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def summarize(results_root: Path, baseline_path: Path) -> dict[str, Any]:
    samples = []
    for path in sorted(results_root.glob("*/result.json")) if results_root.is_dir() else []:
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        harness = (result.get("cost") or {}).get("harness") or {}
        samples.append({
            "tier": (result.get("tier") or {}).get("effective"),
            "initial_tier": (result.get("tier") or {}).get("initial"),
            "decision": result.get("decision"),
            "context_chars": _number(harness.get("context_chars")),
            "gate_duration_ms": _number(harness.get("gate_duration_ms")),
            "reruns": _number(harness.get("reruns")),
            "checks": len(result.get("checks") or {}),
        })
    lite = [sample for sample in samples if sample["tier"] == "lite"]
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        baseline = {}
    baseline_harness = baseline.get("harness") or {}

    def median(field: str) -> float | None:
        values = [sample[field] for sample in lite if sample[field] is not None]
        return statistics.median(values) if values else None

    current = {
        "lite_samples": len(lite),
        "median_context_chars": median("context_chars"),
        "median_gate_duration_ms": median("gate_duration_ms"),
        "median_checks": median("checks"),
        "tier_downgrades": sum(
            1 for sample in samples
            if sample["initial_tier"] in {"standard", "strict"} and sample["tier"] == "lite"
        ),
        "block_rate": (sum(sample["decision"] == "block" for sample in samples) / len(samples)
                       if samples else None),
    }
    comparisons = {}
    for name, current_field, baseline_field in (
        ("context_reduction", "median_context_chars", "context_chars"),
        ("duration_reduction", "median_gate_duration_ms", "gate_duration_ms"),
        ("evidence_reduction", "median_checks", "default_evidence_types"),
    ):
        old = _number(baseline_harness.get(baseline_field))
        new = current[current_field]
        comparisons[name] = ((old - new) / old if old and new is not None else None)
    sufficient = len(lite) >= 5 and all(value is not None for value in comparisons.values())
    passed = bool(
        sufficient and comparisons["context_reduction"] >= 0.40
        and comparisons["duration_reduction"] >= 0.30
        and current["tier_downgrades"] == 0
        and current["block_rate"] is not None and current["block_rate"] <= 0.05
    )
    return {
        "decision": "pass" if passed else "insufficient_data" if not sufficient else "block",
        "reason": "ROLLOUT_METRICS_PASS" if passed else (
            "ROLLOUT_METRICS_INSUFFICIENT_DATA" if not sufficient else "ROLLOUT_METRICS_THRESHOLD_BLOCK"
        ),
        "samples": len(samples), "current": current, "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Harness rollout metrics")
    parser.add_argument("--results-root", required=True)
    parser.add_argument("--baseline", required=True)
    args = parser.parse_args()
    dump_json(summarize(Path(args.results_root), Path(args.baseline)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
