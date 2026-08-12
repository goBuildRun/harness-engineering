#!/usr/bin/env python3
"""State transition helpers for task-level Harness results."""
from __future__ import annotations

from typing import Any


def invalidate_if_stale(result: dict[str, Any], subject_digest: str,
                        policy_digest: str) -> bool:
    if result.get("state") == "active" and "INPUT_CHANGED" in result.get("blockers", []):
        return False
    previous_subject = str((result.get("subject") or {}).get("digest") or "")
    previous_policy = str(result.get("policy_digest") or "")
    stale = bool(
        (previous_subject and previous_subject != subject_digest)
        or (previous_policy and previous_policy != policy_digest)
    )
    if not stale:
        return False
    result["state"] = "active"
    result["decision"] = "block"
    result["blockers"] = sorted(set(result.get("blockers", [])) | {"INPUT_CHANGED"})
    result["invariants"]["risk_validation"] = "pending"
    result["invariants"]["final_result"] = "pending"
    for check in result.get("checks", {}).values():
        check["stale"] = True
    cost = result["cost"]["harness"]
    cost["reruns"] = int(cost.get("reruns") or 0) + 1
    return True
