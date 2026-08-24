#!/usr/bin/env python3
"""Pinned signed receipts for trusted execution and readback authorities."""
from __future__ import annotations

import json
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acceptance_trust import SSH_FINGERPRINT, verify_pinned_signature
from ael_runtime import canonical_digest


SCHEMA = "harness-execution-authority-v1"
NAMESPACE = "harness-execution-authority"
BASE_FIELDS = {
    "schema", "decision", "reason", "authority", "action",
    "subject_digest", "provider", "input_digest", "output_digest",
    "claims", "issued_at", "expires_at",
}
FIELDS = BASE_FIELDS | {"receipt_digest", "signature"}
TRUST_POLICY_SCHEMA = "harness-authority-trust-v1"


@dataclass(frozen=True)
class AuthorityTrust:
    allowed_signers: Path
    signer_fingerprint: str
    principal: str

    def validate(self) -> None:
        if (
            not self.allowed_signers.is_file()
            or not SSH_FINGERPRINT.fullmatch(self.signer_fingerprint)
            or not (self.principal == "harness" or self.principal.startswith("harness-"))
        ):
            raise ValueError("EXECUTION_AUTHORITY_TRUST_INVALID")


def _installed_path(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise ValueError("EXECUTION_AUTHORITY_INSTALLATION_POLICY_INVALID")
    base = root.resolve()
    candidate = base / relative
    current = base
    for component in Path(relative).parts:
        if component in {"", ".", ".."}:
            raise ValueError("EXECUTION_AUTHORITY_INSTALLATION_POLICY_INVALID")
        current = current / component
        if current.is_symlink():
            raise ValueError("EXECUTION_AUTHORITY_INSTALLATION_PATH_UNTRUSTED")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to((base / ".ael").resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError("EXECUTION_AUTHORITY_INSTALLATION_PATH_UNTRUSTED") from exc
    return resolved


def trust_from_installation(
    ael_root: Path, authority: str, principal: str,
) -> AuthorityTrust:
    root = ael_root.resolve()
    policy_path = root / ".ael/authority-trust.json"
    if not policy_path.is_file() or policy_path.is_symlink():
        raise ValueError("EXECUTION_AUTHORITY_INSTALLATION_POLICY_MISSING")
    try:
        if stat.S_IMODE(policy_path.stat().st_mode) & 0o022:
            raise ValueError("EXECUTION_AUTHORITY_INSTALLATION_POLICY_PERMISSIONS")
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("EXECUTION_AUTHORITY_INSTALLATION_POLICY_INVALID") from exc
    if (
        not isinstance(policy, dict)
        or set(policy) != {"schema", "authorities"}
        or policy.get("schema") != TRUST_POLICY_SCHEMA
        or not isinstance(policy.get("authorities"), dict)
    ):
        raise ValueError("EXECUTION_AUTHORITY_INSTALLATION_POLICY_INVALID")
    entry = policy["authorities"].get(authority)
    if (
        not isinstance(entry, dict)
        or set(entry) != {"principal", "allowed_signers", "signer_fingerprint"}
        or entry.get("principal") != principal
        or not isinstance(entry.get("allowed_signers"), str)
        or not isinstance(entry.get("signer_fingerprint"), str)
    ):
        raise ValueError("EXECUTION_AUTHORITY_INSTALLATION_TRUST_MISSING")
    allowed = _installed_path(root, entry["allowed_signers"])
    if stat.S_IMODE(allowed.stat().st_mode) & 0o022:
        raise ValueError("EXECUTION_AUTHORITY_INSTALLATION_TRUST_PERMISSIONS")
    trust = AuthorityTrust(allowed, entry["signer_fingerprint"], principal)
    trust.validate()
    return trust


def trust_from_environment(_prefix: str, _principal: str) -> AuthorityTrust:
    raise ValueError("EXECUTION_AUTHORITY_ENV_TRUST_FORBIDDEN")


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("EXECUTION_AUTHORITY_TIMESTAMP_INVALID")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("EXECUTION_AUTHORITY_TIMESTAMP_INVALID") from exc
    return parsed.astimezone(timezone.utc)


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def canonical_message(receipt: dict[str, Any]) -> str:
    payload = {key: value for key, value in receipt.items() if key != "signature"}
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"


def build_receipt(
    *, authority: str, action: str, subject_digest: str, provider: str,
    input_digest: str, output_digest: str, claims: dict[str, Any],
    issued_at: datetime, expires_at: datetime,
) -> dict[str, Any]:
    payload = {
        "schema": SCHEMA,
        "decision": "pass",
        "reason": "EXECUTION_AUTHORITY_ATTESTED",
        "authority": authority,
        "action": action,
        "subject_digest": subject_digest,
        "provider": provider,
        "input_digest": input_digest,
        "output_digest": output_digest,
        "claims": claims,
        "issued_at": _timestamp(issued_at),
        "expires_at": _timestamp(expires_at),
    }
    payload["receipt_digest"] = canonical_digest(payload)
    return payload


def sign_receipt(receipt: dict[str, Any], signing_key: Path) -> dict[str, Any]:
    if set(receipt) != BASE_FIELDS | {"receipt_digest"}:
        raise ValueError("EXECUTION_AUTHORITY_RECEIPT_FIELDS_INVALID")
    with tempfile.TemporaryDirectory() as tmp:
        message = Path(tmp) / "receipt.json"
        message.write_text(canonical_message(receipt), encoding="utf-8")
        try:
            subprocess.run(
                [
                    "ssh-keygen", "-Y", "sign", "-f", str(signing_key),
                    "-n", NAMESPACE, str(message),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise ValueError("EXECUTION_AUTHORITY_SIGNATURE_FAILED") from exc
        receipt = dict(receipt)
        receipt["signature"] = Path(f"{message}.sig").read_text(encoding="utf-8")
    return receipt


def validate_receipt(
    receipt: Any, *, trust: AuthorityTrust, authority: str, action: str,
    subject_digest: str, provider: str, input_digest: str,
    output_digest: str, claims: dict[str, Any],
    observed_at: datetime | None = None,
) -> dict[str, str]:
    if not isinstance(receipt, dict) or set(receipt) != FIELDS:
        return {"decision": "block", "reason": "EXECUTION_AUTHORITY_RECEIPT_FIELDS_INVALID"}
    unsigned = {key: receipt.get(key) for key in BASE_FIELDS}
    if receipt.get("receipt_digest") != canonical_digest(unsigned):
        return {"decision": "block", "reason": "EXECUTION_AUTHORITY_RECEIPT_DIGEST_INVALID"}
    expected = {
        "authority": authority,
        "action": action,
        "subject_digest": subject_digest,
        "provider": provider,
        "input_digest": input_digest,
        "output_digest": output_digest,
        "claims": claims,
    }
    for field, value in expected.items():
        if receipt.get(field) != value:
            reason = f"EXECUTION_AUTHORITY_{field.upper()}_MISMATCH"
            return {"decision": "block", "reason": reason}
    if (
        receipt.get("schema") != SCHEMA
        or receipt.get("decision") != "pass"
        or not str(receipt.get("reason") or "")
        or not subject_digest
        or not provider
        or not _valid_digest(input_digest)
        or not _valid_digest(output_digest)
    ):
        return {"decision": "block", "reason": "EXECUTION_AUTHORITY_RECEIPT_INVALID"}
    try:
        issued_at = _parse_timestamp(receipt.get("issued_at"))
        expires_at = _parse_timestamp(receipt.get("expires_at"))
    except ValueError as exc:
        return {"decision": "block", "reason": str(exc)}
    current = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expires_at <= issued_at or current < issued_at or current >= expires_at:
        return {"decision": "block", "reason": "EXECUTION_AUTHORITY_RECEIPT_EXPIRED"}
    try:
        trust.validate()
        verify_pinned_signature(
            message=canonical_message(receipt),
            signature_text=str(receipt.get("signature") or ""),
            allowed_signers=trust.allowed_signers,
            principal=trust.principal,
            namespace=NAMESPACE,
            fingerprint=trust.signer_fingerprint,
            missing_reason="EXECUTION_AUTHORITY_TRUST_MISSING",
            mismatch_reason="EXECUTION_AUTHORITY_SIGNER_MISMATCH",
            invalid_reason="EXECUTION_AUTHORITY_SIGNATURE_INVALID",
            tool_reason="EXECUTION_AUTHORITY_SIGNATURE_TOOL_MISSING",
        )
    except ValueError as exc:
        return {"decision": "block", "reason": str(exc)}
    return {"decision": "pass", "reason": "EXECUTION_AUTHORITY_RECEIPT_VALID"}
