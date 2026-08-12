#!/usr/bin/env python3
"""Shared JSON output helpers for harness-engineering scripts."""
from __future__ import annotations

import json
import os
import sys
from typing import Any


FALSE_VALUES = {"0", "false", "no", "off", "raw"}


def pretty_enabled() -> bool:
    value = os.environ.get("HARNESS_PRETTY", "1").strip().lower()
    return sys.stdout.isatty() and value not in FALSE_VALUES


def dump_json(payload: Any) -> None:
    indent = 2 if pretty_enabled() else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))


def emit(decision: str, reason: str, **extra: Any) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})
