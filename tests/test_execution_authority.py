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
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_execution_authority import (  # noqa: E402
    AuthorityTrust,
    build_receipt,
    sign_receipt,
    trust_from_installation,
    validate_receipt,
)
from harness_provider_authority import (  # noqa: E402
    SANDBOX_CLAIMS,
    build_binding,
    provider_input_digest,
    sandbox_input_digest,
    wait_for_receipt,
    validate_binding,
)
from harness_release_readback import validate as validate_lifecycle_readback  # noqa: E402
from harness_runtime import canonical_digest  # noqa: E402


class ExecutionAuthorityTest(unittest.TestCase):
    def keys(self, root: Path, principal: str) -> tuple[Path, AuthorityTrust]:
        root.mkdir(parents=True, exist_ok=True)
        key = root / "authority"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True,
        )
        public = key.with_suffix(".pub").read_text(encoding="utf-8")
        allowed = root / "allowed_signers"
        allowed.write_text(f"{principal} {public}", encoding="utf-8")
        fingerprint = subprocess.check_output(
            ["ssh-keygen", "-lf", str(key.with_suffix(".pub")), "-E", "sha256"],
            text=True,
        ).split()[1]
        return key, AuthorityTrust(
            allowed_signers=allowed,
            signer_fingerprint=fingerprint,
            principal=principal,
        )

    def receipt(self, root: Path, *, action: str = "provider-preflight"):
        principal = "harness-network-sandbox"
        key, trust = self.keys(root, principal)
        issued = datetime.now(timezone.utc)
        receipt = build_receipt(
            authority="network-sandbox",
            action=action,
            subject_digest="subject",
            provider="mock",
            input_digest="a" * 64,
            output_digest="b" * 64,
            claims={"network": "none", "workspace": "read-only"},
            issued_at=issued,
            expires_at=issued + timedelta(minutes=5),
        )
        return sign_receipt(receipt, key), trust

    def validate(self, receipt: dict, trust: AuthorityTrust):
        return validate_receipt(
            receipt,
            trust=trust,
            authority="network-sandbox",
            action="provider-preflight",
            subject_digest="subject",
            provider="mock",
            input_digest="a" * 64,
            output_digest="b" * 64,
            claims={"network": "none", "workspace": "read-only"},
        )

    def test_pinned_receipt_binds_every_execution_dimension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipt, trust = self.receipt(Path(tmp))
            result = self.validate(receipt, trust)
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(result["reason"], "EXECUTION_AUTHORITY_RECEIPT_VALID")

    def test_tamper_wrong_action_and_expiry_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt, trust = self.receipt(root)
            tampered = json.loads(json.dumps(receipt))
            tampered["output_digest"] = "c" * 64
            self.assertEqual(
                self.validate(tampered, trust)["reason"],
                "EXECUTION_AUTHORITY_RECEIPT_DIGEST_INVALID",
            )
            self.assertEqual(
                validate_receipt(
                    receipt, trust=trust, authority="network-sandbox",
                    action="provider-execution", subject_digest="subject",
                    provider="mock", input_digest="a" * 64,
                    output_digest="b" * 64,
                    claims={"network": "none", "workspace": "read-only"},
                )["reason"],
                "EXECUTION_AUTHORITY_ACTION_MISMATCH",
            )
            key = root / "authority"
            expired = build_receipt(
                authority="network-sandbox", action="provider-preflight",
                subject_digest="subject", provider="mock",
                input_digest="a" * 64, output_digest="b" * 64,
                claims={"network": "none", "workspace": "read-only"},
                issued_at=datetime.now(timezone.utc) - timedelta(minutes=10),
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
            expired = sign_receipt(expired, key)
            self.assertEqual(
                self.validate(expired, trust)["reason"],
                "EXECUTION_AUTHORITY_RECEIPT_EXPIRED",
            )

    def test_unpinned_signer_cannot_authorize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt, trust = self.receipt(root)
            other_root = root / "other"
            other_root.mkdir()
            _other_key, other_trust = self.keys(other_root, trust.principal)
            forged_trust = AuthorityTrust(
                allowed_signers=trust.allowed_signers,
                signer_fingerprint=other_trust.signer_fingerprint,
                principal=trust.principal,
            )
            result = self.validate(receipt, forged_trust)
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["reason"], "EXECUTION_AUTHORITY_SIGNER_MISMATCH")

    def test_caller_environment_cannot_select_execution_trust_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _key, self_selected = self.keys(root / "caller", "harness-network-sandbox")
            with mock.patch.dict(os.environ, {
                "HARNESS_SANDBOX_AUTHORITY_ALLOWED_SIGNERS": str(
                    self_selected.allowed_signers
                ),
                "HARNESS_SANDBOX_AUTHORITY_SIGNER_FINGERPRINT": (
                    self_selected.signer_fingerprint
                ),
            }):
                with self.assertRaisesRegex(
                    ValueError, "EXECUTION_AUTHORITY_INSTALLATION_POLICY_MISSING",
                ):
                    trust_from_installation(
                        root, "network-sandbox", "harness-network-sandbox",
                    )

    def test_installation_policy_pins_execution_trust_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _key, expected = self.keys(
                root / ".harness/trust/network", "harness-network-sandbox",
            )
            policy = root / ".harness/authority-trust.json"
            policy.write_text(json.dumps({
                "schema": "harness-authority-trust-v1",
                "authorities": {
                    "network-sandbox": {
                        "principal": "harness-network-sandbox",
                        "allowed_signers": ".harness/trust/network/allowed_signers",
                        "signer_fingerprint": expected.signer_fingerprint,
                    },
                },
            }))

            loaded = trust_from_installation(
                root, "network-sandbox", "harness-network-sandbox",
            )
            self.assertEqual(loaded.allowed_signers, expected.allowed_signers.resolve())
            self.assertEqual(loaded.signer_fingerprint, expected.signer_fingerprint)
            self.assertEqual(loaded.principal, expected.principal)

    def test_provider_binding_requires_both_pinned_authorities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sandbox_key, sandbox_trust = self.keys(root / "sandbox", "harness-network-sandbox")
            provider_key, provider_trust = self.keys(root / "provider", "harness-provider-response")
            preflight = {
                "subject_digest": "subject", "provider": "mock",
                "contract_digest": "1" * 64, "adapter_digest": "2" * 64,
                "canonical_argv_digest": "3" * 64,
                "execution_dependency_digest": "4" * 64,
                "execution_environment_digest": "5" * 64,
                "trace_digest": "6" * 64, "receipt_digest": "7" * 64,
            }
            attempt = {
                "decision": "pass", "subject_digest": "subject", "provider": "mock",
                "contract_digest": "1" * 64, "adapter_digest": "2" * 64,
                "canonical_argv_digest": "3" * 64,
                "execution_dependency_digest": "4" * 64,
                "execution_environment_digest": "5" * 64,
                "evidence_ref": "evidence/provider.json",
                "evidence_schema": "harness-provider-evidence-v1",
                "evidence_digest": "8" * 64, "receipt_digest": "9" * 64,
            }
            issued = datetime.now(timezone.utc)
            sandbox = sign_receipt(build_receipt(
                authority="network-sandbox", action="provider-preflight",
                subject_digest="subject", provider="mock",
                input_digest=sandbox_input_digest(preflight),
                output_digest=preflight["trace_digest"], claims=SANDBOX_CLAIMS,
                issued_at=issued, expires_at=issued + timedelta(minutes=5),
            ), sandbox_key)
            provider = sign_receipt(build_receipt(
                authority="provider-response", action="provider-execution",
                subject_digest="subject", provider="mock",
                input_digest=provider_input_digest(preflight, attempt),
                output_digest=attempt["evidence_digest"],
                claims={
                    "evidence_ref": attempt["evidence_ref"],
                    "evidence_schema": attempt["evidence_schema"],
                    "response_authoritative": True,
                },
                issued_at=issued, expires_at=issued + timedelta(minutes=5),
            ), provider_key)
            binding = build_binding(
                preflight=preflight, attempt=attempt,
                sandbox_authority=sandbox, provider_authority=provider,
                sandbox_trust=sandbox_trust, provider_trust=provider_trust,
            )
            self.assertEqual(binding["decision"], "pass")
            self.assertEqual(validate_binding(
                binding, preflight=preflight, attempt=attempt,
                sandbox_trust=sandbox_trust, provider_trust=provider_trust,
            )["decision"], "pass")
            binding["provider_authority"]["output_digest"] = "0" * 64
            self.assertEqual(validate_binding(
                binding, preflight=preflight, attempt=attempt,
                sandbox_trust=sandbox_trust, provider_trust=provider_trust,
            )["decision"], "block")

    def test_external_lifecycle_readback_requires_exact_signed_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key, trust = self.keys(root, "harness-lifecycle-readback")
            claims = {
                "task_id": "task-1", "work_item_id": "wi-1",
                "status": "ready_to_release", "readback": "pass",
                "candidate_digest": "candidate", "commit": "a" * 40,
            }
            issued = datetime.now(timezone.utc)
            receipt = sign_receipt(build_receipt(
                authority="lifecycle-readback",
                action="ready-to-release-readback",
                subject_digest="candidate", provider="feishu",
                input_digest=canonical_digest({
                    "task_id": "task-1", "work_item_id": "wi-1",
                    "candidate_digest": "candidate", "commit": "a" * 40,
                    "provider": "feishu",
                }),
                output_digest=canonical_digest({
                    "status": "ready_to_release", "readback": "pass",
                }),
                claims=claims, issued_at=issued,
                expires_at=issued + timedelta(minutes=5),
            ), key)
            self.assertEqual(validate_lifecycle_readback(
                receipt, task_id="task-1", work_item_id="wi-1",
                provider="feishu", candidate_digest="candidate",
                commit="a" * 40, trust=trust,
            )["decision"], "pass")
            self.assertEqual(validate_lifecycle_readback(
                receipt, task_id="task-1", work_item_id="wi-other",
                provider="feishu", candidate_digest="candidate",
                commit="a" * 40, trust=trust,
            )["decision"], "block")

    def test_provider_response_authority_wait_is_bounded_and_observes_atomic_arrival(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "provider-authority.json"
            path.write_text('{"receipt":"stale"}')
            clock = [0.0]

            def sleep(duration: float) -> None:
                clock[0] += duration
                path.write_text('{"receipt":"ready"}')

            receipt = wait_for_receipt(
                path, deadline=1.0, clock=lambda: clock[0], sleeper=sleep,
                validator=lambda value: value.get("receipt") == "ready",
            )
            self.assertEqual(receipt, {"receipt": "ready"})
            path.unlink()
            with self.assertRaisesRegex(ValueError, "PROVIDER_RESPONSE_AUTHORITY_TIMEOUT"):
                wait_for_receipt(
                    path, deadline=0.0, clock=lambda: clock[0], sleeper=sleep,
                )


if __name__ == "__main__":
    unittest.main()
