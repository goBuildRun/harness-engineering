#!/usr/bin/env python3
"""Install and audit controlled bare-Git enforcement hooks."""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

from harness_output import dump_json


def _config(repo: Path, key: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "config", "--local", "--get", key], cwd=repo, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def _valid_submodule_repository(path: str, source: Path) -> bool:
    candidate = Path(path)
    if not path or candidate.is_absolute() or ".." in candidate.parts or not source.is_absolute():
        return False
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"], cwd=source, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except (FileNotFoundError, NotADirectoryError, subprocess.CalledProcessError):
        return False


def parse_submodule_repositories(values: list[str]) -> dict[str, Path]:
    repositories: dict[str, Path] = {}
    for value in values:
        path, separator, source = value.partition("=")
        repository = Path(source).resolve() if separator and source else Path("")
        if not separator or not _valid_submodule_repository(path, repository):
            raise ValueError("ENFORCED_SUBMODULE_SOURCE_INVALID")
        repositories[path.rstrip("/")] = repository
    return repositories


def hook_script(*, post: bool = False) -> str:
    action = "post-receive" if post else "pre-receive"
    extra = (' --signing-key "$SIGNING_KEY" --receipt-dir "$RECEIPT_DIR"' if post else "")
    return f'''#!/usr/bin/env bash
set -euo pipefail
REPO="$(pwd -P)"
HARNESS_ROOT="$(git config --local --get harness.enforcedRoot)"
PROTECTED_REFS="$(git config --local --get harness.protectedRefs)"
ALLOWED_SIGNERS="$(git config --local --get harness.acceptanceAllowedSigners || true)"
SIGNING_KEY="$(git config --local --get harness.acceptanceSigningKey || true)"
RECEIPT_DIR="$(git config --local --get harness.acceptanceReceiptDir || true)"
SUBMODULE_REPOSITORIES="$(git config --local --get harness.submoduleRepositories || echo '{{}}')"
[[ -n "$HARNESS_ROOT" && -n "$PROTECTED_REFS" ]] || {{ echo 'HARNESS_ENFORCED_CONFIG_MISSING' >&2; exit 1; }}
{f'[[ -f "$SIGNING_KEY" && -d "$RECEIPT_DIR" ]] || {{ echo \'HARNESS_ACCEPTANCE_CONFIG_MISSING\' >&2; exit 1; }}' if post else ''}
HARNESS_PROTECTED_REFS="$PROTECTED_REFS" python3 "$HARNESS_ROOT/.harness/scripts/harness_receive.py" \\
  --repo "$REPO" --harness-root "$HARNESS_ROOT" --gc-allowed-signers "$ALLOWED_SIGNERS" \\
  --submodule-repositories-json "$SUBMODULE_REPOSITORIES"{extra}
echo 'HARNESS_{action.upper().replace('-', '_')}_PASS' >&2
'''


def install(repo: Path, harness: Path, *, protected_refs: tuple[str, ...], signing_key: Path,
            allowed_signers: Path, receipt_dir: Path,
            submodule_repositories: dict[str, Path] | None = None) -> dict[str, Any]:
    if _config(repo, "core.bare") != "true":
        return {"decision": "block", "reason": "ENFORCED_BARE_REPOSITORY_REQUIRED"}
    if not protected_refs or any(not ref.startswith("refs/") for ref in protected_refs):
        return {"decision": "block", "reason": "ENFORCED_PROTECTED_REFS_INVALID"}
    if not signing_key.is_file() or not allowed_signers.is_file():
        return {"decision": "block", "reason": "ENFORCED_TRUST_ROOT_MISSING"}
    receipt_dir.mkdir(parents=True, exist_ok=True)
    hooks = repo / "hooks"
    values = {
        "harness.enforcedRoot": str(harness.resolve()),
        "harness.protectedRefs": ",".join(protected_refs),
        "harness.acceptanceSigningKey": str(signing_key.resolve()),
        "harness.acceptanceAllowedSigners": str(allowed_signers.resolve()),
        "harness.acceptanceReceiptDir": str(receipt_dir.resolve()),
        "harness.submoduleRepositories": json.dumps(
            {path: str(source) for path, source in (submodule_repositories or {}).items()},
            sort_keys=True, separators=(",", ":"),
        ),
    }
    try:
        for key, value in values.items():
            subprocess.run(["git", "config", "--local", key, value], cwd=repo, check=True)
        for name, text in (("pre-receive", hook_script()), ("post-receive", hook_script(post=True))):
            target = hooks / name
            target.write_text(text, encoding="utf-8")
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except (OSError, FileNotFoundError, subprocess.CalledProcessError):
        return {"decision": "block", "reason": "ENFORCED_INSTALL_FAILED"}
    return {"decision": "pass", "reason": "ENFORCED_HOOKS_INSTALLED"}


def audit(repo: Path) -> dict[str, Any]:
    harness = Path(_config(repo, "harness.enforcedRoot"))
    signing_key = Path(_config(repo, "harness.acceptanceSigningKey"))
    allowed = Path(_config(repo, "harness.acceptanceAllowedSigners"))
    receipts = Path(_config(repo, "harness.acceptanceReceiptDir"))
    protected = tuple(filter(None, _config(repo, "harness.protectedRefs").split(",")))
    try:
        submodules = {
            path: Path(source) for path, source in json.loads(
                _config(repo, "harness.submoduleRepositories") or "{}"
            ).items()
        }
        submodules_valid = all(
            _valid_submodule_repository(path, source) for path, source in submodules.items()
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        submodules = {}
        submodules_valid = False
    expected = {"pre-receive": hook_script(), "post-receive": hook_script(post=True)}
    hooks_valid = all(
        (repo / "hooks" / name).is_file()
        and os.access(repo / "hooks" / name, os.X_OK)
        and (repo / "hooks" / name).read_text(encoding="utf-8") == text
        for name, text in expected.items()
    )
    try:
        key_permissions_valid = signing_key.is_file() and signing_key.stat().st_mode & 0o077 == 0
        trusted_signers = [
            line for line in allowed.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        public_key = subprocess.check_output(
            ["ssh-keygen", "-y", "-f", str(signing_key)], text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        signing_key_trusted = any(public_key in line for line in trusted_signers)
    except (OSError, FileNotFoundError, subprocess.CalledProcessError):
        key_permissions_valid = False
        trusted_signers = []
        signing_key_trusted = False
    valid = bool(
        _config(repo, "core.bare") == "true" and protected
        and harness.joinpath(".harness/scripts/harness_receive.py").is_file()
        and key_permissions_valid and trusted_signers and signing_key_trusted
        and receipts.is_dir() and hooks_valid and submodules_valid
    )
    return {
        "decision": "pass" if valid else "block",
        "reason": "ENFORCED_AUDIT_PASS" if valid else "ENFORCED_AUDIT_BLOCK",
        "assurance": {
            "level": "enforced" if valid else "guarded",
            "acceptance_authority": "git-receive" if valid else "git-hooks",
            "bypassable": not valid,
            "protected_refs": list(protected),
            "signing_key_permissions_valid": key_permissions_valid,
            "trusted_signers": len(trusted_signers),
            "signing_key_trusted": signing_key_trusted,
            "submodule_repositories": len(submodules),
            "submodule_repositories_valid": submodules_valid,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Internal enforced Git authority installer")
    parser.add_argument("command", choices=("install", "audit"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--harness-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--protected-ref", action="append", default=[])
    parser.add_argument("--signing-key", default="")
    parser.add_argument("--allowed-signers", default="")
    parser.add_argument("--receipt-dir", default="")
    parser.add_argument("--submodule-repository", action="append", default=[])
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    if args.command == "audit":
        outcome = audit(repo)
    else:
        try:
            submodules = parse_submodule_repositories(args.submodule_repository)
        except ValueError as exc:
            dump_json({"decision": "block", "reason": str(exc)})
            return 1
        outcome = install(
            repo, Path(args.harness_root),
            protected_refs=tuple(args.protected_ref or ["refs/heads/main"]),
            signing_key=Path(args.signing_key), allowed_signers=Path(args.allowed_signers),
            receipt_dir=Path(args.receipt_dir),
            submodule_repositories=submodules,
        )
    dump_json(outcome)
    return 0 if outcome["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
