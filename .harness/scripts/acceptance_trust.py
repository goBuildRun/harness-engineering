#!/usr/bin/env python3
"""Supervisor-pinned trust anchors for terminal acceptance validation."""
from __future__ import annotations

import base64
import hashlib
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

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
        "harness_execution_started",
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
def requires_terminal_authority(status: str) -> bool:
    return status.strip().lower() not in LOCAL_NONTERMINAL_STATUSES


@dataclass(frozen=True)
class TrustPolicy:
    """Trust anchors and closure state supplied by the acceptance supervisor."""

    acceptance_policy_id: str
    acceptance_policy_digest: str
    receipt_signer_fingerprint: str
    policy_signer_fingerprint: str
    completed_work_items: tuple[str, ...] = ()

    def validate(self) -> None:
        if (
            not self.acceptance_policy_id
            or not SHA256_DIGEST.fullmatch(self.acceptance_policy_digest)
            or not SSH_FINGERPRINT.fullmatch(self.receipt_signer_fingerprint)
            or not SSH_FINGERPRINT.fullmatch(self.policy_signer_fingerprint)
            or any(not item for item in self.completed_work_items)
            or len(self.completed_work_items) != len(set(self.completed_work_items))
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
