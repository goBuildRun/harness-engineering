#!/usr/bin/env python3
"""qa_write_evidence.py — 写入 QA 签章 JSON 凭证。"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 8:
        print("USAGE: qa_write_evidence.py <path> <task_id> <work_item_id> <decision> <ts> <summary> <paths_json> [binding_json]", file=sys.stderr)
        return 1

    path, task_id, work_item_id, decision, ts, summary, paths_json = sys.argv[1:8]
    try:
        paths = json.loads(paths_json)
    except json.JSONDecodeError:
        paths = []

    binding = {}
    if len(sys.argv) >= 9:
        try:
            value = json.loads(sys.argv[8])
            binding = value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            binding = {}
    payload = {
        "task_id": task_id,
        "work_item_id": work_item_id,
        "decision": decision,
        "reviewer": "qa-evaluator",
        "timestamp": ts,
        "summary": summary or "",
        "paths_reviewed": paths,
        "structure_gate": "pass" if decision == "pass" else "n/a",
        "findings": [],
        **binding,
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
