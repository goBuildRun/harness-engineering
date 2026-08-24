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
PENDING_GATE_ORDER = ("T5", "T-GC", "strict_evidence")


def _git(repo: Path, *args: str, input_text: str | None = None) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repo, text=True, input=input_text,
        stderr=subprocess.DEVNULL,
    ).strip()


def receipt_path(repo: Path) -> Path:
    path = Path(_git(repo, "rev-parse", "--git-path", "harness/release-candidate.json"))
    return path if path.is_absolute() else repo.resolve() / path


def _staged_paths(repo: Path) -> list[str]:
    output = _git(repo, "-c", "core.quotePath=false", "diff", "--cached", "--name-only")
    return sorted(line for line in output.splitlines() if line)


def _unstaged_paths(repo: Path) -> list[str]:
    tracked = _git(repo, "-c", "core.quotePath=false", "diff", "--name-only")
    untracked = _git(
        repo, "-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard",
    )
    return sorted({line for output in (tracked, untracked) for line in output.splitlines() if line})


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _pending_gates(result: dict[str, Any]) -> list[str] | None:
    checks = result.get("checks") or {}
    failed = {name for name, check in checks.items() if check.get("decision") != "pass"}
    reason = str((checks.get("qa_evidence") or {}).get("reason") or "")
    prefix = "QA_EVIDENCE_INVALID:"
    issues = {part.strip() for part in reason.removeprefix(prefix).split(";") if part.strip()}
    missing = set(re.findall(r"QA_SIGNOFF_MISSING:(T(?:[1-5]|-GC))", reason))
    expected_issues = {f"QA_SIGNOFF_MISSING:{task_id}" for task_id in missing}
    strict = checks.get("strict_evidence") or {}
    strict_pending = (
        strict.get("decision") == "block"
        and strict.get("reason") == "STRICT_EVIDENCE_REQUIRED"
    )
    allowed_failed = {"qa_evidence"} | ({"strict_evidence"} if strict_pending else set())
    invariants = result.get("invariants") or {}
    eligible = (
        result.get("state") == "blocked" and result.get("decision") == "block"
        and not result.get("blockers") and failed == allowed_failed
        and reason.startswith(prefix) and issues == expected_issues
        and "T5" in missing and missing <= {"T5", "T-GC"}
        and all(value == "pass" for name, value in invariants.items() if name != "risk_validation")
        and invariants.get("risk_validation") == "block"
    )
    if not eligible:
        return None
    pending = set(missing)
    if strict_pending:
        pending.add("strict_evidence")
    return [name for name in PENDING_GATE_ORDER if name in pending]


def _eligible(result: dict[str, Any]) -> bool:
    return _pending_gates(result) is not None


def _valid_pending_gates(value: Any) -> bool:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return False
    pending = set(value)
    return (
        "T5" in pending
        and pending <= set(PENDING_GATE_ORDER)
        and value == [name for name in PENDING_GATE_ORDER if name in pending]
    )


def create(repo: Path, result: dict[str, Any], *, guarded: bool) -> dict[str, Any]:
    if not guarded:
        return {"decision": "block", "reason": "RELEASE_CANDIDATE_GUARDED_REQUIRED"}
    pending_gates = _pending_gates(result)
    if pending_gates is None:
        return {"decision": "block", "reason": "RELEASE_CANDIDATE_NOT_ELIGIBLE"}
    staged = _staged_paths(repo)
    subject_paths = set((result.get("subject") or {}).get("paths") or [])
    unstaged_subject_paths = subject_paths.intersection(_unstaged_paths(repo))
    if not staged or not set(staged).issubset(subject_paths) or unstaged_subject_paths:
        return {"decision": "block", "reason": "RELEASE_CANDIDATE_INDEX_SCOPE_MISMATCH"}
    receipt = {
        "schema": SCHEMA, "task_id": result.get("task_id"),
        "policy_digest": result.get("policy_digest"),
        "binding_digest": result.get("binding_digest"), "result_digest": _digest(result),
        "pending_gates": pending_gates, "parent": _git(repo, "rev-parse", "HEAD"),
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
    if receipt.get("schema") != SCHEMA or not _valid_pending_gates(receipt.get("pending_gates")):
        return {"decision": "block", "reason": "RELEASE_CANDIDATE_INVALID"}
    if consume:
        valid = (_git(repo, "rev-parse", "HEAD^1") == receipt.get("parent")
                 and _git(repo, "rev-parse", "HEAD^{tree}") == receipt.get("tree"))
    else:
        staged = _staged_paths(repo)
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
