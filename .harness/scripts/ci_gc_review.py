#!/usr/bin/env python3
"""Subject-bound GC review adapter for commit acceptance CI."""
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


GC_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["pass", "block"]},
        "findings": {"type": "integer", "minimum": 0},
        "remediated": {"type": "integer", "minimum": 0},
        "deferred_findings": {"type": "integer", "minimum": 0},
        "deferred_work_items": {"type": "array", "items": {"type": "string"}},
        "triggers": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["decision", "findings", "remediated", "deferred_findings",
                 "deferred_work_items", "triggers"],
}


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


def request_remote(endpoint: str, token: str, payload: dict) -> dict:
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        remote = json.loads(response.read().decode("utf-8"))
    receipt = remote.get("result", remote) if isinstance(remote, dict) else {}
    return receipt if isinstance(receipt, dict) else {}


def request_openai(api_key: str, payload: dict) -> dict:
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    endpoint = f"{base}/responses"
    body = {
        "model": os.environ.get("HARNESS_GC_MODEL", "gpt-5-mini"),
        "instructions": (
            "You are an independent gc-sweeper. Review only the supplied task-scoped context. "
            "Do not add features, change acceptance criteria, or expand scope. Block unresolved "
            "dead code, debug residue, harmful duplication, or structural regression. Out-of-scope "
            "debt must reference a deferred work item; never invent identifiers."
        ),
        "input": json.dumps(payload["context"], ensure_ascii=False),
        "text": {"format": {"type": "json_schema", "name": "gc_result",
                              "strict": True, "schema": GC_RESPONSE_SCHEMA}},
    }
    request = urllib.request.Request(
        endpoint, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        remote = json.loads(response.read().decode("utf-8"))
    text = remote.get("output_text", "") if isinstance(remote, dict) else ""
    if not text and isinstance(remote, dict):
        for item in remote.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text += content.get("text", "")
    receipt = json.loads(text)
    receipt.update({
        "role": "gc-sweeper", "independent": True,
        "task_id": payload["task_id"], "subject_digest": payload["subject_digest"],
        "policy_digest": payload["policy_digest"],
    })
    return receipt


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
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    parsed = urllib.parse.urlparse(endpoint)
    remote_configured = parsed.scheme == "https" and bool(parsed.netloc) and bool(token)
    if not remote_configured and not api_key:
        return {"decision": "block", "reason": "GC_REVIEWER_UNAVAILABLE",
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
    try:
        receipt = (request_remote(endpoint, token, payload) if remote_configured
                   else request_openai(api_key, payload))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, KeyError, TypeError):
        return {"decision": "block", "reason": "GC_REVIEW_FAILED", "subject_digest": sha}
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
