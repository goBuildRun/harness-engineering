#!/usr/bin/env python3
"""Validated, bounded browser QA scenario steps."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ALLOWED_ACTIONS = {
    "click": {"action", "selector"},
    "fill": {"action", "selector", "value"},
    "press": {"action", "selector", "key"},
    "expect-visible": {"action", "selector"},
    "expect-text": {"action", "selector", "text"},
    "wait-for-url": {"action", "url"},
}


def is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def load_scenario(product_root: Path, raw: str, allow_external: bool = False) -> tuple[list[dict[str, str]], str]:
    if not raw:
        return [], "BROWSER_QA_SCENARIO_REQUIRED"
    path = Path(raw)
    if not path.is_absolute():
        path = product_root / path
    path = path.resolve()
    if not allow_external and not is_under(path, product_root):
        return [], "BROWSER_QA_SCENARIO_OUTSIDE_PRODUCT"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], f"BROWSER_QA_SCENARIO_INVALID: {type(exc).__name__}"
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list) or not steps or len(steps) > 50:
        return [], "BROWSER_QA_SCENARIO_STEPS_INVALID"
    normalized: list[dict[str, str]] = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            return [], f"BROWSER_QA_SCENARIO_STEP_INVALID:{index}"
        action = str(step.get("action") or "")
        allowed = ALLOWED_ACTIONS.get(action)
        if not allowed or set(step) != allowed or any(not str(step.get(key) or "").strip() for key in allowed):
            return [], f"BROWSER_QA_SCENARIO_STEP_INVALID:{index}"
        normalized.append({key: str(step[key]) for key in allowed})
    return normalized, ""


def run_scenario(page: Any, steps: list[dict[str, str]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        action = step["action"]
        locator = page.locator(step["selector"]).first if "selector" in step else None
        if action == "click":
            locator.click(timeout=10_000)
        elif action == "fill":
            locator.fill(step["value"], timeout=10_000)
        elif action == "press":
            locator.press(step["key"], timeout=10_000)
        elif action == "expect-visible":
            locator.wait_for(state="visible", timeout=10_000)
        elif action == "expect-text":
            locator.filter(has_text=step["text"]).wait_for(state="visible", timeout=10_000)
        elif action == "wait-for-url":
            page.wait_for_url(step["url"], timeout=10_000)
        results.append({"index": index, "action": action, "decision": "pass"})
    return results
