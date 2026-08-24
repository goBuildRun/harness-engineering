#!/usr/bin/env python3
"""Shared JSON output helpers for buildrun-agent-engineering-lifecycle scripts."""
from __future__ import annotations

import json
import os
import sys
from typing import Any


FALSE_VALUES = {"0", "false", "no", "off", "raw"}
_LAST_DECISION = ""


def pretty_enabled() -> bool:
    value = os.environ.get("AEL_PRETTY", "1").strip().lower()
    return sys.stdout.isatty() and value not in FALSE_VALUES


def dump_json(payload: Any) -> None:
    global _LAST_DECISION
    if isinstance(payload, dict):
        _LAST_DECISION = str(payload.get("decision") or "")
    indent = 2 if pretty_enabled() else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))


def reset_decision() -> None:
    global _LAST_DECISION
    _LAST_DECISION = ""


def decision_exit_code(fallback: int = 0) -> int:
    if _LAST_DECISION == "pass":
        return 0
    if _LAST_DECISION == "block":
        return 1
    return fallback


def emit(decision: str, reason: str, **extra: Any) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})
