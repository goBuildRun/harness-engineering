#!/usr/bin/env python3
"""Evaluate live enforcement probes and merge/release lifecycle receipts."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


TERMINAL_STATUSES = {
    "done", "closed", "complete", "completed", "implemented", "released",
    "mr_merged", "qa_passed", "已完成", "已实现", "已发布",
}


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def evaluate_enforcement(snapshot: dict[str, Any] | None, *, repository: str,
                         branch: str, required_check: str) -> dict[str, Any]:
    data = snapshot or {}
    blockers: list[str] = []
    if data.get("schema_version") != 1:
        blockers.append("PROBE_SCHEMA_INVALID")
    if data.get("source") != "live":
        blockers.append("PROBE_NOT_LIVE")
    if data.get("repository") != repository or data.get("target_branch") != branch:
        blockers.append("PROBE_TARGET_MISMATCH")
    if data.get("required_check") != required_check:
        blockers.append("PROBE_CHECK_MISMATCH")
    target_commit = str(data.get("target_commit_sha") or "")
    if len(target_commit) != 40 or any(char not in "0123456789abcdefABCDEF" for char in target_commit):
        blockers.append("TARGET_COMMIT_INVALID")
    expires = _parse_time(str(data.get("expires_at") or ""))
    if not expires or expires <= datetime.now(timezone.utc):
        blockers.append("PROBE_EXPIRED")
    if not data.get("ci_shared_judger"):
        blockers.append("CI_SHARED_JUDGER_MISSING")
    if required_check not in (data.get("branch_required_checks") or []):
        blockers.append("BRANCH_REQUIRED_CHECK_MISSING")
    if required_check not in (data.get("release_dependencies") or []):
        blockers.append("RELEASE_DEPENDENCY_MISSING")
    release_run = data.get("release_dependency_run") or {}
    if (release_run.get("event") != "workflow_run"
            or release_run.get("status") != "completed"
            or release_run.get("conclusion") != "success"):
        blockers.append("RELEASE_WORKFLOW_RUN_MISSING")
    elif release_run.get("head_sha") != target_commit:
        blockers.append("RELEASE_RUN_SUBJECT_MISMATCH")
    if not data.get("provider_done_guard"):
        blockers.append("PROVIDER_DONE_GUARD_MISSING")
    provider_run = data.get("provider_completion_run") or {}
    if (provider_run.get("event") != "workflow_run"
            or provider_run.get("status") != "completed"
            or provider_run.get("conclusion") != "success"):
        blockers.append("PROVIDER_COMPLETION_RUN_MISSING")
    elif provider_run.get("head_sha") != target_commit:
        blockers.append("PROVIDER_RUN_SUBJECT_MISMATCH")
    return {
        "enforcement": "enforced" if not blockers else "shadow",
        "notice": "ENFORCED" if not blockers else "NOT_ENFORCED",
        "blockers": blockers,
        "probe_digest": _digest(data),
        "probed_at": data.get("probed_at", ""),
    }


def validate_lifecycle_receipt(receipt: dict[str, Any] | None, *,
                               work_item_id: str, ci: bool,
                               required_check: str = "harness-commit-acceptance",
                               repository: str = "", commit_sha: str = "",
                               run_id: str = "") -> tuple[bool, str]:
    data = receipt or {}
    if not ci:
        return False, "PROVIDER_TERMINAL_STATUS_FORBIDDEN: terminal status requires CI"
    if data.get("source") != "live" or data.get("event") not in {"merge", "release"}:
        return False, "PROVIDER_LIFECYCLE_RECEIPT_INVALID: live merge/release event required"
    if data.get("work_item_id") != work_item_id:
        return False, "PROVIDER_LIFECYCLE_RECEIPT_MISMATCH: work item"
    for field in ("task_id", "policy_digest", "binding_digest", "result_digest"):
        value = str(data.get(field) or "")
        if (not value or (field.endswith("digest")
                          and (len(value) != 64 or any(char not in "0123456789abcdefABCDEF" for char in value)))):
            return False, f"PROVIDER_LIFECYCLE_RECEIPT_INVALID: {field}"
    commit = str(data.get("commit_sha") or "")
    if len(commit) != 40 or any(char not in "0123456789abcdefABCDEF" for char in commit):
        return False, "PROVIDER_LIFECYCLE_RECEIPT_INVALID: commit SHA"
    if data.get("check_status") != "success" or data.get("required_check") != required_check:
        return False, "PROVIDER_LIFECYCLE_RECEIPT_INVALID: required check not successful"
    if repository and data.get("repository") != repository:
        return False, "PROVIDER_LIFECYCLE_RECEIPT_MISMATCH: repository"
    if commit_sha and commit != commit_sha:
        return False, "PROVIDER_LIFECYCLE_RECEIPT_MISMATCH: commit"
    if run_id and str(data.get("run_id") or "") != run_id:
        return False, "PROVIDER_LIFECYCLE_RECEIPT_MISMATCH: run"
    expires = _parse_time(str(data.get("expires_at") or ""))
    if not expires or expires <= datetime.now(timezone.utc):
        return False, "PROVIDER_LIFECYCLE_RECEIPT_EXPIRED"
    return True, "PROVIDER_LIFECYCLE_RECEIPT_OK"


def trusted_ci_context(env: Mapping[str, str]) -> tuple[bool, str, str, str]:
    if str(env.get("GITHUB_ACTIONS") or "").lower() == "true":
        commit = str(env.get("HARNESS_CI_COMMIT_SHA") or env.get("GITHUB_SHA") or "")
        return True, str(env.get("GITHUB_REPOSITORY") or ""), commit, str(env.get("GITHUB_RUN_ID") or "")
    if str(env.get("GITLAB_CI") or "").lower() == "true":
        return True, str(env.get("CI_PROJECT_PATH") or ""), str(env.get("CI_COMMIT_SHA") or ""), str(env.get("CI_PIPELINE_ID") or "")
    return False, "", "", ""


def load_snapshot(product: Path) -> dict[str, Any] | None:
    path = product / "harness-workspace" / "runs" / "enforcement.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_snapshot(product: Path, snapshot: dict[str, Any]) -> Path:
    path = product / "harness-workspace" / "runs" / "enforcement.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def git_identity(repo: Path) -> tuple[str, str]:
    try:
        remote = subprocess.check_output(
            ["git", "remote", "get-url", "origin"], cwd=repo, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=repo, text=True,
            stderr=subprocess.DEVNULL,
        ).strip() or "main"
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "", "main"
    if "://" in remote:
        parsed = urllib.parse.urlparse(remote)
        host, path = parsed.hostname or "", parsed.path
    else:
        authority, separator, path = remote.partition(":")
        host = authority.rsplit("@", 1)[-1] if separator else ""
    parts = path.removesuffix(".git").strip("/").split("/")
    repository = "/".join(parts) if host in {"github.com", "ssh.github.com"} and len(parts) == 2 else ""
    return repository, branch


def _latest_workflow_run(repository: str, workflow: str, token: str) -> dict[str, Any]:
    url = (f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/runs"
           "?event=workflow_run&status=completed&per_page=1")
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json",
                      "Authorization": f"Bearer {token}",
                      "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        runs = json.loads(response.read().decode("utf-8")).get("workflow_runs") or []
    if not runs:
        return {}
    latest = runs[0]
    return {key: latest.get(key) for key in (
        "id", "event", "status", "conclusion", "head_sha", "updated_at",
    )}


def _remote_file(repository: str, path: str, ref: str, token: str) -> str:
    encoded_path = urllib.parse.quote(path, safe="/")
    encoded_ref = urllib.parse.quote(ref, safe="")
    url = f"https://api.github.com/repos/{repository}/contents/{encoded_path}?ref={encoded_ref}"
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json",
                      "Authorization": f"Bearer {token}",
                      "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
        raise ValueError(f"GITHUB_WORKFLOW_CONTENT_INVALID: {path}")
    return base64.b64decode(payload["content"], validate=False).decode("utf-8")


def probe_github(product: Path, *, repository: str, branch: str,
                 required_check: str, credential: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repository}/branches/{branch}/protection/required_status_checks"
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {credential}",
                      "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        protection = json.loads(response.read().decode("utf-8"))
    contexts = protection.get("contexts") or [item.get("context") for item in protection.get("checks") or []]
    commit_url = (f"https://api.github.com/repos/{repository}/commits/"
                  f"{urllib.parse.quote(branch, safe='')}")
    commit_request = urllib.request.Request(
        commit_url, headers={"Accept": "application/vnd.github+json",
                             "Authorization": f"Bearer {credential}",
                             "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib.request.urlopen(commit_request, timeout=20) as response:
        target_commit = str(json.loads(response.read().decode("utf-8")).get("sha") or "")
    workflow_text = _remote_file(
        repository, ".github/workflows/harness-required.yml", target_commit, credential)
    release_text = _remote_file(repository, ".github/workflows/release.yml", target_commit, credential)
    provider_text = _remote_file(
        repository, ".github/workflows/harness-provider-complete.yml", target_commit, credential)
    now = datetime.now(timezone.utc)
    release_guard = (
        "workflow_run:" in release_text and 'workflows: ["Harness Required"]' in release_text
        and "workflow_run.conclusion == 'success'" in release_text
        and "workflow_run.conclusion != 'success'" in release_text
        and "Reject unsuccessful required run" in release_text
        and "listPullRequestsAssociatedWithCommit" in release_text
        and "merge_commit_sha === run.head_sha" in release_text
        and "item.base.ref === targetBranch" in release_text
        and "release_eligibility.py" in release_text
        and "--result harness-artifacts/harness-result.json" in release_text
        and "run-id: ${{ github.event.workflow_run.id }}" in release_text
        and '--commit "$VERIFIED_COMMIT"' in release_text
        and '--repository "$REPOSITORY"' in release_text
        and '--required-run-id "$REQUIRED_RUN_ID"' in release_text
        and "release-eligibility.json" in release_text
        and "actions/upload-artifact@v4" in release_text
        and "if-no-files-found: error" in release_text
    )
    provider_guard = (
        "--lifecycle-receipt" in provider_text
        and "workflow_run:" in provider_text and 'workflows: ["Harness Required"]' in provider_text
        and "workflow_run.conclusion == 'success'" in provider_text
        and "workflow_run.conclusion != 'success'" in provider_text
        and "Reject unsuccessful required run" in provider_text
        and "listPullRequestsAssociatedWithCommit" in provider_text
        and "merge_commit_sha === run.head_sha" in provider_text
        and "item.base.ref === targetBranch" in provider_text
        and "required_run_id', String(run.id)" in provider_text
        and "d['decision']=='pass'" in provider_text
    )
    release_run = _latest_workflow_run(repository, "release.yml", credential) if release_guard else {}
    provider_run = (_latest_workflow_run(repository, "harness-provider-complete.yml", credential)
                    if provider_guard else {})
    return {
        "schema_version": 1, "source": "live", "repository": repository,
        "target_branch": branch, "target_commit_sha": target_commit,
        "required_check": required_check,
        "ci_shared_judger": (
            "harness_runtime.py" in workflow_text
            and "ci-check" in workflow_text
            and "Reject closed unmerged pull request" in workflow_text
            and "github.event.pull_request.merged != true" in workflow_text
        ),
        "branch_required_checks": [str(item) for item in contexts if item],
        "release_dependencies": [required_check] if release_guard else [],
        "release_dependency_run": release_run,
        "provider_done_guard": provider_guard,
        "provider_completion_run": provider_run,
        "probed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def probe_authority(*, url: str, token: str, repository: str,
                    branch: str, required_check: str) -> dict[str, Any]:
    """Request a live snapshot from a platform-independent acceptance authority."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("AUTHORITY_PROBE_HTTPS_REQUIRED")
    if not token:
        raise ValueError("AUTHORITY_PROBE_TOKEN_REQUIRED")
    request = urllib.request.Request(
        url,
        data=json.dumps({
            "schema_version": 1,
            "repository": repository,
            "target_branch": branch,
            "required_check": required_check,
        }, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("AUTHORITY_PROBE_RESPONSE_INVALID")
    snapshot = payload.get("snapshot", payload)
    if not isinstance(snapshot, dict):
        raise ValueError("AUTHORITY_PROBE_RESPONSE_INVALID")
    snapshot = dict(snapshot)
    snapshot["source"] = "live"
    snapshot["probe_transport"] = "https-authority"
    return snapshot


def probe_live(product: Path, *, authority_url: str, credentials: Mapping[str, str],
               repository: str, branch: str,
               required_check: str) -> dict[str, Any]:
    if authority_url:
        return probe_authority(
            url=authority_url, token=str(credentials.get("authority") or ""), repository=repository,
            branch=branch, required_check=required_check,
        )
    github_token = str(credentials.get("github") or "")
    if not github_token or not repository:
        raise ValueError("GITHUB_LIVE_PROBE_CREDENTIALS_MISSING")
    return probe_github(
        product, repository=repository, branch=branch,
        required_check=required_check, credential=github_token,
    )


def _digest(value: Any) -> str:
    import hashlib

    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
