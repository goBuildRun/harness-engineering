"""CLI adapter for the importable planning API."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from ael_output import dump_json
from ael_planning import plan_batch
from workspace_paths import load_layout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ael-root", default=".")
    parser.add_argument("--product-root", default=os.environ.get("AEL_PRODUCT_ROOT", os.getcwd()))
    parser.add_argument("--level", choices=("L1", "L2", "L3"), required=True)
    parser.add_argument("--task-dir", action="append", required=True)
    parser.add_argument("--batch-id", default="")
    parser.add_argument("--provider-mode", choices=("offline", "configured"), default="offline")
    args = parser.parse_args()
    layout = load_layout(Path(args.ael_root).resolve(), Path(args.product_root).resolve())
    result = plan_batch(layout, level=args.level, task_dirs=args.task_dir,
                        batch_id=args.batch_id, provider_mode=args.provider_mode)
    dump_json(result)
    return 0 if result.get("decision") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
