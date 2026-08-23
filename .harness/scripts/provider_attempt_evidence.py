#!/usr/bin/env python3
"""Provider attempt evidence and preflight compatibility validation."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from harness_provider_preflight import (
    RECEIPT_SCHEMA as PREFLIGHT_SCHEMA,
    validate_receipt as validate_harness_preflight,
)


EVIDENCE_SCHEMA = "harness-provider-evidence-v1"


def write_claim(path: Path, payload: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return True


def replace_state(path: Path, payload: dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "absent"


def load_evidence(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "absent" if not path.is_file() else sha256_file(path), {}
    return hashlib.sha256(raw).hexdigest(), value if isinstance(value, dict) else {}


def evidence_reason(payload: dict[str, Any], subject: str, provider: str) -> str:
    if payload.get("schema") != EVIDENCE_SCHEMA:
        return "PROVIDER_EVIDENCE_SCHEMA_INVALID"
    if payload.get("decision") != "pass":
        return "PROVIDER_EVIDENCE_DECISION_INVALID"
    if payload.get("subject_digest") != subject:
        return "PROVIDER_EVIDENCE_SUBJECT_MISMATCH"
    if payload.get("provider") != provider:
        return "PROVIDER_EVIDENCE_PROVIDER_MISMATCH"
    return "PROVIDER_EVIDENCE_OK"


def validate_preflight(
    receipt: dict[str, Any], expected_subject: str, expected_provider: str,
) -> dict[str, str]:
    primary = validate_harness_preflight(receipt, expected_subject, expected_provider)
    if primary["decision"] == "pass" or receipt.get("schema") == PREFLIGHT_SCHEMA:
        return primary
    try:
        from provider_verifier_preflight import validate_receipt as validate_compatibility

        compatibility = validate_compatibility(
            receipt, expected_subject, expected_provider,
        )
    except (ImportError, TypeError, ValueError):
        return primary
    return compatibility if compatibility.get("decision") == "pass" else primary
