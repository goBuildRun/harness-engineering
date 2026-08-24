#!/usr/bin/env python3
"""Structured production-evidence policy shared by Planning and release gates."""
from __future__ import annotations

from typing import Any, Callable


def from_spec_metadata(
    metadata: dict[str, Any],
    *,
    string_value: Callable[[Any, str], str],
) -> dict[str, str]:
    raw = metadata.get("production_evidence")
    if raw is not None and not isinstance(raw, dict):
        raise ValueError(
            "PRODUCTION_EVIDENCE_METADATA_INVALID: expected mapping, "
            f"got {type(raw).__name__}"
        )
    provider_mode = string_value(
        (raw or {}).get("provider_mode"),
        "PRODUCTION_EVIDENCE_PROVIDER_MODE",
    )
    if provider_mode and provider_mode not in {"synthetic_allowed", "real_required"}:
        raise ValueError(
            "PRODUCTION_EVIDENCE_PROVIDER_MODE_INVALID: "
            f"{provider_mode}; expected synthetic_allowed/real_required"
        )
    return {"provider_mode": provider_mode} if provider_mode else {}
