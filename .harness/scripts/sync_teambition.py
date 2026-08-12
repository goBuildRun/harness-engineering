#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[DEPRECATED] 请使用: bash .harness/scripts/work_item.sh sync-spec <markdown> [--assignee <id>]"""
import subprocess
import sys
from pathlib import Path

def main() -> None:
    print(
        "[DEPRECATED] sync_teambition.py → work_item.sh sync-spec（可替换协同系统）",
        file=sys.stderr,
    )
    if len(sys.argv) < 2:
        print("Usage: sync_teambition.py <markdown> [--assignee <id>]  # 转发至 work_item.sh sync-spec")
        sys.exit(1)
    script = Path(__file__).resolve().parent / "work_item.sh"
    subprocess.run(["bash", str(script), "sync-spec", *sys.argv[1:]], check=False)

if __name__ == "__main__":
    main()
