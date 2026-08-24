#!/usr/bin/env python3
"""Supervisor-pinned trust anchors for terminal acceptance validation."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHA256_DIGEST = re.compile(r"^[0-9a-f]{64}$")
SSH_FINGERPRINT = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
LOCAL_NONTERMINAL_STATUSES = frozenset(
    {
        "pending",
        "todo",
        "not_started",
        "planning_gate_ready",
        "in_progress",
        "progress",
        "ael_execution_started",
        "qa",
        "testing",
        "ready_to_release",
        "release_ready",
        "awaiting_release",
        "待处理",
        "开发中",
        "测试中",
        "待发布",
    }
)
class TerminalAuthorization:
    """Signed acceptance receipt presented again at the provider side-effect boundary."""

    __slots__ = ("receipt",)

    def __init__(self, receipt: dict) -> None:
        if not isinstance(receipt, dict):
            raise ValueError("TERMINAL_AUTHORIZATION_RECEIPT_INVALID")
        self.receipt = json.loads(json.dumps(receipt))


def requires_terminal_authority(status: str) -> bool:
    return status.strip().lower() not in LOCAL_NONTERMINAL_STATUSES


def terminal_authorization_matches(
    authorization: TerminalAuthorization | None,
    *,
    work_item_id: str,
    provider: str,
    status: str,
    allowed_signers: Path | None = None,
    signer_fingerprint: str = "",
) -> bool:
    if (
        not isinstance(authorization, TerminalAuthorization)
        or allowed_signers is None
        or not requires_terminal_authority(status)
    ):
        return False
    receipt = authorization.receipt
    try:
        expires_at = datetime.strptime(
            str(receipt.get("expires_at") or ""), "%Y-%m-%dT%H:%M:%SZ",
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    if (
        receipt.get("schema") != "harness-acceptance-receipt-v1"
        or receipt.get("authority") not in {"git-receive", "release-gate"}
        or receipt.get("work_item_id") != work_item_id
        or receipt.get("provider") != provider
        or expires_at <= datetime.now(timezone.utc)
        or not isinstance(receipt.get("signature"), str)
    ):
        return False
    message = json.dumps(
        {key: value for key, value in receipt.items() if key != "signature"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ) + "\n"
    try:
        verify_pinned_signature(
            message=message,
            signature_text=receipt["signature"],
            allowed_signers=allowed_signers,
            principal="harness",
            namespace="harness-acceptance",
            fingerprint=signer_fingerprint,
            missing_reason="TERMINAL_AUTHORIZATION_TRUST_MISSING",
            mismatch_reason="TERMINAL_AUTHORIZATION_SIGNER_MISMATCH",
            invalid_reason="TERMINAL_AUTHORIZATION_SIGNATURE_INVALID",
            tool_reason="TERMINAL_AUTHORIZATION_SIGNATURE_TOOL_MISSING",
        )
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class TrustPolicy:
    """Trust anchors and closure state supplied by the acceptance supervisor."""

    acceptance_policy_id: str
    acceptance_policy_digest: str
    receipt_signer_fingerprint: str
    policy_signer_fingerprint: str
    predecessor_receipts: tuple[dict[str, Any], ...] = ()

    def validate(self) -> None:
        if (
            not self.acceptance_policy_id
            or not SHA256_DIGEST.fullmatch(self.acceptance_policy_digest)
            or not SSH_FINGERPRINT.fullmatch(self.receipt_signer_fingerprint)
            or not SSH_FINGERPRINT.fullmatch(self.policy_signer_fingerprint)
            or any(not isinstance(receipt, dict) for receipt in self.predecessor_receipts)
        ):
            raise ValueError("ACCEPTANCE_TRUST_POLICY_INVALID")


def _signer_fingerprint(line: str) -> str | None:
    fields = line.split()
    key_index = next(
        (index for index, field in enumerate(fields) if field.startswith(("ssh-", "ecdsa-", "sk-"))),
        None,
    )
    if key_index is None or key_index + 1 >= len(fields):
        return None
    try:
        key_blob = base64.b64decode(fields[key_index + 1], validate=True)
    except (ValueError, TypeError):
        return None
    digest = base64.b64encode(hashlib.sha256(key_blob).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{digest}"


def _trusted_signer_lines(
    allowed_signers: Path,
    *,
    principal: str,
    fingerprint: str,
    missing_reason: str,
    mismatch_reason: str,
) -> list[str]:
    if not allowed_signers.is_file():
        raise ValueError(missing_reason)
    try:
        lines = [
            line.strip()
            for line in allowed_signers.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except OSError as exc:
        raise ValueError(missing_reason) from exc
    trusted = [
        line
        for line in lines
        if principal in line.split()[0].split(",") and _signer_fingerprint(line) == fingerprint
    ]
    if not trusted:
        raise ValueError(mismatch_reason)
    return trusted


def verify_pinned_signature(
    *,
    message: str,
    signature_text: str,
    allowed_signers: Path,
    principal: str,
    namespace: str,
    fingerprint: str,
    missing_reason: str,
    mismatch_reason: str,
    invalid_reason: str,
    tool_reason: str,
) -> None:
    trusted = _trusted_signer_lines(
        allowed_signers,
        principal=principal,
        fingerprint=fingerprint,
        missing_reason=missing_reason,
        mismatch_reason=mismatch_reason,
    )
    with tempfile.TemporaryDirectory() as tmp:
        signature = Path(tmp) / "message.sig"
        trusted_signers = Path(tmp) / "allowed_signers"
        signature.write_text(signature_text, encoding="utf-8")
        trusted_signers.write_text("\n".join(trusted) + "\n", encoding="utf-8")
        try:
            completed = subprocess.run(
                [
                    "ssh-keygen",
                    "-Y",
                    "verify",
                    "-f",
                    str(trusted_signers),
                    "-I",
                    principal,
                    "-n",
                    namespace,
                    "-s",
                    str(signature),
                ],
                input=message,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise ValueError(tool_reason) from exc
    if completed.returncode != 0:
        raise ValueError(invalid_reason)
