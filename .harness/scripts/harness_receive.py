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
from harness_gates import run_gate_plan
from harness_gc_context import build_gc_context
from harness_gc_receipt import verify_receipt as verify_gc_receipt
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


def is_fast_forward(repo: Path, old: str, new: str) -> bool:
    if is_zero(old) or is_zero(new):
        return True
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", old, new], cwd=repo,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def valid_update_objects(repo: Path, old: str, new: str) -> bool:
    values = [value for value in (old, new) if not is_zero(value)]
    try:
        return all(_git(repo, "cat-file", "-t", value) == "commit" for value in values)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _export_tree(repo: Path, commit: str, destination: Path) -> None:
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", commit], cwd=repo, stderr=subprocess.DEVNULL,
    )
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        bundle.extractall(destination, filter="data")


def _checkout_commit(repo: Path, commit: str, destination: Path) -> None:
    synthetic_env = dict(os.environ)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_QUARANTINE_PATH"):
        synthetic_env.pop(name, None)
    subprocess.run(
        ["git", "init", "--quiet", str(destination)],
        check=True, env=synthetic_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    subprocess.run(["git", "config", "user.email", "authority@harness.invalid"], cwd=destination,
                   check=True, env=synthetic_env)
    subprocess.run(["git", "config", "user.name", "Harness Authority"], cwd=destination,
                   check=True, env=synthetic_env)
    try:
        parent = _git(repo, "rev-parse", f"{commit}^1")
    except (FileNotFoundError, subprocess.CalledProcessError):
        parent = ""
    if parent:
        _export_tree(repo, parent, destination)
        subprocess.run(["git", "add", "-A"], cwd=destination, check=True, env=synthetic_env)
        subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "-m", "subject parent"],
            cwd=destination, check=True, env=synthetic_env,
        )
        for child in destination.iterdir():
            if child.name != ".git":
                if child.is_dir():
                    import shutil
                    shutil.rmtree(child)
                else:
                    child.unlink()
    _export_tree(repo, commit, destination)
    subprocess.run(["git", "add", "-A"], cwd=destination, check=True, env=synthetic_env)
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "--allow-empty",
         "-m", "subject commit"], cwd=destination,
        check=True, env=synthetic_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def verify_commit(repo: Path, harness: Path, commit: str, *,
                  gc_allowed_signers: Path | None = None) -> dict[str, Any]:
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
        try:
            _checkout_commit(repo, commit, checkout)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            return {"decision": "block", "reason": "RECEIVE_CHECKOUT_FAILED", "commit": commit,
                    "detail": type(exc).__name__ + (
                        f":exit={exc.returncode}" if isinstance(exc, subprocess.CalledProcessError) else ""
                    )}
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
        if health.get("agent_required"):
            if gc_allowed_signers is None:
                return {"decision": "block", "reason": "RECEIVE_GC_AUTHORITY_REQUIRED",
                        "commit": commit, "triggers": health.get("triggers", [])}
            try:
                context, _ = build_gc_context(
                    result, health, changed, checkout, base_ref="HEAD^",
                )
            except ValueError:
                return {"decision": "block", "reason": "BUDGET_APPROVAL_REQUIRED",
                        "commit": commit}
            gc = verify_gc_receipt(
                repo, commit=commit, task_id=str(result.get("task_id") or ""),
                policy_digest=policy, context=context, triggers=health.get("triggers", []),
                allowed_signers=gc_allowed_signers,
            )
            if gc["decision"] != "pass":
                return {"decision": "block", "reason": gc["reason"], "commit": commit}
        gates = run_gate_plan(
            harness, checkout, tier=effective, subject_digest=commit, policy_digest=policy,
            ci_task_id=str(result.get("task_id") or ""), changed_files=changed, read_only=True,
        )
        if gates["decision"] != "pass" or gates["missing"]:
            return {"decision": "block", "reason": "RECEIVE_GATE_BLOCK", "commit": commit,
                    "blocked_gates": sorted(
                        name for name, check in gates["checks"].items()
                        if check.get("decision") != "pass"
                    ) + list(gates["missing"])}
    return {"decision": "pass", "reason": "RECEIVE_COMMIT_VALID", "commit": commit,
            "task_id": result["task_id"], "work_item": result.get("work_item"),
            "attestation_object": attested["object"]}


def verify_updates(repo: Path, harness: Path, updates: list[tuple[str, str, str]], *,
                   protected_refs: tuple[str, ...] = ("refs/heads/main",),
                   gc_allowed_signers: Path | None = None) -> dict[str, Any]:
    verified: list[dict[str, Any]] = []
    for old, new, ref in updates:
        if ref not in protected_refs:
            continue
        if not valid_update_objects(repo, old, new):
            return {"decision": "block", "reason": "RECEIVE_RANGE_INVALID", "ref": ref,
                    "verified_commits": verified}
        if not is_fast_forward(repo, old, new):
            return {"decision": "block", "reason": "RECEIVE_NON_FAST_FORWARD_BLOCK",
                    "ref": ref, "verified_commits": verified}
        try:
            commits = commits_for_update(repo, old, new)
        except (FileNotFoundError, subprocess.CalledProcessError):
            return {"decision": "block", "reason": "RECEIVE_RANGE_INVALID", "ref": ref,
                    "verified_commits": verified}
        for commit in commits:
            outcome = verify_commit(repo, harness, commit, gc_allowed_signers=gc_allowed_signers)
            if outcome["decision"] != "pass":
                return {**outcome, "ref": ref, "verified_commits": verified}
            outcome["ref"] = ref
            verified.append(outcome)
    return {"decision": "pass", "reason": "RECEIVE_UPDATES_VALID",
            "verified_commits": verified}


def accept_updates(repo: Path, harness: Path, updates: list[tuple[str, str, str]], *,
                   signing_key: Path, protected_refs: tuple[str, ...] = ("refs/heads/main",),
                   gc_allowed_signers: Path | None = None,
                   ) -> dict[str, Any]:
    outcome = verify_updates(repo, harness, updates, protected_refs=protected_refs,
                             gc_allowed_signers=gc_allowed_signers)
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
    parser.add_argument("--gc-allowed-signers", default="")
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
                gc_allowed_signers=Path(args.gc_allowed_signers) if args.gc_allowed_signers else None,
            )
            if outcome["decision"] == "pass":
                receipt_dir = Path(args.receipt_dir)
                receipt_dir.mkdir(parents=True, exist_ok=True)
                for receipt in outcome["receipts"]:
                    target = receipt_dir / f"{receipt['commit_sha']}.json"
                    fd, temporary_name = tempfile.mkstemp(
                        prefix=f".{receipt['commit_sha']}.", suffix=".tmp", dir=receipt_dir,
                    )
                    temporary = Path(temporary_name)
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as stream:
                            json.dump(receipt, stream, ensure_ascii=False, indent=2)
                            stream.write("\n")
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, target)
                    finally:
                        temporary.unlink(missing_ok=True)
    else:
        outcome = verify_updates(
            repo, harness, updates, protected_refs=configured,
            gc_allowed_signers=Path(args.gc_allowed_signers) if args.gc_allowed_signers else None,
        )
    dump_json(outcome)
    return 0 if outcome["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
