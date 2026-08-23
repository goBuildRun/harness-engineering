#!/usr/bin/env python3
"""Bounded reads and digests for external gate dependencies."""
from __future__ import annotations

import hashlib
from pathlib import Path

from harness_gate_manifest import CandidateManifest
from harness_runtime import canonical_digest


def external_dependency_digest(path: Path, manifest: CandidateManifest) -> str:
    manifest.check_deadline()
    content = manifest.read_external(path)
    if content is None:
        return "absent"
    return canonical_digest({
        "path": str(path), "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    })


def dependency_text(
    path: Path, harness: Path, product: Path, manifest: CandidateManifest,
) -> str:
    if path.is_relative_to(harness):
        return manifest.read_text(path, root=harness)
    if path.is_relative_to(product):
        return manifest.read_text(path, root=product)
    raw = manifest.read_external(path)
    if raw is None:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def dependency_entries(
    seen: set[Path], direct: dict[str, str], harness: Path, product: Path,
    manifest: CandidateManifest,
) -> list[tuple[str, str]]:
    entries = []
    for path in sorted(seen):
        if path.is_relative_to(harness):
            label = str(path.relative_to(harness))
            digest = manifest.digest(path, root=harness)
        elif path.is_relative_to(product):
            label = f"product:{path.relative_to(product)}"
            digest = manifest.digest(path, root=product)
        else:
            label = f"external:{path}"
            digest = external_dependency_digest(path, manifest)
        entries.append((label, digest))
    return sorted(entries + list(direct.items()))
