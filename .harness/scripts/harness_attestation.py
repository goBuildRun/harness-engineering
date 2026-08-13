#!/usr/bin/env python3
"""Canonical Git-object attestations for Harness-validated commits."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from harness_output import dump_json
from harness_runtime import canonical_digest, now

SCHEMA = "harness-git-attestation-v1"
REF_PREFIX = "refs/harness/attestations"


def _git(repo: Path, *args: str, input_text: str | None = None) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repo, text=True, input=input_text,
        stderr=subprocess.DEVNULL,
    ).strip()


def resolve_commit(repo: Path, commit: str) -> str:
    try:
        value = _git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}")
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""
    try:
        return value if _git(repo, "cat-file", "-t", value) == "commit" else ""
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def attestation_ref(commit: str) -> str:
    return f"{REF_PREFIX}/{commit}"


def commit_subject(repo: Path, commit: str, paths: list[str]) -> str:
    entries: list[tuple[str, str]] = []
    for rel in sorted(set(paths)):
        try:
            content = subprocess.check_output(
                ["git", "show", f"{commit}:{rel}"], cwd=repo, stderr=subprocess.DEVNULL,
            )
            digest = hashlib.sha256(content).hexdigest()
        except (FileNotFoundError, subprocess.CalledProcessError):
            digest = "absent"
        entries.append((rel, digest))
    return canonical_digest(entries)


def create_attestation(repo: Path, result: dict[str, Any], *, commit: str = "HEAD") -> dict[str, Any]:
    sha = resolve_commit(repo, commit)
    if not sha:
        return {"decision": "block", "reason": "ATTESTATION_COMMIT_INVALID"}
    if result.get("decision") != "pass" or result.get("state") != "validated":
        return {"decision": "block", "reason": "HARNESS_RESULT_NOT_PASS"}
    task_id = str(result.get("task_id") or "")
    policy = str(result.get("policy_digest") or "")
    if not task_id or not policy:
        return {"decision": "block", "reason": "HARNESS_RESULT_BINDING_MISSING"}
    subject = result.get("subject") or {}
    if subject.get("kind") == "worktree":
        paths = subject.get("paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(path, str) for path in paths):
            return {"decision": "block", "reason": "HARNESS_RESULT_SUBJECT_PATHS_MISSING"}
        if commit_subject(repo, sha, paths) != subject.get("digest"):
            return {"decision": "block", "reason": "HARNESS_RESULT_SUBJECT_MISMATCH"}
    elif subject.get("kind") == "commit":
        if subject.get("digest") != sha:
            return {"decision": "block", "reason": "HARNESS_RESULT_SUBJECT_MISMATCH"}
    else:
        return {"decision": "block", "reason": "HARNESS_RESULT_SUBJECT_INVALID"}
    result_raw = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        result_object = _git(repo, "hash-object", "-w", "--stdin", input_text=result_raw)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {"decision": "block", "reason": "ATTESTATION_WRITE_FAILED"}
    payload = {
        "schema": SCHEMA,
        "commit_sha": sha,
        "tree_sha": _git(repo, "rev-parse", f"{sha}^{{tree}}"),
        "task_id": task_id,
        "policy_digest": policy,
        "binding_digest": str(result.get("binding_digest") or ""),
        "result_digest": canonical_digest(result),
        "result_object": result_object,
        "decision": "pass",
        "created_at": now(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        blob = _git(repo, "hash-object", "-w", "--stdin", input_text=raw)
        subprocess.run(["git", "update-ref", attestation_ref(sha), blob], cwd=repo,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {"decision": "block", "reason": "ATTESTATION_WRITE_FAILED"}
    return {"decision": "pass", "reason": "ATTESTATION_CREATED", "ref": attestation_ref(sha),
            "object": blob, "attestation": payload}


def verify_attestation(repo: Path, *, commit: str = "HEAD", policy_digest: str = "") -> dict[str, Any]:
    sha = resolve_commit(repo, commit)
    if not sha:
        return {"decision": "block", "reason": "ATTESTATION_COMMIT_INVALID"}
    ref = attestation_ref(sha)
    try:
        obj = _git(repo, "rev-parse", "--verify", ref)
        if _git(repo, "cat-file", "-t", obj) != "blob":
            raise ValueError
        payload = json.loads(_git(repo, "cat-file", "blob", obj))
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError, ValueError):
        return {"decision": "block", "reason": "ATTESTATION_MISSING", "ref": ref}
    if payload.get("schema") != SCHEMA:
        return {"decision": "block", "reason": "ATTESTATION_SCHEMA_INVALID", "ref": ref}
    if payload.get("commit_sha") != sha:
        return {"decision": "block", "reason": "ATTESTATION_COMMIT_MISMATCH", "ref": ref}
    if payload.get("tree_sha") != _git(repo, "rev-parse", f"{sha}^{{tree}}"):
        return {"decision": "block", "reason": "ATTESTATION_TREE_MISMATCH", "ref": ref}
    if payload.get("decision") != "pass":
        return {"decision": "block", "reason": "ATTESTATION_DECISION_BLOCK", "ref": ref}
    if policy_digest and payload.get("policy_digest") != policy_digest:
        return {"decision": "block", "reason": "ATTESTATION_POLICY_MISMATCH", "ref": ref}
    try:
        result = json.loads(_git(repo, "cat-file", "blob", str(payload.get("result_object") or "")))
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
        return {"decision": "block", "reason": "ATTESTATION_RESULT_INVALID", "ref": ref}
    if (canonical_digest(result) != payload.get("result_digest")
            or result.get("task_id") != payload.get("task_id")
            or result.get("policy_digest") != payload.get("policy_digest")
            or result.get("decision") != "pass" or result.get("state") != "validated"):
        return {"decision": "block", "reason": "ATTESTATION_RESULT_INVALID", "ref": ref}
    return {"decision": "pass", "reason": "ATTESTATION_VALID", "ref": ref,
            "object": obj, "attestation": payload, "result": result}


def main() -> int:
    parser = argparse.ArgumentParser(description="Internal Git-native Harness attestation helper")
    parser.add_argument("action", choices=("create", "verify"))
    parser.add_argument("--repo", default=".")
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--result", default="")
    parser.add_argument("--policy-digest", default="")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    if args.action == "verify":
        outcome = verify_attestation(repo, commit=args.commit, policy_digest=args.policy_digest)
    else:
        try:
            result = json.loads(Path(args.result).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            outcome = {"decision": "block", "reason": "HARNESS_RESULT_INVALID"}
        else:
            outcome = create_attestation(repo, result, commit=args.commit)
    dump_json(outcome)
    return 0 if outcome["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
