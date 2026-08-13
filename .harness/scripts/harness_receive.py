#!/usr/bin/env python3
"""Read-only Git receive verifier for protected refs."""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from harness_attestation import verify_attestation
from harness_output import dump_json
from harness_runtime import classify_tier, git_changed, mechanical_code_health, policy_for
from harness_scope import paths_within_scope

ZERO_SHA = "0" * 40


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repo, text=True, stderr=subprocess.DEVNULL,
    ).strip()


def is_zero(value: str) -> bool:
    return bool(value) and set(value) == {"0"}


def commits_for_update(repo: Path, old: str, new: str) -> list[str]:
    if is_zero(new):
        return []
    revision = new if is_zero(old) else f"{old}..{new}"
    output = _git(repo, "rev-list", "--reverse", revision)
    return [line for line in output.splitlines() if line]


def _export_commit(repo: Path, commit: str, destination: Path) -> None:
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", commit], cwd=repo,
        stderr=subprocess.DEVNULL,
    )
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        bundle.extractall(destination, filter="data")


def verify_commit(repo: Path, harness: Path, commit: str) -> dict[str, Any]:
    attested = verify_attestation(repo, commit=commit)
    if attested.get("decision") != "pass":
        return {"decision": "block", "reason": "RECEIVE_ATTESTATION_INVALID", "commit": commit,
                "detail": attested.get("reason")}
    result = attested["result"]
    task = result.get("task") or {}
    scope = task.get("scope") or []
    tier_floor = str(task.get("tier_floor") or (result.get("tier") or {}).get("initial") or "standard")
    if not isinstance(scope, list) or not scope or not all(isinstance(path, str) for path in scope):
        return {"decision": "block", "reason": "RECEIVE_TASK_BINDING_INVALID", "commit": commit}
    try:
        changed = git_changed(repo, commit)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {"decision": "block", "reason": "RECEIVE_DIFF_FAILED", "commit": commit}
    with tempfile.TemporaryDirectory() as tmp:
        checkout = Path(tmp) / "subject"
        checkout.mkdir()
        try:
            _export_commit(repo, commit, checkout)
        except (FileNotFoundError, subprocess.CalledProcessError):
            return {"decision": "block", "reason": "RECEIVE_CHECKOUT_FAILED", "commit": commit}
        effective = classify_tier(changed, floor=tier_floor)
        if effective != (result.get("tier") or {}).get("effective"):
            return {"decision": "block", "reason": "RECEIVE_TIER_MISMATCH", "commit": commit}
        if not paths_within_scope(changed, scope):
            return {"decision": "block", "reason": "RECEIVE_SCOPE_BLOCK", "commit": commit}
        policy = policy_for(harness, checkout)
        if result.get("policy_digest") != policy:
            return {"decision": "block", "reason": "RECEIVE_POLICY_MISMATCH", "commit": commit}
        health = mechanical_code_health(checkout, changed, tier=effective)
        if health["decision"] != "pass":
            return {"decision": "block", "reason": "RECEIVE_CODE_HEALTH_BLOCK", "commit": commit}
    checks = result.get("checks") or {}
    if any(check.get("decision") != "pass" or check.get("stale") for check in checks.values()):
        return {"decision": "block", "reason": "RECEIVE_RESULT_CHECK_BLOCK", "commit": commit}
    return {"decision": "pass", "reason": "RECEIVE_COMMIT_VALID", "commit": commit,
            "task_id": result["task_id"], "attestation_object": attested["object"]}


def verify_updates(repo: Path, harness: Path, updates: list[tuple[str, str, str]], *,
                   protected_refs: tuple[str, ...] = ("refs/heads/main",)) -> dict[str, Any]:
    verified: list[dict[str, Any]] = []
    for old, new, ref in updates:
        if ref not in protected_refs:
            continue
        try:
            commits = commits_for_update(repo, old, new)
        except (FileNotFoundError, subprocess.CalledProcessError):
            return {"decision": "block", "reason": "RECEIVE_RANGE_INVALID", "ref": ref,
                    "verified_commits": verified}
        for commit in commits:
            outcome = verify_commit(repo, harness, commit)
            if outcome["decision"] != "pass":
                return {**outcome, "ref": ref, "verified_commits": verified}
            verified.append(outcome)
    return {"decision": "pass", "reason": "RECEIVE_UPDATES_VALID",
            "verified_commits": verified}


def parse_updates(stream: Any) -> list[tuple[str, str, str]]:
    updates = []
    for line in stream:
        parts = line.strip().split()
        if len(parts) != 3:
            raise ValueError("RECEIVE_INPUT_INVALID")
        updates.append((parts[0], parts[1], parts[2]))
    return updates


def main() -> int:
    parser = argparse.ArgumentParser(description="Internal bare Git pre-receive verifier")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--harness-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--protected-ref", action="append", default=[])
    args = parser.parse_args()
    try:
        updates = parse_updates(sys.stdin)
    except ValueError as exc:
        dump_json({"decision": "block", "reason": str(exc)})
        return 1
    configured = tuple(args.protected_ref or os.environ.get(
        "HARNESS_PROTECTED_REFS", "refs/heads/main").split(","))
    outcome = verify_updates(
        Path(args.repo).resolve(), Path(args.harness_root).resolve(), updates,
        protected_refs=configured,
    )
    dump_json(outcome)
    return 0 if outcome["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
