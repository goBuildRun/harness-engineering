#!/usr/bin/env python3
"""Compatibility facade for story-cycle commands and helpers."""
from __future__ import annotations

from pathlib import Path

from harness_assurance import refresh_result
from harness_attestation import verify_attestation
from harness_cycle_confirmation import cmd_confirm
from harness_cycle_release import (
    allow_finish_attempt, begin_finish_span, cmd_release_ready as _cmd_release_ready,
    complete_finish_span,
)
from harness_cycle_stage_commands import cmd_stage, load_candidate_snapshot
from harness_cycle_stages import (
    STAGE_ORDER, begin_stage, close_story_cycle, finish_readiness, finish_stage,
    start_story_cycle,
)
from harness_cycle_usage import cmd_usage_baseline
from harness_runtime import now


def refresh_assurance(result: dict, product: Path, policy_digest: str, *, phase: str) -> None:
    refresh_result(result, product, policy_digest, phase=phase, verified_at=now(),
                   attestation=verify_attestation(
                       product, commit="HEAD", policy_digest=policy_digest,
                       task_id=str(result.get("task_id") or ""),
                   ))


def cmd_release_ready(args) -> int:
    return _cmd_release_ready(args, refresh_assurance=refresh_assurance)


__all__ = [
    "STAGE_ORDER",
    "allow_finish_attempt",
    "begin_finish_span",
    "begin_stage",
    "close_story_cycle",
    "cmd_confirm",
    "cmd_release_ready",
    "cmd_stage",
    "cmd_usage_baseline",
    "complete_finish_span",
    "finish_readiness",
    "finish_stage",
    "load_candidate_snapshot",
    "refresh_assurance",
    "start_story_cycle",
]
