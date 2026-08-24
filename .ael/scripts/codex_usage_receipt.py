#!/usr/bin/env python3
"""Export exact server-reported Codex token totals without copying conversation content."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


def fail(reason: str) -> int:
    print(reason, file=sys.stderr)
    return 2


def non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def build_receipt(raw: bytes, *, task_id: str, subject_digest: str,
                  policy_digest: str) -> dict[str, Any]:
    """Parse only metadata and token events from one rollout."""
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    session_ids: set[str] = set()
    originator = ""
    provider = ""
    model = ""
    usage: dict[str, Any] | None = None
    usage_at = ""
    for row in rows:
        payload = row.get("payload") if isinstance(row, dict) else None
        if not isinstance(payload, dict):
            continue
        if row.get("type") == "session_meta":
            session_id = str(payload.get("id") or payload.get("session_id") or "").strip()
            if session_id:
                session_ids.add(session_id)
            originator = str(payload.get("originator") or originator).strip()
            provider = str(payload.get("model_provider") or provider).strip()
        elif row.get("type") == "turn_context":
            model = str(payload.get("model") or model).strip()
        elif row.get("type") == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            candidate = info.get("total_token_usage") if isinstance(info, dict) else None
            required = ("input_tokens", "output_tokens", "total_tokens")
            if isinstance(candidate, dict) and all(non_negative_int(candidate.get(key)) for key in required):
                usage = candidate
                usage_at = str(row.get("timestamp") or "")
    if len(session_ids) != 1:
        raise ValueError("CODEX_SESSION_ID_INVALID: rollout must contain exactly one session id")
    if usage is None:
        raise ValueError("CODEX_USAGE_MISSING: no exact cumulative token_count event")
    if usage["input_tokens"] + usage["output_tokens"] != usage["total_tokens"]:
        raise ValueError("CODEX_USAGE_INVALID: total_tokens does not reconcile")
    if not model:
        raise ValueError("CODEX_MODEL_MISSING: no turn_context model")
    source = {
        "kind": "codex-rollout-token-count-v1",
        "session_id": next(iter(session_ids)),
        "originator": originator or "Codex",
        "rollout_sha256": hashlib.sha256(raw).hexdigest(),
        "rollout_size_bytes": len(raw),
        "usage_event_at": usage_at,
        "cached_input_tokens": usage.get("cached_input_tokens", "unknown"),
        "reasoning_output_tokens": usage.get("reasoning_output_tokens", "unknown"),
        "total_tokens": usage["total_tokens"],
    }
    return {
        "task_id": task_id, "subject_digest": subject_digest,
        "policy_digest": policy_digest, "provider": provider or "openai", "model": model,
        "implementation": {
            "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
            "context_chars": "unknown", "agent_calls": "unknown",
        },
        "harness": {
            "input_tokens": "unknown", "output_tokens": "unknown",
            "context_chars": "unknown", "agent_calls": "unknown",
        },
        "source": source,
    }


def automatic_receipt(task_id: str, subject_digest: str,
                      policy_digest: str) -> dict[str, Any] | None:
    thread_id = os.environ.get("CODEX_THREAD_ID", "").strip()
    if not thread_id:
        return None
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    matches = list((codex_home / "sessions").glob(f"**/*{thread_id}*.jsonl"))
    matches += list((codex_home / "archived_sessions").glob(f"*{thread_id}*.jsonl"))
    if len(matches) != 1:
        return None
    try:
        receipt = build_receipt(
            matches[0].read_bytes(), task_id=task_id,
            subject_digest=subject_digest, policy_digest=policy_digest,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if receipt["source"]["session_id"] != thread_id:
        return None
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rollout", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--subject-digest", required=True)
    parser.add_argument("--policy-digest", required=True)
    args = parser.parse_args()

    rollout = Path(args.rollout).expanduser().resolve()
    output = Path(args.output).resolve()
    try:
        raw = rollout.read_bytes()
    except OSError as exc:
        return fail(f"CODEX_ROLLOUT_UNREADABLE: {exc}")

    try:
        receipt = build_receipt(raw, task_id=args.task_id,
                                subject_digest=args.subject_digest,
                                policy_digest=args.policy_digest)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return fail(str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": "pass", "reason": "CODEX_USAGE_EXPORTED", "source": receipt["source"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
