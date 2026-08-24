#!/usr/bin/env python3
"""Controlled bridge from trusted acceptance receipts to provider capabilities."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from acceptance_trust import (
    TerminalAuthorization,
    TrustPolicy,
)
from provider_lifecycle import validate_receipt


def authorize_provider_transition(
    receipt: dict[str, Any],
    *,
    work_item_id: str,
    provider: str,
    status: str,
    expected_ref: str,
    expected_commit: str,
    repo: Path,
    allowed_signers: Path,
    acceptance_policy: Path,
    policy_allowed_signers: Path,
    repo_id: str,
    trust_policy: TrustPolicy,
) -> TerminalAuthorization:
    """Issue an in-process capability only after supervisor-pinned validation."""
    if provider != "feishu":
        raise ValueError("ACCEPTANCE_PROVIDER_TERMINAL_UNSUPPORTED")
    valid, reason = validate_receipt(
        receipt,
        work_item_id=work_item_id,
        expected_ref=expected_ref,
        expected_commit=expected_commit,
        configured_provider=provider,
        repo=repo,
        allowed_signers=allowed_signers,
        acceptance_policy=acceptance_policy,
        policy_allowed_signers=policy_allowed_signers,
        repo_id=repo_id,
        trust_policy=trust_policy,
    )
    if not valid:
        raise ValueError(reason)
    return TerminalAuthorization(receipt)
