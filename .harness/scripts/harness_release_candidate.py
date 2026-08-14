#!/usr/bin/env python3
"""One-shot guarded release-candidate receipts and commit attestations."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


SCHEMA = "harness-guarded-release-candidate-v1"
REF_PREFIX = "refs/harness/release-candidates"


def _git(repo: Path, *args: str, input_text: str | None = None) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repo, text=True, input=input_text,
        stderr=subprocess.DEVNULL,
    ).strip()


def receipt_path(repo: Path) -> Path:
    path = Path(_git(repo, "rev-parse", "--git-path", "harness/release-candidate.json"))
    return path if path.is_absolute() else repo.resolve() / path


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _eligible(result: dict[str, Any]) -> bool:
    checks = result.get("checks") or {}
    failed = {name for name, check in checks.items() if check.get("decision") != "pass"}
    reason = str((checks.get("qa_evidence") or {}).get("reason") or "")
    missing = set(re.findall(r"QA_SIGNOFF_MISSING:(T[1-5])", reason))
    invariants = result.get("invariants") or {}
    return (
        result.get("state") == "blocked" and result.get("decision") == "block"
        and not result.get("blockers") and failed == {"qa_evidence"} and missing == {"T5"}
        and all(value == "pass" for name, value in invariants.items() if name != "risk_validation")
        and invariants.get("risk_validation") == "block"
    )


def create(repo: Path, result: dict[str, Any], *, guarded: bool) -> dict[str, Any]:
    if not guarded:
        return {"decision": "block", "reason": "RELEASE_CANDIDATE_GUARDED_REQUIRED"}
    if not _eligible(result):
        return {"decision": "block", "reason": "RELEASE_CANDIDATE_NOT_ELIGIBLE"}
    staged = sorted(line for line in _git(repo, "diff", "--cached", "--name-only").splitlines() if line)
    if not staged or staged != sorted((result.get("subject") or {}).get("paths") or []):
        return {"decision": "block", "reason": "RELEASE_CANDIDATE_INDEX_SCOPE_MISMATCH"}
    receipt = {
        "schema": SCHEMA, "task_id": result.get("task_id"),
        "policy_digest": result.get("policy_digest"),
        "binding_digest": result.get("binding_digest"), "result_digest": _digest(result),
        "pending_gates": ["T5"], "parent": _git(repo, "rev-parse", "HEAD"),
        "tree": _git(repo, "write-tree"), "paths": staged,
    }
    if not all(receipt.get(field) for field in ("task_id", "policy_digest", "binding_digest")):
        return {"decision": "block", "reason": "RELEASE_CANDIDATE_BINDING_MISSING"}
    path = receipt_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)
    return {"decision": "pass", "reason": "RELEASE_CANDIDATE_CREATED", "receipt": receipt}


def check(repo: Path, *, consume: bool = False) -> dict[str, Any]:
    path = receipt_path(repo)
    try:
        receipt = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"decision": "block", "reason": "RELEASE_CANDIDATE_MISSING"}
    if receipt.get("schema") != SCHEMA or receipt.get("pending_gates") != ["T5"]:
        return {"decision": "block", "reason": "RELEASE_CANDIDATE_INVALID"}
    if consume:
        valid = (_git(repo, "rev-parse", "HEAD^1") == receipt.get("parent")
                 and _git(repo, "rev-parse", "HEAD^{tree}") == receipt.get("tree"))
    else:
        staged = sorted(line for line in _git(repo, "diff", "--cached", "--name-only").splitlines() if line)
        valid = (_git(repo, "rev-parse", "HEAD") == receipt.get("parent")
                 and _git(repo, "write-tree") == receipt.get("tree") and staged == receipt.get("paths"))
    if not valid:
        return {"decision": "block", "reason": "RELEASE_CANDIDATE_BINDING_MISMATCH"}
    if not consume:
        return {"decision": "pass", "reason": "RELEASE_CANDIDATE_VALID", "receipt": receipt}
    sha = _git(repo, "rev-parse", "HEAD")
    payload = {**receipt, "commit_sha": sha, "tree_sha": receipt["tree"],
               "status": "pending-production-qa"}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        blob = _git(repo, "hash-object", "-w", "--stdin", input_text=raw)
        subprocess.run(
            ["git", "update-ref", f"{REF_PREFIX}/{sha}", blob], cwd=repo, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {"decision": "block", "reason": "RELEASE_CANDIDATE_ATTESTATION_FAILED"}
    path.unlink()
    return {"decision": "pass", "reason": "RELEASE_CANDIDATE_ATTESTED", "attestation": payload}
