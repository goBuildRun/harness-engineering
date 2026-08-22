#!/usr/bin/env python3
"""Offline lifecycle capability discovery performed before Story execution."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from harness_output import dump_json
from harness_runtime import canonical_digest, now

try:
    import yaml
except ImportError:  # pragma: no cover - runtime dependency is already required elsewhere
    yaml = None  # type: ignore


SCHEMA = "harness-lifecycle-capability-v1"


def _config(product: Path) -> dict[str, Any]:
    path = product / "harness-workspace/project.yaml"
    if yaml is None or not path.is_file():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}
    return value.get("work_item") or {}


def discover(product: Path, provider_hint: str = "") -> dict[str, Any]:
    work_item = _config(product)
    provider = str(provider_hint or work_item.get("provider") or "noop").strip().lower()
    provider_config = (
        (work_item.get("providers") or {}).get(provider)
        or work_item.get(provider)
        or {}
    )
    mapping: dict[str, str] = {}
    readback = "unknown"
    reason = "LIFECYCLE_CAPABILITY_UNKNOWN"
    decision = "block"
    if provider == "noop":
        mapping = {"in_progress": "in_progress", "ready_to_release": "ready_to_release"}
        readback, decision, reason = "local", "pass", "LIFECYCLE_CAPABILITY_OK"
    elif provider == "feishu":
        mode = str(provider_config.get("status_update_mode") or "skip").strip().lower()
        mapping = {"in_progress": "open", "ready_to_release": "open"}
        if mode in {"completed", "complete", "completed_at", "completion"}:
            readback, decision, reason = "verified-open", "pass", "LIFECYCLE_CAPABILITY_OK"
        elif mode in {"description", "patch", "note"}:
            readback, reason = "unverified-description", "LIFECYCLE_READBACK_UNPROVEN"
        else:
            readback, reason = "none", "LIFECYCLE_STATUS_UPDATE_DISABLED"
    elif provider == "teambition":
        configured = provider_config.get("status_map") or provider_config.get("stage_map") or {}
        ready = str((configured or {}).get("ready_to_release") or "").strip()
        mapping = {"ready_to_release": ready or "unknown"}
        if ready:
            readback, decision, reason = "configured", "pass", "LIFECYCLE_CAPABILITY_OK"
        else:
            reason = "LIFECYCLE_READY_MAPPING_MISSING"
    elif provider == "jira":
        mapping = {"ready_to_release": "unknown"}
        reason = "LIFECYCLE_READY_TRANSITION_MISSING"
    payload = {
        "schema": SCHEMA,
        "decision": decision,
        "reason": reason,
        "provider": provider,
        "required_statuses": ["in_progress", "ready_to_release"],
        "status_mapping": mapping,
        "readback": readback,
        "network_calls": 0,
        "checked_at": now(),
    }
    payload["capability_digest"] = canonical_digest({
        key: value for key, value in payload.items() if key != "checked_at"
    })
    return payload


def preflight_resumed(
    outcome: dict[str, Any], product: Path, resolved_work_item: dict[str, str] | None,
) -> dict[str, Any]:
    if outcome.get("decision") == "block" or not isinstance(outcome.get("result"), dict):
        return outcome
    result = outcome["result"]
    work_item = result.get("work_item") or resolved_work_item or {}
    lifecycle = discover(product, str(work_item.get("provider") or ""))
    tier = result.get("tier") or {}
    effective = str(tier.get("effective") or tier.get("initial") or "standard")
    if effective == "strict" and lifecycle["decision"] == "block":
        return {
            "decision": "block", "reason": lifecycle["reason"],
            "lifecycle": lifecycle, "changed": False,
        }
    previous = result.get("lifecycle") or {}
    if previous.get("capability_digest") != lifecycle["capability_digest"]:
        result["lifecycle"] = lifecycle
        outcome["changed"] = True
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-root", required=True)
    parser.add_argument("--provider", default="")
    args = parser.parse_args()
    outcome = discover(Path(args.product_root).resolve(), args.provider)
    dump_json(outcome)
    return 0 if outcome["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
