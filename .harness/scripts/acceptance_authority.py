#!/usr/bin/env python3
"""Controlled bridge from trusted acceptance receipts to provider capabilities."""
from __future__ import annotations

import hashlib
import weakref
from pathlib import Path
from typing import Any

from acceptance_trust import SHA256_DIGEST, TrustPolicy, requires_terminal_authority
from provider_lifecycle import canonical_receipt, validate_receipt


def _build_authorization_api() -> tuple[type, Any, Any]:
    issued: weakref.WeakSet[Any] = weakref.WeakSet()

    class TerminalAuthorization:
        """Opaque, receipt-validated capability owned by the authority service."""

        __slots__ = (
            "work_item_id",
            "provider",
            "status",
            "receipt_digest",
            "__weakref__",
        )

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise ValueError("TERMINAL_AUTHORIZATION_ISSUER_INVALID")

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
        if not requires_terminal_authority(status):
            raise ValueError("ACCEPTANCE_TERMINAL_STATUS_REQUIRED")
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
        digest = hashlib.sha256(canonical_receipt(receipt).encode("utf-8")).hexdigest()
        authorization = object.__new__(TerminalAuthorization)
        authorization.work_item_id = work_item_id
        authorization.provider = provider
        authorization.status = status.strip().lower()
        authorization.receipt_digest = digest
        issued.add(authorization)
        return authorization

    def terminal_authorization_matches(
        authorization: TerminalAuthorization | None,
        *,
        work_item_id: str,
        provider: str,
        status: str,
    ) -> bool:
        return bool(
            type(authorization) is TerminalAuthorization
            and authorization in issued
            and authorization.work_item_id == work_item_id
            and authorization.provider == provider
            and authorization.status == status.strip().lower()
            and SHA256_DIGEST.fullmatch(authorization.receipt_digest)
        )

    return TerminalAuthorization, authorize_provider_transition, terminal_authorization_matches


TerminalAuthorization, authorize_provider_transition, terminal_authorization_matches = (
    _build_authorization_api()
)
