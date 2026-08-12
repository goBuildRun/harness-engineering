#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_enforcement import (  # noqa: E402
    evaluate_enforcement, git_identity, probe_github, trusted_ci_context,
    validate_lifecycle_receipt,
)


def live_snapshot(**overrides):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "schema_version": 1,
        "source": "live",
        "repository": "goBuildRun/product",
        "target_branch": "main",
        "target_commit_sha": "a" * 40,
        "required_check": "harness-commit-acceptance",
        "ci_shared_judger": True,
        "branch_required_checks": ["harness-commit-acceptance"],
        "release_dependencies": ["harness-commit-acceptance"],
        "release_dependency_run": {
            "id": 41, "event": "workflow_run", "status": "completed",
            "conclusion": "success", "head_sha": "a" * 40,
        },
        "provider_done_guard": True,
        "provider_completion_run": {
            "id": 42, "event": "workflow_run", "status": "completed",
            "conclusion": "success", "head_sha": "a" * 40,
        },
        "expires_at": future,
    }
    data.update(overrides)
    return data


class EnforcementTest(unittest.TestCase):
    def test_git_identity_parses_supported_github_remotes_without_port_leakage(self) -> None:
        cases = {
            "https://github.com/org/repo.git": "org/repo",
            "git@github.com:org/repo.git": "org/repo",
            "ssh://git@ssh.github.com:443/org/repo.git": "org/repo",
            "ssh://git@example.com/org/repo.git": "",
        }
        for remote, expected in cases.items():
            with self.subTest(remote=remote), patch(
                "harness_enforcement.subprocess.check_output", side_effect=[remote, "main"]
            ):
                self.assertEqual(git_identity(ROOT), (expected, "main"))

    def test_installed_workflows_contain_merge_and_subject_guards(self) -> None:
        required = (ROOT / ".github/workflows/harness-required.yml").read_text()
        release = (ROOT / ".harness/templates/github/release.yml").read_text()
        provider = (ROOT / ".harness/templates/github/harness-provider-complete.yml").read_text()
        self.assertIn("opened, synchronize, reopened, edited, closed", required)
        self.assertIn("Reject closed unmerged pull request", required)
        self.assertIn("pull_request.merged != true", required)
        self.assertIn("ci_gc_review.py", required)
        self.assertIn("HARNESS_GC_REVIEW_URL", required)
        self.assertIn("--gc-result gc-result.json", required)
        for workflow in (release, provider):
            self.assertIn("workflow_run.conclusion == 'success'", workflow)
            self.assertIn("workflow_run.conclusion != 'success'", workflow)
            self.assertIn("Reject unsuccessful required run", workflow)
            self.assertIn("listPullRequestsAssociatedWithCommit", workflow)
            self.assertIn("merge_commit_sha === run.head_sha", workflow)
            self.assertIn("item.base.ref === targetBranch", workflow)
            self.assertIn("outputs.eligible == 'true'", workflow)
            self.assertIn("steps.lifecycle.outputs.eligible != 'true'", workflow)
        self.assertNotIn("pull_request:\n    types: [closed]", provider)
        self.assertIn("required_run_id', String(run.id)", provider)
        self.assertIn("--lifecycle-receipt", provider)
        self.assertIn("actions/setup-python@v5", provider)
        self.assertIn("pip install -r .harness/scripts/requirements.txt", provider)
        self.assertIn("tee provider-complete-output.json", provider)
        self.assertIn("d['decision']=='pass'", provider)
        for secret in (
            "FEISHU_APP_ID", "FEISHU_APP_SECRET", "JIRA_BASE_URL", "JIRA_EMAIL",
            "JIRA_API_TOKEN", "DINGTALK_APP_KEY", "DINGTALK_APP_SECRET",
            "DINGTALK_OPERATOR_USER_ID", "TEAMBITION_OPEN_API_AUTHORIZATION",
        ):
            self.assertIn(f"{secret}: ${{{{ secrets.{secret} }}}}", provider)
        self.assertEqual(release, (ROOT / ".github/workflows/release.yml").read_text())
        self.assertEqual(provider, (ROOT / ".github/workflows/harness-provider-complete.yml").read_text())

    def test_probe_rejects_skip_only_workflow_guards(self) -> None:
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(self.payload).encode()

        fail_closed = (ROOT / ".harness/templates/github/release.yml").read_text()
        skip_only = fail_closed.replace(
            "      - name: Reject unsuccessful required run\n"
            "        if: github.event.workflow_run.conclusion != 'success'\n"
            "        run: exit 1\n",
            "",
        )
        required = (ROOT / ".github/workflows/harness-required.yml").read_text()
        skip_only_required = required.replace(
            "      - name: Reject closed unmerged pull request\n"
            "        if: github.event.action == 'closed' && github.event.pull_request.merged != true\n"
            "        run: exit 1\n",
            "",
        )
        provider = (ROOT / ".harness/templates/github/harness-provider-complete.yml").read_text()
        skip_only_provider = provider.replace(
            "      - name: Reject unsuccessful required run\n"
            "        if: github.event.workflow_run.conclusion != 'success'\n"
            "        run: exit 1\n",
            "",
        )
        responses = [Response({"contexts": ["harness-commit-acceptance"]}),
                     Response({"sha": "a" * 40})]
        with tempfile.TemporaryDirectory() as tmp, \
                patch("urllib.request.urlopen", side_effect=responses), \
                patch("harness_enforcement._remote_file", side_effect=[
                    skip_only_required, skip_only, skip_only_provider,
                ]):
            snapshot = probe_github(
                Path(tmp), repository="org/repo", branch="main",
                required_check="harness-commit-acceptance", token="token",
            )
        self.assertEqual(snapshot["release_dependencies"], [])
        self.assertFalse(snapshot["provider_done_guard"])
        self.assertFalse(snapshot["ci_shared_judger"])

    def test_probe_recognizes_workflow_run_merge_guards(self) -> None:
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(self.payload).encode()

        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            workflows = product / ".github/workflows"
            workflows.mkdir(parents=True)
            (workflows / "harness-required.yml").write_text("untrusted local workflow")
            remote_workflows = [
                (ROOT / ".github/workflows/harness-required.yml").read_text(),
                (ROOT / ".harness/templates/github/release.yml").read_text(),
                (ROOT / ".harness/templates/github/harness-provider-complete.yml").read_text(),
            ]
            responses = [
                Response({"contexts": ["harness-commit-acceptance"]}),
                Response({"sha": "a" * 40}),
                Response({"workflow_runs": [{
                    "id": 41, "event": "workflow_run", "status": "completed",
                    "conclusion": "success", "head_sha": "a" * 40,
                    "updated_at": "2026-08-12T00:00:00Z",
                }]}),
                Response({"workflow_runs": [{
                    "id": 42, "event": "workflow_run", "status": "completed",
                    "conclusion": "success", "head_sha": "a" * 40,
                    "updated_at": "2026-08-12T00:00:00Z",
                }]}),
            ]
            with patch("urllib.request.urlopen", side_effect=responses), \
                    patch("harness_enforcement._remote_file", side_effect=remote_workflows) as remote_file:
                snapshot = probe_github(
                    product, repository="org/repo", branch="main",
                    required_check="harness-commit-acceptance", token="token",
                )
            self.assertEqual(snapshot["release_dependencies"], ["harness-commit-acceptance"])
            self.assertEqual(snapshot["target_commit_sha"], "a" * 40)
            self.assertEqual(snapshot["release_dependency_run"]["conclusion"], "success")
            self.assertTrue(snapshot["provider_done_guard"])
            self.assertEqual(snapshot["provider_completion_run"]["conclusion"], "success")
            self.assertEqual(remote_file.call_count, 3)
            self.assertTrue(all(call.args[2] == "a" * 40 for call in remote_file.call_args_list))

    def test_all_live_probes_are_required_for_enforced(self) -> None:
        result = evaluate_enforcement(
            live_snapshot(), repository="goBuildRun/product", branch="main",
            required_check="harness-commit-acceptance",
        )
        self.assertEqual(result["enforcement"], "enforced")
        self.assertEqual(result["blockers"], [])

    def test_local_or_expired_snapshot_cannot_claim_enforced(self) -> None:
        local = evaluate_enforcement(
            live_snapshot(source="snapshot"), repository="goBuildRun/product",
            branch="main", required_check="harness-commit-acceptance",
        )
        expired = evaluate_enforcement(
            live_snapshot(expires_at="2020-01-01T00:00:00Z"),
            repository="goBuildRun/product", branch="main",
            required_check="harness-commit-acceptance",
        )
        self.assertEqual(local["enforcement"], "shadow")
        self.assertIn("PROBE_NOT_LIVE", local["blockers"])
        self.assertIn("PROBE_EXPIRED", expired["blockers"])

    def test_missing_release_dependency_remains_shadow(self) -> None:
        result = evaluate_enforcement(
            live_snapshot(release_dependencies=[]), repository="goBuildRun/product",
            branch="main", required_check="harness-commit-acceptance",
        )
        self.assertIn("RELEASE_DEPENDENCY_MISSING", result["blockers"])

    def test_missing_or_failed_release_run_remains_shadow(self) -> None:
        missing = evaluate_enforcement(
            live_snapshot(release_dependency_run={}), repository="goBuildRun/product",
            branch="main", required_check="harness-commit-acceptance",
        )
        failed = evaluate_enforcement(
            live_snapshot(release_dependency_run={
                "id": 43, "event": "workflow_run", "status": "completed", "conclusion": "failure",
            }), repository="goBuildRun/product", branch="main",
            required_check="harness-commit-acceptance",
        )
        self.assertIn("RELEASE_WORKFLOW_RUN_MISSING", missing["blockers"])
        self.assertIn("RELEASE_WORKFLOW_RUN_MISSING", failed["blockers"])

    def test_workflow_runs_cannot_cross_target_commit(self) -> None:
        stale = live_snapshot()
        stale["release_dependency_run"]["head_sha"] = "b" * 40
        stale["provider_completion_run"]["head_sha"] = "b" * 40
        result = evaluate_enforcement(
            stale, repository="goBuildRun/product", branch="main",
            required_check="harness-commit-acceptance",
        )
        self.assertIn("RELEASE_RUN_SUBJECT_MISMATCH", result["blockers"])
        self.assertIn("PROVIDER_RUN_SUBJECT_MISMATCH", result["blockers"])

    def test_missing_or_failed_provider_run_remains_shadow(self) -> None:
        missing = evaluate_enforcement(
            live_snapshot(provider_completion_run={}), repository="goBuildRun/product",
            branch="main", required_check="harness-commit-acceptance",
        )
        failed = evaluate_enforcement(
            live_snapshot(provider_completion_run={
                "id": 43, "event": "workflow_run", "status": "completed", "conclusion": "failure",
            }), repository="goBuildRun/product", branch="main",
            required_check="harness-commit-acceptance",
        )
        self.assertIn("PROVIDER_COMPLETION_RUN_MISSING", missing["blockers"])
        self.assertIn("PROVIDER_COMPLETION_RUN_MISSING", failed["blockers"])

    def test_terminal_provider_status_requires_ci_merge_or_release_receipt(self) -> None:
        receipt = live_snapshot()
        receipt.update({
            "event": "merge", "work_item_id": "WI-42", "commit_sha": "a" * 40,
            "check_status": "success",
        })
        ok, _ = validate_lifecycle_receipt(receipt, work_item_id="WI-42", ci=True)
        self.assertTrue(ok)
        self.assertFalse(validate_lifecycle_receipt(receipt, work_item_id="WI-42", ci=False)[0])
        self.assertFalse(validate_lifecycle_receipt(receipt, work_item_id="WI-99", ci=True)[0])

    def test_only_supported_ci_platform_context_is_trusted(self) -> None:
        self.assertFalse(trusted_ci_context({"CI": "true"})[0])
        trusted = trusted_ci_context({
            "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "org/repo",
            "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123",
        })
        self.assertEqual(trusted, (True, "org/repo", "a" * 40, "123"))
        workflow_run = trusted_ci_context({
            "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "org/repo",
            "GITHUB_SHA": "b" * 40, "HARNESS_CI_COMMIT_SHA": "a" * 40,
            "GITHUB_RUN_ID": "124",
        })
        self.assertEqual(workflow_run, (True, "org/repo", "a" * 40, "124"))

    def test_work_item_cli_blocks_local_done_but_allows_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            workspace = product / "harness-workspace"
            workspace.mkdir()
            (workspace / "project.yaml").write_text(
                "product:\n  id: demo\nworkspace:\n  root: harness-workspace\n"
                "work_item:\n  provider: noop\n  id_pattern: '^[A-Za-z0-9._:-]+$'\n"
            )
            base = [
                "python3", str(SCRIPTS / "work_item.py"), "--harness-root", str(ROOT),
                "close", "--id", "WI-42",
            ]
            env = {**os.environ, "HARNESS_PRODUCT_ROOT": str(product), "WORK_ITEM_PROVIDER": "noop"}
            blocked = json.loads(subprocess.check_output([*base, "--status", "done"], cwd=product, env=env, text=True))
            ready = json.loads(subprocess.check_output([*base, "--status", "ready_to_release"], cwd=product, env=env, text=True))
            self.assertEqual(blocked["decision"], "block")
            self.assertIn("PROVIDER_TERMINAL_STATUS_FORBIDDEN", blocked["reason"])
            self.assertEqual(ready["decision"], "pass")


if __name__ == "__main__":
    unittest.main()
