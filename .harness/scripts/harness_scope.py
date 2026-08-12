#!/usr/bin/env python3
"""Task scope matching shared by local and CI Harness decisions."""
from __future__ import annotations


def paths_within_scope(paths: list[str], scopes: list[str] | set[str]) -> bool:
    normalized = {scope.rstrip("/") or "." for scope in scopes}
    if "." in normalized:
        return True
    return all(
        any(path == scope or path.startswith(scope + "/") for scope in normalized)
        for path in paths
    )
