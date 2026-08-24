#!/usr/bin/env python3
"""Freeze code/planning inputs separately from mutable release evidence."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ael_runtime import canonical_digest, now
from ael_candidate_git import commit_entry, commit_paths, resolve_commit


SCHEMA = "harness-candidate-snapshot-v2"
EVIDENCE_PREFIXES = (
    "ael-workspace/evidence/",
    "ael-workspace/runs/",
)
SNAPSHOT_FIELDS = {
    "schema", "captured_at", "baseline_commit", "candidate_paths", "evidence_paths",
    "candidate_entries", "evidence_entries", "candidate_digest", "evidence_digest",
    "snapshot_digest",
}
ENTRY_FIELDS = {"path", "kind", "mode", "sha256"}
_SNAPSHOT_EVIDENCE = object()


def _is_evidence_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return any(normalized.startswith(prefix) for prefix in EVIDENCE_PREFIXES)


def _entry(repo: Path, rel: str) -> dict[str, str]:
    path = repo / rel
    if path.is_symlink():
        return {
            "path": rel,
            "kind": "symlink",
            "mode": "120000",
            "sha256": hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest(),
        }
    if path.is_file():
        mode = "100755" if path.stat().st_mode & stat.S_IXUSR else "100644"
        return {
            "path": rel,
            "kind": "file",
            "mode": mode,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return {"path": rel, "kind": "absent", "mode": "000000", "sha256": "absent"}


def _entry_digest(entries: list[dict[str, str]]) -> str:
    return canonical_digest([
        (item["path"], item["kind"], item["mode"], item["sha256"])
        for item in entries
    ])


def _candidate_digest(entries: list[dict[str, str]]) -> str:
    """Match Git attestation subjects while deriving only from frozen entries."""
    return canonical_digest([
        (item["path"], item["sha256"])
        for item in entries
    ])


def candidate_paths(paths: list[str]) -> list[str]:
    return sorted(path for path in set(paths) if not _is_evidence_path(path))


def release_evidence_paths(changed_files: list[str], result_ref: str) -> list[str]:
    """Exclude mutable runtime control records from release evidence inputs."""
    excluded = result_ref.replace("\\", "/").lstrip("./")
    control_records = {
        excluded,
        str(Path(excluded).with_name("wall-clock-ledger.jsonl")),
    }
    return sorted(
        path for path in set(changed_files)
        if path.replace("\\", "/").lstrip("./") not in control_records
    )


def _binding_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": snapshot.get("schema"),
        "captured_at": snapshot.get("captured_at"),
        "baseline_commit": snapshot.get("baseline_commit"),
        "candidate_paths": snapshot.get("candidate_paths"),
        "evidence_paths": snapshot.get("evidence_paths"),
        "candidate_entries": snapshot.get("candidate_entries"),
        "evidence_entries": snapshot.get("evidence_entries"),
        "candidate_digest": snapshot.get("candidate_digest"),
        "evidence_digest": snapshot.get("evidence_digest"),
    }


def _snapshot_digest(snapshot: dict[str, Any]) -> str:
    return canonical_digest(_binding_payload(snapshot))


def _valid_paths(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and item for item in value)
        and value == sorted(set(value))
    )


def _valid_entries(value: Any, paths: list[str]) -> bool:
    return (
        isinstance(value, list)
        and len(value) == len(paths)
        and all(
            isinstance(item, dict)
            and set(item) == ENTRY_FIELDS
            and item.get("path") == path
            and item.get("kind") in {"file", "symlink", "absent"}
            and item.get("mode") in {"100644", "100755", "120000", "000000"}
            and isinstance(item.get("sha256"), str)
            and (
                (item["kind"] == "file" and item["mode"] in {"100644", "100755"})
                or (item["kind"] == "symlink" and item["mode"] == "120000")
                or (
                    item["kind"] == "absent"
                    and item["mode"] == "000000"
                    and item["sha256"] == "absent"
                )
            )
            for item, path in zip(value, paths)
        )
    )


def _validate_snapshot(snapshot: Any) -> dict[str, str]:
    if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_FIELDS:
        return {"decision": "block", "reason": "CANDIDATE_SNAPSHOT_INVALID"}
    candidate_paths = snapshot.get("candidate_paths")
    evidence_paths = snapshot.get("evidence_paths")
    if (
        snapshot.get("schema") != SCHEMA
        or not isinstance(snapshot.get("captured_at"), str)
        or not isinstance(snapshot.get("baseline_commit"), str)
        or not snapshot["baseline_commit"]
        or not _valid_paths(candidate_paths)
        or not _valid_paths(evidence_paths)
        or set(candidate_paths).intersection(evidence_paths)
        or not _valid_entries(snapshot.get("candidate_entries"), candidate_paths)
        or not _valid_entries(snapshot.get("evidence_entries"), evidence_paths)
        or snapshot.get("candidate_digest") != _candidate_digest(snapshot["candidate_entries"])
        or snapshot.get("evidence_digest") != _entry_digest(snapshot["evidence_entries"])
    ):
        return {"decision": "block", "reason": "CANDIDATE_SNAPSHOT_INVALID"}
    if snapshot.get("snapshot_digest") != _snapshot_digest(snapshot):
        return {"decision": "block", "reason": "CANDIDATE_SNAPSHOT_DIGEST_MISMATCH"}
    return {"decision": "pass", "reason": "CANDIDATE_SNAPSHOT_VALID"}


def build_snapshot(
    repo: Path, changed_files: list[str], *, baseline_commit: str,
) -> dict[str, Any]:
    paths = sorted(set(changed_files))
    candidate_files = candidate_paths(paths)
    evidence_paths = [path for path in paths if _is_evidence_path(path)]
    candidate_entries = [_entry(repo, path) for path in candidate_files]
    evidence_entries = [_entry(repo, path) for path in evidence_paths]
    payload = {
        "schema": SCHEMA,
        "captured_at": now(),
        "baseline_commit": baseline_commit,
        "candidate_paths": candidate_files,
        "evidence_paths": evidence_paths,
        "candidate_entries": candidate_entries,
        "evidence_entries": evidence_entries,
        "candidate_digest": _candidate_digest(candidate_entries),
        "evidence_digest": _entry_digest(evidence_entries),
    }
    payload["snapshot_digest"] = _snapshot_digest(payload)
    return payload


def _result_binding(snapshot: dict[str, Any], candidate: Any) -> dict[str, str]:
    valid = (
        isinstance(candidate, dict)
        and candidate.get("schema") == snapshot.get("schema")
        and candidate.get("digest") == snapshot.get("candidate_digest")
        and candidate.get("paths") == snapshot.get("candidate_paths")
        and candidate.get("baseline_commit") == snapshot.get("baseline_commit")
        and candidate.get("snapshot_digest") == snapshot.get("snapshot_digest")
        and candidate.get("snapshot") == "candidate-snapshot.json"
        and candidate.get("frozen_at") == snapshot.get("captured_at")
    )
    return {
        "decision": "pass" if valid else "block",
        "reason": "CANDIDATE_RESULT_BOUND" if valid else "CANDIDATE_RESULT_BINDING_MISMATCH",
    }


def current_candidate(
    repo: Path, snapshot: dict[str, Any], changed_files: list[str] | None = None,
) -> dict[str, Any]:
    snapshot_status = _validate_snapshot(snapshot)
    if snapshot_status["decision"] == "block":
        return snapshot_status
    paths = snapshot["candidate_paths"]
    current_paths = (
        sorted(path for path in set(changed_files) if not _is_evidence_path(path))
        if changed_files is not None else paths
    )
    if current_paths != paths:
        return {
            "decision": "block", "reason": "CANDIDATE_PATHS_CHANGED",
            "candidate_paths": current_paths,
        }
    entries = [_entry(repo, path) for path in paths]
    digest = _candidate_digest(entries)
    current = (
        digest == snapshot.get("candidate_digest")
        and entries == snapshot.get("candidate_entries")
    )
    return {
        "decision": "pass" if current else "block",
        "reason": "CANDIDATE_CURRENT" if current else "CANDIDATE_CHANGED",
        "candidate_digest": digest,
        "candidate_entries": entries,
    }


def bound_candidate(
    repo: Path, snapshot: dict[str, Any], result_candidate: Any,
    changed_files: list[str] | None = None,
) -> dict[str, Any]:
    snapshot_status = _validate_snapshot(snapshot)
    if snapshot_status["decision"] == "block":
        return snapshot_status
    binding = _result_binding(snapshot, result_candidate)
    if binding["decision"] == "block":
        return binding
    return current_candidate(repo, snapshot, changed_files)


def committed_candidate(
    repo: Path, snapshot: dict[str, Any], result_candidate: Any, commit: str, *,
    evidence_manifest: Any = _SNAPSHOT_EVIDENCE,
) -> dict[str, Any]:
    snapshot_status = _validate_snapshot(snapshot)
    if snapshot_status["decision"] == "block":
        return snapshot_status
    binding = _result_binding(snapshot, result_candidate)
    if binding["decision"] == "block":
        return binding
    sha = resolve_commit(repo, commit)
    baseline = resolve_commit(repo, snapshot["baseline_commit"])
    if not sha:
        return {"decision": "block", "reason": "CANDIDATE_COMMIT_NOT_FOUND"}
    if not baseline:
        return {"decision": "block", "reason": "CANDIDATE_BASELINE_COMMIT_INVALID"}
    try:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", baseline, sha], cwd=repo,
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {"decision": "block", "reason": "CANDIDATE_BASELINE_COMMIT_INVALID"}
    committed_paths = commit_paths(repo, baseline, sha)
    if committed_paths is None:
        return {"decision": "block", "reason": "CANDIDATE_COMMIT_BINDING_INVALID"}
    if evidence_manifest is _SNAPSHOT_EVIDENCE:
        expected_evidence = {
            "evidence_paths": snapshot["evidence_paths"],
            "evidence_entries": snapshot["evidence_entries"],
            "evidence_digest": snapshot["evidence_digest"],
        }
    elif evidence_manifest is None:
        expected_evidence = None
    elif (
        isinstance(evidence_manifest, dict)
        and _valid_paths(evidence_manifest.get("evidence_paths"))
        and _valid_entries(
            evidence_manifest.get("evidence_entries"),
            evidence_manifest.get("evidence_paths"),
        )
        and evidence_manifest.get("evidence_digest")
        == _entry_digest(evidence_manifest["evidence_entries"])
    ):
        expected_evidence = evidence_manifest
    else:
        return {"decision": "block", "reason": "CANDIDATE_EVIDENCE_MANIFEST_INVALID"}
    committed_evidence_paths = sorted(path for path in committed_paths if _is_evidence_path(path))
    expected_evidence_paths = (
        expected_evidence["evidence_paths"] if expected_evidence is not None
        else committed_evidence_paths
    )
    if committed_evidence_paths != expected_evidence_paths:
        return {
            "decision": "block", "reason": "CANDIDATE_COMMIT_PATHS_MISMATCH",
            "candidate_paths": snapshot["candidate_paths"],
            "evidence_paths": expected_evidence_paths,
            "commit_paths": committed_paths,
        }
    committed_candidate_paths = candidate_paths(committed_paths)
    if committed_candidate_paths != snapshot["candidate_paths"]:
        return {
            "decision": "block", "reason": "CANDIDATE_COMMIT_PATHS_MISMATCH",
            "candidate_paths": snapshot["candidate_paths"], "commit_paths": committed_paths,
        }
    committed_entries = [commit_entry(repo, sha, rel) for rel in committed_candidate_paths]
    if committed_entries != snapshot["candidate_entries"]:
        return {
            "decision": "block", "reason": "CANDIDATE_COMMIT_MISMATCH",
            "commit_entries": committed_entries,
        }
    if expected_evidence is not None:
        committed_evidence_entries = [
            commit_entry(repo, sha, rel) for rel in committed_evidence_paths
        ]
        if committed_evidence_entries != expected_evidence["evidence_entries"]:
            return {
                "decision": "block", "reason": "CANDIDATE_COMMIT_EVIDENCE_MISMATCH",
                "commit_evidence_entries": committed_evidence_entries,
            }
    return {
        "decision": "pass", "reason": "CANDIDATE_COMMIT_CURRENT",
        "commit": sha, "baseline_commit": baseline,
    }


def refresh_evidence(repo: Path, changed_files: list[str]) -> dict[str, Any]:
    evidence_paths = sorted(path for path in set(changed_files) if _is_evidence_path(path))
    entries = [_entry(repo, path) for path in evidence_paths]
    return {
        "evidence_paths": evidence_paths,
        "evidence_entries": entries,
        "evidence_digest": _entry_digest(entries),
    }


def write_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(snapshot, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
