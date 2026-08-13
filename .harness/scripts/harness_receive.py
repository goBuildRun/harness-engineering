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
from provider_lifecycle import build_receipt

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
            "task_id": result["task_id"], "work_item": result.get("work_item"),
            "attestation_object": attested["object"]}


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
            outcome["ref"] = ref
            verified.append(outcome)
    return {"decision": "pass", "reason": "RECEIVE_UPDATES_VALID",
            "verified_commits": verified}


def accept_updates(repo: Path, harness: Path, updates: list[tuple[str, str, str]], *,
                   signing_key: Path, protected_refs: tuple[str, ...] = ("refs/heads/main",)
                   ) -> dict[str, Any]:
    outcome = verify_updates(repo, harness, updates, protected_refs=protected_refs)
    if outcome["decision"] != "pass":
        return outcome
    bindings: list[tuple[dict[str, Any], str, str]] = []
    for verified in outcome["verified_commits"]:
        work_item = verified.get("work_item")
        if (not isinstance(work_item, dict) or not isinstance(work_item.get("id"), str)
                or not work_item["id"] or not isinstance(work_item.get("provider"), str)
                or not work_item["provider"]):
            return {"decision": "block", "reason": "ACCEPTANCE_WORK_ITEM_BINDING_MISSING",
                    "commit": verified["commit"], "verified_commits": outcome["verified_commits"]}
        bindings.append((verified, work_item["id"], work_item["provider"]))
    receipts = []
    try:
        for verified, work_item_id, provider in bindings:
            receipts.append(build_receipt(
                repo, commit=verified["commit"], work_item_id=work_item_id,
                provider=provider, authority="git-receive", accepted_ref=verified["ref"],
                signing_key=signing_key,
            ))
    except ValueError as exc:
        return {"decision": "block", "reason": str(exc),
                "verified_commits": outcome["verified_commits"]}
    return {**outcome, "reason": "RECEIVE_UPDATES_ACCEPTED", "receipts": receipts}


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
    parser.add_argument("--signing-key", default="")
    parser.add_argument("--receipt-dir", default="")
    args = parser.parse_args()
    try:
        updates = parse_updates(sys.stdin)
    except ValueError as exc:
        dump_json({"decision": "block", "reason": str(exc)})
        return 1
    configured = tuple(args.protected_ref or os.environ.get(
        "HARNESS_PROTECTED_REFS", "refs/heads/main").split(","))
    repo = Path(args.repo).resolve()
    harness = Path(args.harness_root).resolve()
    if args.signing_key or args.receipt_dir:
        if not args.signing_key or not args.receipt_dir:
            outcome = {"decision": "block", "reason": "ACCEPTANCE_OUTPUT_CONFIG_INVALID"}
        else:
            outcome = accept_updates(
                repo, harness, updates, signing_key=Path(args.signing_key),
                protected_refs=configured,
            )
            if outcome["decision"] == "pass":
                receipt_dir = Path(args.receipt_dir)
                receipt_dir.mkdir(parents=True, exist_ok=True)
                for receipt in outcome["receipts"]:
                    target = receipt_dir / f"{receipt['commit_sha']}.json"
                    temporary = target.with_suffix(".json.tmp")
                    temporary.write_text(
                        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                    )
                    os.replace(temporary, target)
    else:
        outcome = verify_updates(repo, harness, updates, protected_refs=configured)
    dump_json(outcome)
    return 0 if outcome["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
