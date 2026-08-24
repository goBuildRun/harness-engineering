#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".ael" / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"
ADAPTER = FIXTURES / "provider_mock_adapter.py"
sys.path.insert(0, str(SCRIPTS))

from ael_strict_evidence import COMPONENT_SCHEMA, validate  # noqa: E402
from ael_execution_authority import (  # noqa: E402
    AuthorityTrust, build_receipt, sign_receipt,
)
from ael_provider_authority import (  # noqa: E402
    SANDBOX_CLAIMS, build_binding, provider_input_digest, sandbox_input_digest,
)
from provider_attempt import EVIDENCE_SCHEMA, run_once  # noqa: E402
from ael_provider_preflight import execute_preflight  # noqa: E402


class HarnessStrictEvidenceTest(unittest.TestCase):
    def authority(self, root: Path, principal: str) -> tuple[Path, AuthorityTrust]:
        root.mkdir(parents=True, exist_ok=True)
        key = root / "key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True,
        )
        public = key.with_suffix(".pub").read_text(encoding="utf-8")
        allowed = root / "allowed"
        allowed.write_text(f"{principal} {public}", encoding="utf-8")
        fingerprint = subprocess.check_output(
            ["ssh-keygen", "-lf", str(key.with_suffix(".pub")), "-E", "sha256"],
            text=True,
        ).split()[1]
        return key, AuthorityTrust(allowed, fingerprint, principal)

    def command(self):
        return [
            sys.executable, str(ADAPTER),
            "--subject", "subject", "--provider", "mock",
        ]

    def strict_receipt(self, product: Path):
        contract = json.loads((FIXTURES / "provider-offline-contract.json").read_text())
        preflight = execute_preflight(contract, "subject", "mock", ADAPTER, self.command())
        evidence = product / "evidence/provider.json"
        evidence.parent.mkdir(parents=True)

        def runner(_command, **_kwargs):
            evidence.write_text(json.dumps({
                "schema": EVIDENCE_SCHEMA,
                "decision": "pass",
                "subject_digest": "subject",
                "provider": "mock",
            }))
            return SimpleNamespace(returncode=0)

        attempt = run_once(
            product / "provider-attempt.json", preflight, "subject", "mock",
            evidence, "evidence/provider.json", self.command(), 1, runner,
            adapter_path=ADAPTER,
        )
        acceptance = {
            "decision": "pass",
            "provider_mode": "real",
            "synthetic_only": False,
            "provider": "mock",
            "evidence_ref": "evidence/provider.json",
            "attempt_receipt": attempt,
            "preflight_receipt_digest": preflight["receipt_digest"],
            "attempt_receipt_digest": attempt["receipt_digest"],
            "contract_digest": attempt["contract_digest"],
            "adapter_digest": attempt["adapter_digest"],
            "canonical_argv_digest": attempt["canonical_argv_digest"],
            "evidence_digest": attempt["evidence_digest"],
            "attempt_verifier_digest": attempt["attempt_verifier_digest"],
        }
        sandbox_key, sandbox_trust = self.authority(
            product / "authorities/sandbox", "harness-network-sandbox",
        )
        provider_key, provider_trust = self.authority(
            product / "authorities/provider", "harness-provider-response",
        )
        issued = datetime.now(timezone.utc)
        sandbox_authority = sign_receipt(build_receipt(
            authority="network-sandbox", action="provider-preflight",
            subject_digest="subject", provider="mock",
            input_digest=sandbox_input_digest(preflight),
            output_digest=preflight["trace_digest"], claims=SANDBOX_CLAIMS,
            issued_at=issued, expires_at=issued + timedelta(minutes=5),
        ), sandbox_key)
        provider_authority = sign_receipt(build_receipt(
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
        authorities = build_binding(
            preflight=preflight, attempt=attempt,
            sandbox_authority=sandbox_authority,
            provider_authority=provider_authority,
            sandbox_trust=sandbox_trust,
            provider_trust=provider_trust,
        )
        components = {}
        for name, authority in {
            "browser_qa": "browser-qa",
            "deployment": "deployment-controller",
            "rollback": "rollback-verifier",
        }.items():
            artifact = product / f"evidence/{name}.json"
            artifact.write_text(json.dumps({"decision": "pass", "name": name}))
            components[name] = {
                "schema": COMPONENT_SCHEMA,
                "decision": "pass",
                "subject_digest": "subject",
                "authority": authority,
                "evidence_ref": f"evidence/{name}.json",
                "evidence_digest": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            }
        return {
            "subject_digest": "subject",
            **components,
            "provider_preflight": preflight,
            "provider_acceptance": acceptance,
            "provider_authorities": authorities,
        }, evidence, {
            "network-sandbox": sandbox_trust,
            "provider-response": provider_trust,
        }

    @staticmethod
    def installed_trust(authorities):  # type: ignore[no-untyped-def]
        return lambda _root, authority, _principal: authorities[authority]

    def test_expected_provider_and_complete_digest_chain_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            payload, _evidence, authorities = self.strict_receipt(product)
            receipt = product / "strict.json"
            receipt.write_text(json.dumps(payload))
            with mock.patch.dict(os.environ, {
                "AEL_STRICT_EVIDENCE": str(receipt),
            }), mock.patch(
                "ael_strict_evidence.trust_from_installation",
                side_effect=self.installed_trust(authorities),
            ):
                passed = validate(
                    "subject", {"provider_mode": "real_required"}, product, "mock",
                )
                wrong_provider = validate(
                    "subject", {"provider_mode": "real_required"}, product, "other",
                )
        self.assertEqual(passed["decision"], "pass")
        self.assertEqual(wrong_provider["decision"], "block")

    def test_chain_mismatch_and_evidence_tampering_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            payload, evidence, authorities = self.strict_receipt(product)
            receipt = product / "strict.json"
            broken = deepcopy(payload)
            broken["provider_acceptance"]["adapter_digest"] = "a" * 64
            receipt.write_text(json.dumps(broken))
            with mock.patch.dict(os.environ, {
                "AEL_STRICT_EVIDENCE": str(receipt),
            }), mock.patch(
                "ael_strict_evidence.trust_from_installation",
                side_effect=self.installed_trust(authorities),
            ):
                chain_result = validate(
                    "subject", {"provider_mode": "real_required"}, product, "mock",
                )
                receipt.write_text(json.dumps(payload))
                evidence.write_text(json.dumps({
                    "schema": EVIDENCE_SCHEMA,
                    "decision": "pass",
                    "subject_digest": "subject",
                    "provider": "mock",
                    "tampered": True,
                }))
                tampered = validate(
                    "subject", {"provider_mode": "real_required"}, product, "mock",
                )
        self.assertEqual(chain_result["decision"], "block")
        self.assertEqual(tampered["decision"], "block")

    def test_bare_component_pass_and_tampered_artifact_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            payload, _evidence, authorities = self.strict_receipt(product)
            receipt = product / "strict.json"
            bare = deepcopy(payload)
            bare["browser_qa"] = {"decision": "pass"}
            receipt.write_text(json.dumps(bare))
            with mock.patch.dict(os.environ, {
                "AEL_STRICT_EVIDENCE": str(receipt),
            }), mock.patch(
                "ael_strict_evidence.trust_from_installation",
                side_effect=self.installed_trust(authorities),
            ):
                self.assertEqual(
                    validate("subject", {"provider_mode": "real_required"}, product, "mock")["decision"],
                    "block",
                )
                receipt.write_text(json.dumps(payload))
                (product / "evidence/deployment.json").write_text("tampered")
                self.assertEqual(
                    validate("subject", {"provider_mode": "real_required"}, product, "mock")["decision"],
                    "block",
                )

if __name__ == "__main__":
    unittest.main()
