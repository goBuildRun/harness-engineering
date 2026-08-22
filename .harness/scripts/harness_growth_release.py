#!/usr/bin/env python3
"""Release-only view of Growth captures; long-term review stays off the critical path."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from harness_output import dump_json
from workspace_paths import load_active_planning_gate, load_layout


IMPACT_RE = re.compile(r"^- \*\*Release impact\*\*[：:]\s*(blocker|followup|none)\s*$", re.MULTILINE)


def _bound(path: Path, work_item_id: str) -> bool:
    if not work_item_id:
        return True
    if work_item_id in path.name:
        return True
    try:
        return f"**Work Item**：`{work_item_id}`" in path.read_text(encoding="utf-8", errors="ignore")[:1600]
    except OSError:
        return False


def release_status(progress_dir: Path, work_item_id: str = "") -> dict[str, object]:
    captures = sorted(progress_dir.glob("*-GROWTH-CAPTURE.md")) if progress_dir.is_dir() else []
    captures = [path for path in captures if _bound(path, work_item_id)]
    blockers, followups, unknown = [], [], []
    for path in captures:
        try:
            match = IMPACT_RE.search(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            match = None
        ref = path.name
        if match is None:
            unknown.append(ref)
        elif match.group(1) == "blocker":
            blockers.append(ref)
        elif match.group(1) == "followup":
            followups.append(ref)
    if blockers:
        decision, reason = "block", "GROWTH_RELEASE_BLOCKER"
    elif unknown:
        decision, reason = "block", "GROWTH_RELEASE_IMPACT_UNKNOWN"
    else:
        decision, reason = "pass", "GROWTH_RELEASE_CLEAR"
    return {
        "decision": decision, "reason": reason, "work_item_id": work_item_id,
        "captures": len(captures), "blockers": blockers,
        "followups_pending_review": followups, "unknown": unknown,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=".")
    parser.add_argument("--product-root", required=True)
    args = parser.parse_args()
    layout = load_layout(Path(args.harness_root).resolve(), Path(args.product_root).resolve())
    gate = load_active_planning_gate(layout) or {}
    work_item = gate.get("work_item") or {}
    work_item_id = str(work_item.get("id") or "") if isinstance(work_item, dict) else ""
    result = release_status(layout.progress_dir, work_item_id)
    dump_json(result)
    return 0 if result["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
