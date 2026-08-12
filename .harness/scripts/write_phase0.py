#!/usr/bin/env python3
"""Write planning gate credentials.

Compatibility: this script keeps the historical name write_phase0.py and still
writes phase0_pass.json beside the new planning_gate_pass.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 6:
        script_name = Path(sys.argv[0]).name or "write_planning_gate.py"
        print(
            f"USAGE: {script_name} <workspace_path> <level> <ts> <task_dir> <product_root> [work_item_json]",
            file=sys.stderr,
        )
        return 1

    workspace_path, level, ts, task_dir, product_root = sys.argv[1:6]
    work_item_raw = sys.argv[6] if len(sys.argv) > 6 else ""
    work_item = None
    if work_item_raw and work_item_raw not in ("null", "None", ""):
        try:
            work_item = json.loads(work_item_raw)
        except json.JSONDecodeError:
            work_item = None

    planning_root = ""
    try:
        script_dir = Path(__file__).resolve().parent
        harness_root = script_dir.parent.parent
        sys.path.insert(0, str(script_dir))
        from workspace_paths import load_layout

        layout = load_layout(harness_root, Path(product_root))
        planning_root = layout.rel(layout.planning_root)
    except Exception:
        pass

    payload: dict = {
        "decision": "pass",
        "gate": "planning",
        "level": level,
        "timestamp": ts,
        "task_dir": task_dir or None,
        "product_root": product_root,
        "planning_root": planning_root or None,
        "phase0_root": planning_root or None,
        "reviewer": "planning_gate",
        "legacy_reviewer": "bmad_entry_gate",
    }
    if work_item:
        payload["work_item"] = work_item

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    ws = Path(workspace_path)
    ws.parent.mkdir(parents=True, exist_ok=True)
    ws.write_text(text, encoding="utf-8")
    for sibling in ("planning_gate_pass.json", "phase0_pass.json"):
        target = ws.parent / sibling
        if target != ws:
            target.write_text(text, encoding="utf-8")

    if task_dir:
        task_root = Path(task_dir)
        if task_root.is_dir():
            (task_root / "planning_gate_pass.json").write_text(text, encoding="utf-8")
            (task_root / "phase0_pass.json").write_text(text, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
