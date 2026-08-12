#!/usr/bin/env python3
"""Safe reuse for deterministic mechanical Harness checks only."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any


CACHEABLE_GATES = {"tier", "scope", "structure", "plan_sync", "task_contract", "dag_sync"}


def tool_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=str):
        digest.update(str(path).encode("utf-8"))
        digest.update(path.read_bytes() if path.is_file() else b"absent")
    return digest.hexdigest()


def reuse_check(cached: dict[str, Any] | None, *, gate: str,
                fingerprint: str, subject_digest: str,
                policy_digest: str) -> dict[str, Any] | None:
    if gate not in CACHEABLE_GATES or not isinstance(cached, dict):
        return None
    if (
        cached.get("fingerprint") != fingerprint
        or cached.get("subject_digest") != subject_digest
        or cached.get("policy_digest") != policy_digest
        or cached.get("decision") not in {"pass", "block"}
        or cached.get("stale")
    ):
        return None
    reused = deepcopy(cached)
    reused["source"] = "cache"
    reused["cached_from"] = cached.get("completed_at", "")
    reused.pop("stale", None)
    return reused


def executed_check(*, decision: str, fingerprint: str, subject_digest: str,
                   policy_digest: str, completed_at: str, **extra: Any) -> dict[str, Any]:
    return {
        "decision": decision, "fingerprint": fingerprint,
        "subject_digest": subject_digest, "policy_digest": policy_digest,
        "source": "executed", "completed_at": completed_at, **extra,
    }
