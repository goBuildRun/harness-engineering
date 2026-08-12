#!/usr/bin/env python3
"""Build diff-bound review checklists and validate independent reviewer receipts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


REVIEW_VIEWS = ("correctness", "security", "tests", "scope")
IMPLEMENTATION_ROLES = {"backend-agent", "frontend-agent", "implementation-agent", "lead-agent"}


def diff_subject(repo: Path, changed_files: list[str]) -> str:
    entries: list[dict[str, str]] = []
    for rel in sorted(set(changed_files)):
        path = repo / rel
        digest = "missing"
        if path.is_file():
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                digest = "unreadable"
        entries.append({"path": rel, "digest": digest})
    raw = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def build_checklist(repo: Path, changed_files: list[str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "subject_digest": diff_subject(repo, changed_files),
        "changed_files": sorted(set(changed_files)),
        "views": [{"name": name, "status": "required"} for name in REVIEW_VIEWS],
        "decision": "ready_for_review",
    }


def load_and_validate_receipt(path: Path, checklist: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"AGENT_REVIEW_RECEIPT_INVALID: {type(exc).__name__}"
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
        return None, "AGENT_REVIEW_RECEIPT_SCHEMA_INVALID"
    if receipt.get("subject_digest") != checklist["subject_digest"]:
        return None, "AGENT_REVIEW_SUBJECT_MISMATCH"
    role = str(receipt.get("reviewer_role") or "")
    if not role or role in IMPLEMENTATION_ROLES:
        return None, "AGENT_REVIEW_INDEPENDENCE_INVALID"
    views = receipt.get("views")
    if not isinstance(views, dict) or set(views) != set(REVIEW_VIEWS):
        return None, "AGENT_REVIEW_VIEWS_INVALID"
    if any(value not in {"pass", "not_applicable"} for value in views.values()):
        return None, "AGENT_REVIEW_NOT_PASS"
    if receipt.get("decision") != "pass" or not isinstance(receipt.get("findings"), list):
        return None, "AGENT_REVIEW_NOT_PASS"
    return receipt, ""
