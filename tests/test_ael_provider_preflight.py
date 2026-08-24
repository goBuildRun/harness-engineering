#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".ael" / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"
ADAPTER = FIXTURES / "provider_mock_adapter.py"
sys.path.insert(0, str(SCRIPTS))

from ael_provider_preflight import (  # noqa: E402
    argv_digest,
    execute_preflight,
    validate_receipt,
    verify,
)
from ael_gate_manifest import GateInputError  # noqa: E402


class HarnessProviderPreflightTest(unittest.TestCase):
    def contract(self) -> dict:
        return json.loads((FIXTURES / "provider-offline-contract.json").read_text())

    def command(self, adapter: Path = ADAPTER) -> list[str]:
        return [
            sys.executable, str(adapter),
            "--subject", "subject", "--provider", "mock",
        ]

    def preflight(self) -> dict:
        return execute_preflight(
            self.contract(), "subject", "mock", ADAPTER, self.command(),
        )

    def trace(self) -> dict:
        return {
            "schema": "harness-provider-offline-trace-v2",
            "subject_digest": "subject",
            "provider": "mock",
            "offline": True,
            "network_calls": 0,
            "calls": ["provider.accept"],
        }

    def test_executes_offline_adapter_and_binds_truth_inputs(self) -> None:
        result = self.preflight()
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(result["provider"], "mock")
        self.assertEqual(result["network_calls"], 0)
        self.assertEqual(result["judge_usage"], "unknown")
        self.assertEqual(result["canonical_argv_digest"], argv_digest(self.command()))
        self.assertEqual(validate_receipt(result, "subject", "mock")["decision"], "pass")

    def test_dependency_parse_failure_preserves_structured_reason(self) -> None:
        with mock.patch(
            "ael_provider_preflight.execution_dependency_digest",
            side_effect=GateInputError("GATE_INPUT_PYTHON_PARSE_FAILED"),
        ):
            result = execute_preflight(
                self.contract(), "subject", "mock", ADAPTER, self.command(),
            )

        self.assertEqual(result, {
            "decision": "block", "reason": "GATE_INPUT_PYTHON_PARSE_FAILED",
        })

    def test_forged_provider_or_digest_fails_binding(self) -> None:
        receipt = self.preflight()
        receipt["contract_digest"] = "a" * 64
        self.assertEqual(
            validate_receipt(receipt, "subject", "mock")["reason"],
            "PROVIDER_PREFLIGHT_RECEIPT_DIGEST_INVALID",
        )
        self.assertEqual(
            validate_receipt(self.preflight(), "subject", "other")["decision"], "block",
        )

    def test_adapter_must_be_part_of_canonical_argv(self) -> None:
        result = execute_preflight(
            self.contract(), "subject", "mock", ADAPTER,
            [sys.executable, "-c", "print('{}')"],
        )
        self.assertEqual(result["reason"], "PROVIDER_ADAPTER_ARGV_MISMATCH")

    def test_adapter_used_only_as_shell_zero_is_not_executed(self) -> None:
        result = execute_preflight(
            self.contract(), "subject", "mock", ADAPTER,
            ["/bin/sh", "-c", "printf '{}\\n'", str(ADAPTER)],
        )
        self.assertEqual(result["reason"], "PROVIDER_ADAPTER_ARGV_MISMATCH")

    def test_preflight_structures_command_proof_error(self) -> None:
        result = execute_preflight(
            self.contract(), "subject", "mock", ADAPTER,
            ["/bin/sh", "-c", 'sh "$LOADER"'],
        )

        self.assertEqual(result, {
            "decision": "block", "reason": "GATE_INPUT_DYNAMIC_SHELL_COMMAND",
        })

    def test_adapter_change_during_offline_trace_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "mock_adapter.py"
            adapter.write_bytes(ADAPTER.read_bytes())

            def runner(_command, **_kwargs):
                adapter.write_text(adapter.read_text() + "\n# changed\n")
                return SimpleNamespace(returncode=0, stdout=json.dumps(self.trace()))

            result = execute_preflight(
                self.contract(), "subject", "mock", adapter, self.command(adapter),
                runner=runner,
            )
        self.assertEqual(result["reason"], "PROVIDER_ADAPTER_CHANGED")

    def test_imported_helper_change_invalidates_execution_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "helper.py"
            adapter = root / "adapter.py"
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            adapter.write_text(
                "import helper\n"
                "from pathlib import Path\n"
                "exec(Path(%r).read_text())\n" % str(ADAPTER),
                encoding="utf-8",
            )
            command = self.command(adapter)
            before = execute_preflight(
                self.contract(), "subject", "mock", adapter, command,
            )
            helper.write_text("VALUE = 2\n", encoding="utf-8")
            after = execute_preflight(
                self.contract(), "subject", "mock", adapter, command,
            )

        self.assertEqual(before["decision"], "pass", before)
        self.assertEqual(after["decision"], "pass", after)
        self.assertNotEqual(
            before["execution_dependency_digest"],
            after["execution_dependency_digest"],
        )

    def test_parent_package_initializer_is_part_of_import_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "provider_package"
            package.mkdir()
            initializer = package / "__init__.py"
            initializer.write_text("VALUE = 1\n", encoding="utf-8")
            (package / "adapter_helper.py").write_text("VALUE = 1\n", encoding="utf-8")
            adapter = root / "adapter.py"
            adapter.write_text("import provider_package.adapter_helper\n", encoding="utf-8")
            from ael_gate_inputs import execution_dependency_digest

            command = [sys.executable, str(adapter)]
            before = execution_dependency_digest(adapter, command)
            initializer.write_text("VALUE = 2\n", encoding="utf-8")
            after = execution_dependency_digest(adapter, command)

        self.assertNotEqual(before, after)

    def test_external_pythonpath_import_change_invalidates_execution_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root = root / "adapter"
            external_root = root / "external"
            adapter_root.mkdir()
            external_root.mkdir()
            adapter = adapter_root / "adapter.py"
            helper = external_root / "external_helper.py"
            adapter.write_text("import external_helper\n", encoding="utf-8")
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            from ael_gate_inputs import execution_dependency_digest

            with mock.patch.dict(os.environ, {"PYTHONPATH": str(external_root)}, clear=False):
                before = execution_dependency_digest(adapter, [sys.executable, str(adapter)])
                helper.write_text("VALUE = 2\n", encoding="utf-8")
                after = execution_dependency_digest(adapter, [sys.executable, str(adapter)])

        self.assertNotEqual(before, after)

    def test_external_path_shell_helper_change_invalidates_execution_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root = root / "adapter"
            external_root = root / "bin"
            adapter_root.mkdir()
            external_root.mkdir()
            adapter = adapter_root / "adapter"
            helper = external_root / "1provider+helper"
            adapter.write_text("#!/bin/sh\nexec 1provider+helper\n", encoding="utf-8")
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            adapter.chmod(0o755)
            helper.chmod(0o755)
            from ael_gate_inputs import execution_dependency_digest

            path_value = f"../bin{os.pathsep}{os.environ.get('PATH', '')}"
            with mock.patch.dict(os.environ, {"PATH": path_value}, clear=False):
                before = execution_dependency_digest(adapter, [str(adapter)])
                helper.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                after = execution_dependency_digest(adapter, [str(adapter)])

        self.assertNotEqual(before, after)

    def test_shell_helper_with_path_punctuation_is_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "adapter"
            helper = root / "provider+helper"
            wrapper.write_text("#!/bin/sh\nexec ./provider+helper\n", encoding="utf-8")
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            wrapper.chmod(0o755)
            helper.chmod(0o755)
            from ael_gate_inputs import execution_dependency_digest

            before = execution_dependency_digest(wrapper, [str(wrapper)])
            helper.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            after = execution_dependency_digest(wrapper, [str(wrapper)])

        self.assertNotEqual(before, after)

    def test_tilde_expanded_shell_helper_is_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "adapter"
            helper = root / "provider-helper"
            wrapper.write_text("#!/bin/sh\nexec ~/provider-helper\n", encoding="utf-8")
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            wrapper.chmod(0o755)
            helper.chmod(0o755)
            from ael_gate_inputs import execution_dependency_digest

            with mock.patch.dict(os.environ, {"HOME": str(root)}, clear=False):
                before = execution_dependency_digest(wrapper, [str(wrapper)])
                helper.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                after = execution_dependency_digest(wrapper, [str(wrapper)])

        self.assertNotEqual(before, after)

    def test_dynamic_shell_command_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "adapter"
            helper = root / "helper"
            wrapper.write_text(
                "#!/bin/sh\nprovider_command=./helper\nexec \"$provider_command\"\n",
                encoding="utf-8",
            )
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            wrapper.chmod(0o755)
            helper.chmod(0o755)
            from ael_gate_inputs import execution_dependency_digest
            from ael_gate_manifest import GateInputError

            with self.assertRaises(GateInputError) as raised:
                execution_dependency_digest(wrapper, [str(wrapper)])

        self.assertEqual(raised.exception.reason, "GATE_INPUT_DYNAMIC_SHELL_COMMAND")

    def test_dynamic_shell_code_loader_arguments_fail_closed(self) -> None:
        from ael_gate_inputs import execution_dependency_digest
        from ael_gate_manifest import GateInputError

        scripts = {
            "eval": "#!/bin/sh\nprovider_command=./helper\neval \"$provider_command\"\n",
            "python": "#!/bin/sh\nprovider_script=./helper.py\npython3 \"$provider_script\"\n",
            "shell": "#!/bin/sh\nprovider_script=./helper.sh\nsh \"$provider_script\"\n",
        }
        for name, source in scripts.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                wrapper = Path(tmp) / "adapter"
                wrapper.write_text(source, encoding="utf-8")
                wrapper.chmod(0o755)
                with self.assertRaises(GateInputError) as raised:
                    execution_dependency_digest(wrapper, [str(wrapper)])
                self.assertEqual(
                    raised.exception.reason, "GATE_INPUT_DYNAMIC_SHELL_COMMAND",
                )

    def test_shell_pythonpath_assignment_extends_import_closure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root = root / "adapter"
            custom_root = root / "custom"
            adapter_root.mkdir()
            custom_root.mkdir()
            wrapper = adapter_root / "wrapper"
            adapter = adapter_root / "adapter.py"
            helper = custom_root / "assigned_helper.py"
            wrapper.write_text(
                "#!/bin/sh\nPYTHONPATH=../custom exec python3 ./adapter.py\n",
                encoding="utf-8",
            )
            adapter.write_text("import assigned_helper\n", encoding="utf-8")
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            wrapper.chmod(0o755)
            from ael_gate_inputs import execution_dependency_digest

            before = execution_dependency_digest(wrapper, [str(wrapper)])
            helper.write_text("VALUE = 2\n", encoding="utf-8")
            after = execution_dependency_digest(wrapper, [str(wrapper)])

        self.assertNotEqual(before, after)

    def test_selected_python_interpreter_site_packages_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root = root / "adapter"
            interpreter = root / "venv/bin/python3"
            site_packages = root / "venv/lib/python3.12/site-packages"
            adapter_root.mkdir()
            interpreter.parent.mkdir(parents=True)
            site_packages.mkdir(parents=True)
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o755)
            adapter = adapter_root / "adapter.py"
            helper = site_packages / "selected_helper.py"
            adapter.write_text("import selected_helper\n", encoding="utf-8")
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            from ael_gate_inputs import execution_dependency_digest

            command = [str(interpreter), str(adapter)]
            before = execution_dependency_digest(adapter, command)
            helper.write_text("VALUE = 2\n", encoding="utf-8")
            after = execution_dependency_digest(adapter, command)

        self.assertNotEqual(before, after)

    def test_selected_interpreter_pth_and_transitive_imports_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root = root / "adapter"
            interpreter = root / "venv/bin/python3"
            site_packages = root / "venv/lib/python3.12/site-packages"
            custom_packages = root / "custom"
            adapter_root.mkdir()
            interpreter.parent.mkdir(parents=True)
            site_packages.mkdir(parents=True)
            custom_packages.mkdir()
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o755)
            (site_packages / "custom.pth").write_text(
                "../../../../custom\n", encoding="utf-8",
            )
            adapter = adapter_root / "adapter.py"
            adapter.write_text("import top_helper\n", encoding="utf-8")
            (custom_packages / "top_helper.py").write_text(
                "import leaf_helper\n", encoding="utf-8",
            )
            leaf = custom_packages / "leaf_helper.py"
            leaf.write_text("VALUE = 1\n", encoding="utf-8")
            from ael_gate_inputs import execution_dependency_digest

            command = [str(interpreter), str(adapter)]
            before = execution_dependency_digest(adapter, command)
            leaf.write_text("VALUE = 2\n", encoding="utf-8")
            after = execution_dependency_digest(adapter, command)

        self.assertNotEqual(before, after)

    def test_executable_pth_path_computation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            interpreter = root / "venv/bin/python3"
            site_packages = root / "venv/lib/python3.12/site-packages"
            adapter = root / "adapter.py"
            interpreter.parent.mkdir(parents=True)
            site_packages.mkdir(parents=True)
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o755)
            adapter.write_text("VALUE = 1\n", encoding="utf-8")
            (site_packages / "dynamic.pth").write_text(
                "import os; os.environ.get('PROVIDER_PLUGIN_PATH')\n",
                encoding="utf-8",
            )
            from ael_gate_inputs import execution_dependency_digest
            from ael_gate_manifest import GateInputError

            with self.assertRaises(GateInputError) as raised:
                execution_dependency_digest(adapter, [str(interpreter), str(adapter)])

        self.assertEqual(raised.exception.reason, "GATE_INPUT_DYNAMIC_PYTHON_PATH")

    def test_interpreter_discovery_does_not_execute_python_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            interpreter = root / "venv/bin/python3"
            adapter = root / "adapter.py"
            marker = root / "query-side-effect"
            interpreter.parent.mkdir(parents=True)
            interpreter.write_text(
                f"#!/bin/sh\ntouch {marker}\nexit 0\n", encoding="utf-8",
            )
            interpreter.chmod(0o755)
            adapter.write_text("VALUE = 1\n", encoding="utf-8")
            from ael_gate_inputs import execution_dependency_digest

            execution_dependency_digest(adapter, [str(interpreter), str(adapter)])
            side_effect_observed = marker.exists()

        self.assertFalse(side_effect_observed)

    def test_importlib_and_extension_module_targets_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = root / "adapter.py"
            dynamic_helper = root / "dynamic_helper.py"
            native_helper = root / "native_helper.so"
            adapter.write_text(
                "import importlib\n"
                "import native_helper\n"
                "importlib.import_module('dynamic_helper')\n",
                encoding="utf-8",
            )
            dynamic_helper.write_text("VALUE = 1\n", encoding="utf-8")
            native_helper.write_bytes(b"native-v1")
            from ael_gate_inputs import execution_dependency_digest

            command = [sys.executable, str(adapter)]
            before = execution_dependency_digest(adapter, command)
            dynamic_helper.write_text("VALUE = 2\n", encoding="utf-8")
            dynamic_after = execution_dependency_digest(adapter, command)
            native_helper.write_bytes(b"native-v2")
            native_after = execution_dependency_digest(adapter, command)

        self.assertNotEqual(before, dynamic_after)
        self.assertNotEqual(dynamic_after, native_after)

    def test_env_split_interpreter_and_extensionless_wrapper_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "helper"
            adapter = root / "adapter"
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            helper.chmod(0o755)
            adapter.write_text(
                "#!/usr/bin/env -S python3 -u\n"
                "import subprocess\n"
                "subprocess.run(['./helper'], check=True)\n",
                encoding="utf-8",
            )
            adapter.chmod(0o755)
            from ael_gate_inputs import execution_dependency_digest

            before = execution_dependency_digest(adapter, ["./adapter"])
            helper.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            after = execution_dependency_digest(adapter, ["./adapter"])

        self.assertNotEqual(before, after)

    def test_extensionless_shell_helper_and_absolute_python_shebang_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "helper"
            wrapper = root / "wrapper"
            python_wrapper = root / "python-wrapper"
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            wrapper.write_text("#!/bin/sh\n./helper\n", encoding="utf-8")
            python_wrapper.write_text(
                f"#!{sys.executable}\nfrom pathlib import Path\n"
                "import helper\n",
                encoding="utf-8",
            )
            for path in (helper, wrapper, python_wrapper):
                path.chmod(0o755)
            from ael_gate_inputs import execution_dependency_digest

            shell_before = execution_dependency_digest(wrapper, ["./wrapper"])
            python_before = execution_dependency_digest(python_wrapper, ["./python-wrapper"])
            helper.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            shell_after = execution_dependency_digest(wrapper, ["./wrapper"])
            python_after = execution_dependency_digest(python_wrapper, ["./python-wrapper"])

        self.assertNotEqual(shell_before, shell_after)
        self.assertNotEqual(python_before, python_after)

    def test_env_shebang_option_operands_do_not_hide_python_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "helper.py"
            wrapper = root / "wrapper"
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            wrapper.write_text(
                "#!/usr/bin/env -S -u PYTHONPATH python3 -u\n"
                "import helper\n",
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            from ael_gate_inputs import execution_dependency_digest

            before = execution_dependency_digest(wrapper, ["./wrapper"])
            helper.write_text("VALUE = 2\n", encoding="utf-8")
            after = execution_dependency_digest(wrapper, ["./wrapper"])

        self.assertNotEqual(before, after)

    def test_contract_and_executed_trace_fail_closed(self) -> None:
        contract = self.contract()
        contract["allowed_calls"] = []
        result = verify(
            contract, self.trace(), "subject", provider="mock",
            adapter_digest="a" * 64, canonical_argv_digest="b" * 64,
            execution_dependencies="c" * 64,
        )
        self.assertEqual(result["reason"], "PROVIDER_REQUIRED_NOT_ALLOWED")

        trace = self.trace()
        trace["network_calls"] = 1
        result = verify(
            self.contract(), trace, "subject", provider="mock",
            adapter_digest="a" * 64, canonical_argv_digest="b" * 64,
            execution_dependencies="c" * 64,
        )
        self.assertEqual(result["reason"], "PROVIDER_TRACE_NETWORK_ACTIVITY")


if __name__ == "__main__":
    unittest.main()
