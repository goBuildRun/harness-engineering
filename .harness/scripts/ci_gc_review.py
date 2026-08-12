#!/usr/bin/env python3
"""Subject-bound HTTPS GC review adapter for commit acceptance CI."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from harness_gc_context import build_gc_context
from harness_output import dump_json
from harness_runtime import (
    classify_tier, default_result, mechanical_code_health, policy_for,
    git_changed, valid_gc_result,
)
from harness_gates import committed_work_item


def resolve_commit(product: Path, value: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", value], cwd=product, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def emit_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def review(args: argparse.Namespace) -> dict:
    product, harness = Path(args.product_root).resolve(), Path(args.harness_root).resolve()
    sha = resolve_commit(product, args.commit)
    if not sha:
        return {"decision": "block", "reason": "CI_COMMIT_INVALID"}
    changed = git_changed(product, sha)
    effective = classify_tier(changed, floor=args.tier)
    mechanical = mechanical_code_health(product, changed, tier=effective)
    if not mechanical.get("agent_required"):
        return {"decision": "pass", "reason": "GC_NOT_REQUIRED", "required": False,
                "subject_digest": sha, "triggers": mechanical.get("triggers", [])}
    endpoint = os.environ.get("HARNESS_GC_REVIEW_URL", "").strip()
    token = os.environ.get("HARNESS_GC_REVIEW_TOKEN", "").strip()
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc or not token:
        return {"decision": "block", "reason": "GC_REVIEW_CONFIG_MISSING",
                "subject_digest": sha, "triggers": mechanical.get("triggers", [])}
    policy = policy_for(harness, product)
    result = default_result(args.task_id, initial_tier=effective,
                            work_item=committed_work_item(harness, product, args.task_id))
    result["policy_digest"] = policy
    result["task"] = {"task_id": args.task_id, "scope": args.scope, "tier_floor": args.tier}
    try:
        context, context_chars = build_gc_context(
            result, mechanical, changed, product, base_ref=f"{sha}^")
    except ValueError:
        return {"decision": "block", "reason": "BUDGET_APPROVAL_REQUIRED",
                "subject_digest": sha}
    payload = {
        "schema_version": 1, "task_id": args.task_id, "commit_sha": sha,
        "subject_digest": sha, "policy_digest": policy, "context": context,
    }
    started = time.monotonic()
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers={"Authorization": f"Bearer {token}",
                                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            remote = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return {"decision": "block", "reason": "GC_REVIEW_FAILED", "subject_digest": sha}
    receipt = remote.get("result", remote) if isinstance(remote, dict) else {}
    if not isinstance(receipt, dict):
        receipt = {}
    receipt["telemetry"] = {"context_chars": context_chars,
                             "duration_ms": int((time.monotonic() - started) * 1000),
                             "agent_calls": 1}
    if not valid_gc_result(receipt, result, sha, policy):
        return {"decision": "block", "reason": "GC_REVIEW_RECEIPT_INVALID",
                "subject_digest": sha}
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", required=True)
    parser.add_argument("--product-root", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tier", required=True)
    parser.add_argument("--scope", action="append", default=[])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = review(args)
    emit_file(Path(args.output), result)
    dump_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
