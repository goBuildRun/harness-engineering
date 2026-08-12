#!/usr/bin/env python3
"""Extract a unique Harness task binding from a GitHub pull request event."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from harness_output import dump_json


FIELD_RE = re.compile(r"^Harness-(Task|Scope|Tier):\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)


def parse_binding(body: str) -> dict[str, str]:
    values: dict[str, list[str]] = {}
    for name, value in FIELD_RE.findall(body or ""):
        values.setdefault(name.lower(), []).append(value.strip())
    duplicates = [name for name, entries in values.items() if len(entries) != 1]
    if duplicates:
        raise ValueError(f"CI_BINDING_AMBIGUOUS: {','.join(sorted(duplicates))}")
    task_id = next(iter(values.get("task", [])), "")
    scope = next(iter(values.get("scope", [])), "")
    tier = next(iter(values.get("tier", [])), "standard").lower()
    if not task_id or not scope:
        raise ValueError("CI_BINDING_MISSING: Harness-Task and Harness-Scope are required")
    if tier not in {"lite", "standard", "strict"}:
        raise ValueError("CI_BINDING_TIER_INVALID")
    if any(token in scope for token in ("..", "*", "~", "\\")) or scope.startswith("/"):
        raise ValueError("CI_BINDING_SCOPE_INVALID")
    return {"task_id": task_id, "scope": scope.rstrip("/"), "tier": tier}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--github-output", default="")
    args = parser.parse_args()
    try:
        event = json.loads(Path(args.event).read_text(encoding="utf-8"))
        binding = parse_binding(str((event.get("pull_request") or {}).get("body") or ""))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        dump_json({"decision": "block", "reason": str(exc)})
        return 1
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as stream:
            for key, value in binding.items():
                stream.write(f"{key}={value}\n")
    dump_json({"decision": "pass", "reason": "CI_BINDING_OK", "binding": binding})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
