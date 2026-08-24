#!/usr/bin/env python3
"""Validation shared by local and signed independent GC receipts."""
from __future__ import annotations

from typing import Any


ADJUDICATION_DECISIONS = {
    "deferred", "non_actionable_observation", "remediated", "retain",
}


def valid_mechanical_adjudication(gc_result: dict[str, Any],
                                  mechanical: dict[str, Any] | None) -> bool:
    if not mechanical or mechanical.get("decision") != "block":
        return True
    triggers = mechanical.get("triggers")
    if not isinstance(triggers, list) or not triggers or not all(
        isinstance(trigger, str) and trigger for trigger in triggers
    ):
        return False
    adjudications = gc_result.get("mechanical_adjudication")
    if not isinstance(adjudications, list):
        return False
    reviewed: list[str] = []
    for item in adjudications:
        if not isinstance(item, dict):
            return False
        trigger = item.get("trigger")
        if (
            not isinstance(trigger, str)
            or trigger not in triggers
            or item.get("decision") not in ADJUDICATION_DECISIONS
            or not isinstance(item.get("reason"), str)
            or not item["reason"].strip()
        ):
            return False
        reviewed.append(trigger)
    return len(reviewed) == len(set(reviewed)) and set(reviewed) == set(triggers)
