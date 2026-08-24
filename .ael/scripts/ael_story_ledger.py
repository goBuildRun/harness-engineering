#!/usr/bin/env python3
"""Fail-closed reader for the privacy-safe Story ledger."""
from __future__ import annotations

import fcntl
import json
from pathlib import Path
from typing import Any


SCHEMA = "harness-story-wall-clock-v1"


def valid_event(item: Any) -> bool:
    if (
        not isinstance(item, dict)
        or item.get("schema") != SCHEMA
        or item.get("event") not in {"start", "end"}
        or not isinstance(item.get("task_id"), str)
        or not item.get("task_id")
        or not isinstance(item.get("stage"), str)
        or not item.get("stage")
        or not isinstance(item.get("span_id"), str)
        or not item.get("span_id")
        or not isinstance(item.get("attempt"), int)
        or isinstance(item.get("attempt"), bool)
        or item["attempt"] < 1
        or not isinstance(item.get("epoch_ms"), int)
        or isinstance(item.get("epoch_ms"), bool)
        or item["epoch_ms"] < 0
    ):
        return False
    for field in ("wall_ms", "budget_ms"):
        value = item.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            return False
    if item["event"] == "end" and (
        not isinstance(item.get("wall_ms"), int)
        or isinstance(item.get("wall_ms"), bool)
        or item["wall_ms"] < 0
        or item.get("decision") not in {"pass", "block"}
    ):
        return False
    return True


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
            raw = stream.read()
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except (OSError, UnicodeDecodeError) as exc:
        raise OSError("STORY_LEDGER_INVALID") from exc
    if raw and not raw.endswith("\n"):
        raise OSError("STORY_LEDGER_INVALID")
    try:
        events = [json.loads(line) for line in raw.splitlines()]
    except json.JSONDecodeError as exc:
        raise OSError("STORY_LEDGER_INVALID") from exc
    if any(not valid_event(item) for item in events):
        raise OSError("STORY_LEDGER_INVALID")
    return events
