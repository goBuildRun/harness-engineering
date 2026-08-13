#!/usr/bin/env python3
"""Bind cumulative agent usage endpoints into exact per-story deltas."""
from __future__ import annotations

from typing import Any


def _endpoint(receipt: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(receipt, dict):
        return None
    usage = receipt.get("implementation")
    source = receipt.get("source")
    if not isinstance(usage, dict) or not isinstance(source, dict):
        return None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    session_id = str(source.get("session_id") or "").strip()
    if (not session_id or not isinstance(input_tokens, int) or isinstance(input_tokens, bool)
            or not isinstance(output_tokens, int) or isinstance(output_tokens, bool)
            or input_tokens < 0 or output_tokens < 0):
        return None
    return {
        "session_id": session_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "usage_event_at": str(source.get("usage_event_at") or ""),
        "rollout_sha256": str(source.get("rollout_sha256") or ""),
    }


def capture_usage_baseline(result: dict[str, Any], receipt: dict[str, Any] | None) -> bool:
    endpoint = _endpoint(receipt)
    if endpoint is None:
        return False
    result.setdefault("cost", {})["story_usage_baseline"] = endpoint
    return True


def apply_story_usage(result: dict[str, Any], receipt: dict[str, Any] | None) -> bool:
    cost = result.setdefault("cost", {})
    end = _endpoint(receipt)
    start = cost.get("story_usage_baseline")
    story = {"status": "endpoint_missing", "input_tokens": "unknown", "output_tokens": "unknown"}
    if end is None:
        cost["story"] = story
        return False
    story["end"] = end
    if not isinstance(start, dict):
        story["status"] = "baseline_missing"
    elif start.get("session_id") != end["session_id"]:
        story["status"] = "session_mismatch"
        story["start"] = start
    elif (end["input_tokens"] < start.get("input_tokens", 0)
          or end["output_tokens"] < start.get("output_tokens", 0)):
        story["status"] = "counter_reset"
        story["start"] = start
    else:
        story.update({
            "status": "exact",
            "input_tokens": end["input_tokens"] - start["input_tokens"],
            "output_tokens": end["output_tokens"] - start["output_tokens"],
            "start": start,
        })
    cost["story"] = story
    return story["status"] == "exact"


def aggregate_epic_usage(epic_id: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    exact = []
    incomplete = 0
    for result in results:
        if str((result.get("task") or {}).get("epic_id") or "") != epic_id:
            continue
        story = (result.get("cost") or {}).get("story") or {}
        if (story.get("status") == "exact" and isinstance(story.get("input_tokens"), int)
                and isinstance(story.get("output_tokens"), int)):
            exact.append(story)
        else:
            incomplete += 1
    return {
        "epic_id": epic_id,
        "input_tokens": sum(item["input_tokens"] for item in exact),
        "output_tokens": sum(item["output_tokens"] for item in exact),
        "exact_story_count": len(exact),
        "incomplete_story_count": incomplete,
        "complete": incomplete == 0,
    }
