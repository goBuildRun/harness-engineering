#!/usr/bin/env python3
"""Read locally bound open Work Item identifiers from product specs."""
from __future__ import annotations

import re
from pathlib import Path


def local_open_work_item_ids(product_root: Path | None, id_pattern: str) -> list[str]:
    if not product_root or not product_root.is_dir():
        return []
    spec_dir = product_root / "harness-workspace/planning/product-specs"
    if not spec_dir.is_dir():
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for spec in sorted(spec_dir.glob("*.md")):
        try:
            lines = spec.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.startswith("- [ ]"):
                continue
            for match in re.finditer(r"#([A-Za-z0-9][A-Za-z0-9._:-]{1,127})\b", line):
                item_id = match.group(1)
                if item_id in seen or not re.fullmatch(id_pattern, item_id.strip()):
                    continue
                ids.append(item_id)
                seen.add(item_id)
    return ids
