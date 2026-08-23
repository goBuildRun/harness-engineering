#!/usr/bin/env python3
from __future__ import annotations

import os
import ast
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".harness" / "scripts"))

from harness_gate_inputs import execution_dependency_digest  # noqa: E402
from harness_gate_inputs import gate_input_digest  # noqa: E402
from harness_gate_imports import add_import_targets, python_modules  # noqa: E402
from harness_gate_argv import command_executes_path  # noqa: E402
from harness_gate_language import (  # noqa: E402
    MAX_LANGUAGE_SOURCE_CHARS, shell_words,
)
from harness_gate_manifest import CandidateManifest, GateInputError  # noqa: E402
from harness_gate_python import PythonEnvironment  # noqa: E402
from harness_gate_tools import tool_dependency_entries  # noqa: E402


class E4GateLanguageTest(unittest.TestCase):
    def assert_dependency_change(
        self, adapter: Path, dependency: Path, command: list[str] | None = None,
    ) -> None:
        before = execution_dependency_digest(adapter, command or [str(adapter)])
        dependency.write_text(dependency.read_text() + "# changed\n", encoding="utf-8")
        after = execution_dependency_digest(adapter, command or [str(adapter)])
        self.assertNotEqual(before, after)

    def test_gate_dependency_modules_and_review_suites_are_manifested(self) -> None:
        manifest = (ROOT / ".harness/harness-manifest.yaml").read_text(encoding="utf-8")
        for relative in (
            ".harness/scripts/harness_gate_argv.py",
            ".harness/scripts/harness_gate_imports.py",
            "tests/test_e4_gate_language.py",
            "tests/test_e4_review_hardening.py",
        ):
            self.assertIn(f"  - {relative}\n", manifest)

    def test_wrapper_local_path_assignment_binds_selected_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root, helper_root = root / "adapter", root / "bin"
            adapter_root.mkdir()
            helper_root.mkdir()
            wrapper, helper = adapter_root / "wrapper", helper_root / "provider-helper"
            wrapper.write_text(
                "#!/bin/sh\nPATH=../bin:$PATH exec provider-helper\n", encoding="utf-8",
            )
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            wrapper.chmod(0o755)
            helper.chmod(0o755)
            self.assert_dependency_change(wrapper, helper)

    def test_static_cd_binds_helper_from_resulting_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root, helper_root = root / "adapter", root / "bin"
            adapter_root.mkdir()
            helper_root.mkdir()
            wrapper, helper = adapter_root / "wrapper", helper_root / "provider-helper"
            wrapper.write_text(
                "#!/bin/sh\ncd ../bin\nexec ./provider-helper\n", encoding="utf-8",
            )
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            wrapper.chmod(0o755)
            helper.chmod(0o755)
            self.assert_dependency_change(wrapper, helper)

    def test_relative_path_is_resolved_at_command_lookup_after_cd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root = root / "adapter"
            selected_root = root / "bin"
            decoy_root = adapter_root / "bin"
            adapter_root.mkdir()
            selected_root.mkdir()
            decoy_root.mkdir()
            wrapper = adapter_root / "wrapper"
            selected = selected_root / "provider-helper"
            decoy = decoy_root / "provider-helper"
            wrapper.write_text(
                "#!/bin/sh\nPATH=./bin\ncd ..\nexec provider-helper\n",
                encoding="utf-8",
            )
            selected.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            decoy.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            for path in (wrapper, selected, decoy):
                path.chmod(0o755)

            self.assert_dependency_change(wrapper, selected)

    def test_nested_shell_inherits_call_site_cwd_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root, runtime_root, helper_root = (
                root / "adapter", root / "runtime", root / "bin",
            )
            for directory in (adapter_root, runtime_root, helper_root):
                directory.mkdir()
            wrapper = adapter_root / "wrapper"
            child = runtime_root / "child"
            helper = helper_root / "provider-helper"
            wrapper.write_text(
                "#!/bin/sh\nPATH=../bin\ncd ../runtime\n/bin/sh child\n",
                encoding="utf-8",
            )
            child.write_text("exec provider-helper\n", encoding="utf-8")
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            wrapper.chmod(0o755)
            helper.chmod(0o755)

            self.assert_dependency_change(wrapper, helper)

    def test_same_sourced_script_is_analyzed_under_every_call_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root, first_root, second_root = (
                root / "adapter", root / "first/bin", root / "second/bin",
            )
            for directory in (adapter_root, first_root, second_root):
                directory.mkdir(parents=True)
            wrapper, common = adapter_root / "wrapper", root / "common"
            first, second = first_root / "provider-helper", second_root / "provider-helper"
            wrapper.write_text(
                "#!/bin/sh\nPATH=../first/bin\n. ../common\n"
                "cd ../second\nPATH=./bin\n. ../common\n",
                encoding="utf-8",
            )
            common.write_text("provider-helper\n", encoding="utf-8")
            first.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            second.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            for path in (wrapper, first, second):
                path.chmod(0o755)

            self.assert_dependency_change(wrapper, first)
            self.assert_dependency_change(wrapper, second)

    def test_sourced_shell_context_mutation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper, setup = root / "wrapper", root / "setup"
            wrapper.write_text(
                "#!/bin/sh\n. ./setup\nexec provider-helper\n", encoding="utf-8",
            )
            setup.write_text("PATH=./provider-bin\n", encoding="utf-8")
            wrapper.chmod(0o755)

            with self.assertRaises(GateInputError) as raised:
                execution_dependency_digest(wrapper, [str(wrapper)])

        self.assertEqual(raised.exception.reason, "GATE_INPUT_SOURCED_CONTEXT_MUTATION")

    def test_sourced_shell_extended_context_mutations_fail_closed(self) -> None:
        for mutation in (
            "pushd ./bin", "popd", "unset PATH", "PATH+=:./bin",
            "read PATH", "printf -v PATH %s ./bin", "eval 'cd ./bin'", "mutate_path",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                wrapper, setup = root / "wrapper", root / "setup"
                wrapper.write_text("#!/bin/sh\n. ./setup\n", encoding="utf-8")
                setup.write_text(f"{mutation}\n", encoding="utf-8")
                wrapper.chmod(0o755)
                with self.assertRaises(GateInputError) as raised:
                    execution_dependency_digest(wrapper, [str(wrapper)])
                self.assertEqual(
                    raised.exception.reason, "GATE_INPUT_SOURCED_CONTEXT_MUTATION",
                )

    def test_sourced_subshell_context_mutation_does_not_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root, helper_root, decoy_root = (
                root / "adapter", root / "bin", root / "decoy",
            )
            for directory in (adapter_root, helper_root, decoy_root):
                directory.mkdir()
            wrapper, setup = adapter_root / "wrapper", adapter_root / "setup"
            helper = helper_root / "provider-helper"
            wrapper.write_text(
                "#!/bin/sh\nPATH=../bin\n. ./setup\nprovider-helper\n",
                encoding="utf-8",
            )
            setup.write_text(
                "(PATH=../decoy; cd ../decoy; true)\n", encoding="utf-8",
            )
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            wrapper.chmod(0o755)
            helper.chmod(0o755)

            self.assert_dependency_change(wrapper, helper)

    def test_subshell_restores_parent_cwd_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root, selected_root, decoy_root = (
                root / "adapter", root / "main/bin", root / "sub/bin",
            )
            for directory in (adapter_root, selected_root, decoy_root):
                directory.mkdir(parents=True)
            wrapper = adapter_root / "wrapper"
            selected = selected_root / "provider-helper"
            decoy = decoy_root / "provider-helper"
            wrapper.write_text(
                "#!/bin/sh\nPATH=../main/bin\n(cd ../sub; PATH=./bin; true)\n"
                "provider-helper\n",
                encoding="utf-8",
            )
            for path in (selected, decoy):
                path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            for path in (wrapper, selected, decoy):
                path.chmod(0o755)

            self.assert_dependency_change(wrapper, selected)

    def test_conditions_arrays_and_heredocs_preserve_static_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper, helper = root / "wrapper", root / "provider-helper"
            wrapper.write_text(
                "#!/bin/bash\nOPTIONS=(--mode safe)\n"
                "if [[ \"${MODE:-safe}\" == safe || -n \"$MODE\" ]]; then\n"
                "  python3 - <<'PY'\nprint('$NOT_A_SHELL_COMMAND')\nPY\nfi\n"
                "exec ./provider-helper\n",
                encoding="utf-8",
            )
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            wrapper.chmod(0o755)
            helper.chmod(0o755)
            self.assert_dependency_change(wrapper, helper)

    def test_dynamic_source_target_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "wrapper"
            wrapper.write_text(
                "#!/bin/sh\nFILE=./provider.env\nsource \"$FILE\"\n", encoding="utf-8",
            )
            wrapper.chmod(0o755)
            with self.assertRaises(GateInputError) as raised:
                execution_dependency_digest(wrapper, [str(wrapper)])
        self.assertEqual(raised.exception.reason, "GATE_INPUT_DYNAMIC_SHELL_COMMAND")

    def test_static_shell_loader_binds_extensionless_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper, helper = root / "wrapper", root / "provider-helper"
            wrapper.write_text("#!/bin/sh\nsh provider-helper\n", encoding="utf-8")
            helper.write_text("exit 0\n", encoding="utf-8")
            wrapper.chmod(0o755)
            self.assert_dependency_change(wrapper, helper)

    def test_python_module_loader_binds_static_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper, helper = root / "wrapper", root / "provider_helper.py"
            wrapper.write_text("#!/bin/sh\npython3 -m provider_helper\n", encoding="utf-8")
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            wrapper.chmod(0o755)
            self.assert_dependency_change(wrapper, helper)

    def test_shebang_interpreter_pth_import_is_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            interpreter = root / "venv/bin/python3"
            site_packages = root / "venv/lib/python3.12/site-packages"
            interpreter.parent.mkdir(parents=True)
            site_packages.mkdir(parents=True)
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o755)
            startup = site_packages / "startup_helper.py"
            startup.write_text("VALUE = 1\n", encoding="utf-8")
            (site_packages / "startup.pth").write_text(
                "import startup_helper\n", encoding="utf-8",
            )
            adapter = root / "adapter"
            adapter.write_text(f"#!{interpreter}\nVALUE = 1\n", encoding="utf-8")
            adapter.chmod(0o755)
            self.assert_dependency_change(adapter, startup)

    def test_nonliteral_importlib_target_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "adapter.py"
            adapter.write_text(
                "import importlib\nmodule_name = 'provider_helper'\n"
                "importlib.import_module(module_name)\n",
                encoding="utf-8",
            )
            with self.assertRaises(GateInputError) as raised:
                execution_dependency_digest(adapter, [sys.executable, str(adapter)])
        self.assertEqual(raised.exception.reason, "GATE_INPUT_DYNAMIC_PYTHON_IMPORT")

    def test_external_provider_python_closure_is_recursive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root = root / "adapter"
            site_packages = root / "external/lib/python3.12/site-packages"
            package = site_packages / "provider_sdk"
            adapter_root.mkdir()
            package.mkdir(parents=True)
            adapter = adapter_root / "adapter.py"
            helper = package / "client.py"
            adapter.write_text("import provider_sdk\n", encoding="utf-8")
            (package / "__init__.py").write_text(
                "from . import client\n", encoding="utf-8",
            )
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ, {"PYTHONPATH": str(site_packages)}, clear=False,
            ):
                self.assert_dependency_change(
                    adapter, helper, [sys.executable, str(adapter)],
                )

    def test_external_provider_dynamic_import_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root = root / "adapter"
            site_packages = root / "external/lib/python3.12/site-packages"
            package = site_packages / "provider_sdk"
            adapter_root.mkdir()
            package.mkdir(parents=True)
            adapter = adapter_root / "adapter.py"
            adapter.write_text("import provider_sdk\n", encoding="utf-8")
            (package / "__init__.py").write_text(
                "import importlib\nNAME = 'provider_sdk.client'\n"
                "importlib.import_module(NAME)\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ, {"PYTHONPATH": str(site_packages)}, clear=False,
            ), self.assertRaises(GateInputError) as raised:
                execution_dependency_digest(adapter, [sys.executable, str(adapter)])

        self.assertEqual(raised.exception.reason, "GATE_INPUT_DYNAMIC_PYTHON_IMPORT")

    def test_external_provider_closure_uses_shared_manifest_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root = root / "adapter"
            site_packages = root / "external/site-packages"
            package = site_packages / "provider_sdk"
            adapter_root.mkdir()
            package.mkdir(parents=True)
            interpreter = adapter_root / "python3"
            adapter = adapter_root / "adapter.py"
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o755)
            adapter.write_text("import provider_sdk\n", encoding="utf-8")
            (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
            manifest = CandidateManifest(ROOT, adapter_root, max_files=2)

            with mock.patch.dict(
                os.environ, {"PYTHONPATH": str(site_packages)}, clear=False,
            ), self.assertRaises(GateInputError) as raised:
                tool_dependency_entries(
                    ROOT, [str(interpreter), str(adapter)], manifest,
                    execution_cwd=adapter_root, bind_interpreter_environment=True,
                    strict_external_python=True,
                )

        self.assertEqual(raised.exception.reason, "GATE_INPUT_LIMIT_EXCEEDED")

    def test_python_subprocess_path_executable_is_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter_root, helper_root = root / "adapter", root / "bin"
            adapter_root.mkdir()
            helper_root.mkdir()
            adapter, helper = adapter_root / "adapter.py", helper_root / "provider-helper"
            adapter.write_text(
                "import subprocess\nsubprocess.run(['provider-helper'], check=True)\n",
                encoding="utf-8",
            )
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            helper.chmod(0o755)
            with mock.patch.dict(
                os.environ, {"PATH": f"{helper_root}{os.pathsep}{os.environ.get('PATH', '')}"},
                clear=False,
            ):
                self.assert_dependency_change(
                    adapter, helper, [sys.executable, str(adapter)],
                )

    def test_dynamic_python_subprocess_fails_closed_for_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "adapter.py"
            adapter.write_text(
                "import subprocess\ncommand = ['provider-helper']\n"
                "subprocess.run(command, check=True)\n",
                encoding="utf-8",
            )
            with self.assertRaises(GateInputError) as raised:
                execution_dependency_digest(adapter, [sys.executable, str(adapter)])

        self.assertEqual(raised.exception.reason, "GATE_INPUT_DYNAMIC_PYTHON_COMMAND")

    def test_python_subprocess_aliases_and_executable_override_are_bound(self) -> None:
        sources = (
            "import subprocess as sp\nsp.run(['provider-helper'], check=True)\n",
            "from subprocess import run as invoke\ninvoke(['provider-helper'], check=True)\n",
            "import subprocess\nsubprocess.run(['ignored'], executable='provider-helper')\n",
            "def invoke():\n"
            "    import subprocess as sp\n"
            "    sp.run(['provider-helper'], check=True)\n"
            "invoke()\n",
            "import subprocess\n"
            "invoke = subprocess.run\n"
            "invoke(['provider-helper'], check=True)\n",
        )
        for source in sources:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                adapter, helper = root / "adapter.py", root / "provider-helper"
                adapter.write_text(source, encoding="utf-8")
                helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                helper.chmod(0o755)
                with mock.patch.dict(
                    os.environ, {"PATH": f"{root}{os.pathsep}{os.environ.get('PATH', '')}"},
                    clear=False,
                ):
                    self.assert_dependency_change(
                        adapter, helper, [sys.executable, str(adapter)],
                    )

    def test_provider_python_parse_failure_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "adapter.py"
            adapter.write_text("FUTURE_SYNTAX = 1\n", encoding="utf-8")
            real_parse = __import__("ast").parse

            def selected_parse(source: str, *args, **kwargs):
                if "FUTURE_SYNTAX" in source:
                    raise SyntaxError("selected interpreter syntax")
                return real_parse(source, *args, **kwargs)

            with mock.patch("harness_gate_tools.ast.parse", side_effect=selected_parse), \
                    self.assertRaises(GateInputError) as raised:
                execution_dependency_digest(adapter, [sys.executable, str(adapter)])

        self.assertEqual(raised.exception.reason, "GATE_INPUT_PYTHON_PARSE_FAILED")

    def test_cacheable_gate_binds_selected_interpreter_startup_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / ".harness/scripts"
            interpreter = root / "venv/bin/python3"
            site_packages = root / "venv/lib/python3.12/site-packages"
            scripts.mkdir(parents=True)
            interpreter.parent.mkdir(parents=True)
            site_packages.mkdir(parents=True)
            wrapper = scripts / "gate.py"
            startup = site_packages / "startup_helper.py"
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o755)
            wrapper.write_text("VALUE = 1\n", encoding="utf-8")
            startup.write_text("VALUE = 1\n", encoding="utf-8")
            (site_packages / "startup.pth").write_text(
                "import startup_helper\n", encoding="utf-8",
            )
            common = {
                "harness": root, "product": root, "changed_files": [],
                "planning_gate": root / "missing.json", "planning_credential": {},
                "command": [str(interpreter), str(wrapper)],
            }

            before = gate_input_digest("structure", **common)
            startup.write_text("VALUE = 2\n", encoding="utf-8")
            after = gate_input_digest("structure", **common)

        self.assertNotEqual(before, after)

    def test_env_selected_python_interpreter_is_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            interpreter_root = root / "bin"
            interpreter_root.mkdir()
            interpreter = interpreter_root / "python3"
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o755)
            adapter = root / "adapter.py"
            adapter.write_text("VALUE = 1\n", encoding="utf-8")
            path_value = f"{interpreter_root}{os.pathsep}{os.environ.get('PATH', '')}"
            with mock.patch.dict(os.environ, {"PATH": path_value}, clear=False):
                self.assert_dependency_change(
                    adapter, interpreter, ["/usr/bin/env", "python3", str(adapter)],
                )

    def test_top_level_shell_loader_binds_bare_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter, helper = root / "adapter", root / "provider-helper"
            adapter.write_text("anchor\n", encoding="utf-8")
            helper.write_text("exit 0\n", encoding="utf-8")
            self.assert_dependency_change(
                adapter, helper, [shutil.which("sh") or "/bin/sh", "provider-helper"],
            )

    def test_top_level_shell_code_binds_static_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter, helper = root / "adapter", root / "provider-helper"
            adapter.write_text("anchor\n", encoding="utf-8")
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            helper.chmod(0o755)
            self.assert_dependency_change(
                adapter, helper,
                [shutil.which("sh") or "/bin/sh", "-c", "./provider-helper"],
            )

    def test_top_level_shell_code_binds_path_resolved_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter, helper = root / "adapter", root / "provider-helper"
            adapter.write_text("anchor\n", encoding="utf-8")
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            helper.chmod(0o755)
            with mock.patch.dict(
                os.environ, {"PATH": f"{root}{os.pathsep}{os.environ.get('PATH', '')}"},
                clear=False,
            ):
                self.assert_dependency_change(
                    adapter, helper,
                [shutil.which("sh") or "/bin/sh", "-c", "exec provider-helper", str(adapter)],
            )

    def test_top_level_shell_code_binds_nested_loader_script_and_module(self) -> None:
        for mode in ("script", "module"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                adapter = root / "adapter"
                adapter.write_text("anchor\n", encoding="utf-8")
                if mode == "script":
                    dependency = root / "provider-wrapper"
                    dependency.write_text("exit 0\n", encoding="utf-8")
                    code = "sh ./provider-wrapper"
                else:
                    dependency = root / "provider_module.py"
                    dependency.write_text("VALUE = 1\n", encoding="utf-8")
                    code = "python3 -m provider_module"
                self.assert_dependency_change(
                    adapter, dependency,
                    [shutil.which("sh") or "/bin/sh", "-c", code, str(adapter)],
                )

    def test_top_level_shell_code_binds_static_source_operand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter, dependency = root / "adapter", root / "provider-wrapper"
            adapter.write_text("anchor\n", encoding="utf-8")
            dependency.write_text("VALUE=1\n", encoding="utf-8")

            self.assert_dependency_change(
                adapter, dependency,
                [shutil.which("sh") or "/bin/sh", "-c", ". ./provider-wrapper", str(adapter)],
            )

    def test_top_level_dynamic_loader_operand_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "adapter"
            adapter.write_text("anchor\n", encoding="utf-8")
            with self.assertRaises(GateInputError) as raised:
                execution_dependency_digest(
                    adapter,
                    [shutil.which("sh") or "/bin/sh", "-c", 'sh "$WRAPPER"', str(adapter)],
                )

        self.assertEqual(raised.exception.reason, "GATE_INPUT_DYNAMIC_SHELL_COMMAND")

    def test_top_level_python_module_and_code_are_bound(self) -> None:
        for mode in ("module", "code"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                adapter, helper = root / "adapter.py", root / "provider_helper.py"
                adapter.write_text("VALUE = 1\n", encoding="utf-8")
                helper.write_text("VALUE = 1\n", encoding="utf-8")
                command = (
                    [sys.executable, "-m", "provider_helper"]
                    if mode == "module" else
                    [sys.executable, "-c", "import provider_helper"]
                )
                self.assert_dependency_change(adapter, helper, command)

    def test_top_level_provider_python_code_parse_failure_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "adapter.py"
            adapter.write_text("VALUE = 1\n", encoding="utf-8")
            real_parse = __import__("ast").parse

            def selected_parse(source: str, *args, **kwargs):
                if "FUTURE_ARGV_SYNTAX" in source:
                    raise SyntaxError("selected interpreter syntax")
                return real_parse(source, *args, **kwargs)

            with mock.patch("harness_gate_language.ast.parse", side_effect=selected_parse), \
                    self.assertRaises(GateInputError) as raised:
                execution_dependency_digest(
                    adapter, [sys.executable, "-c", "FUTURE_ARGV_SYNTAX", str(adapter)],
                )

        self.assertEqual(raised.exception.reason, "GATE_INPUT_PYTHON_PARSE_FAILED")

    def test_env_chdir_binds_python_module_from_effective_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter, work = root / "adapter.py", root / "runtime"
            work.mkdir()
            module = work / "provider_module.py"
            adapter.write_text("VALUE = 1\n", encoding="utf-8")
            module.write_text("VALUE = 1\n", encoding="utf-8")

            self.assert_dependency_change(
                adapter, module,
                ["/usr/bin/env", "-C", str(work), sys.executable, "-m", "provider_module", str(adapter)],
            )

    def test_cacheable_gate_binds_external_transitive_python_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "harness"
            scripts = harness / ".harness/scripts"
            site_packages = root / "external/site-packages"
            package = site_packages / "provider_sdk"
            scripts.mkdir(parents=True)
            package.mkdir(parents=True)
            wrapper, helper = scripts / "gate.py", package / "client.py"
            wrapper.write_text("import provider_sdk\n", encoding="utf-8")
            (package / "__init__.py").write_text("from . import client\n", encoding="utf-8")
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            common = {
                "harness": harness, "product": harness, "changed_files": [],
                "planning_gate": harness / "missing.json", "planning_credential": {},
                "command": [sys.executable, str(wrapper)],
            }
            with mock.patch.dict(
                os.environ, {"PYTHONPATH": str(site_packages)}, clear=False,
            ):
                before = gate_input_digest("structure", **common)
                helper.write_text("VALUE = 2\n", encoding="utf-8")
                after = gate_input_digest("structure", **common)

        self.assertNotEqual(before, after)

    def test_cacheable_gate_with_external_dynamic_import_is_downgraded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "harness"
            scripts = harness / ".harness/scripts"
            site_packages = root / "external/site-packages"
            package = site_packages / "provider_sdk"
            scripts.mkdir(parents=True)
            package.mkdir(parents=True)
            wrapper = scripts / "gate.py"
            wrapper.write_text("import provider_sdk\n", encoding="utf-8")
            (package / "__init__.py").write_text(
                "import importlib\nNAME='provider_sdk.client'\n"
                "importlib.import_module(NAME)\n",
                encoding="utf-8",
            )
            manifest = CandidateManifest(harness, harness)
            with mock.patch.dict(
                os.environ, {"PYTHONPATH": str(site_packages)}, clear=False,
            ):
                digest = gate_input_digest(
                    "structure", harness=harness, product=harness, changed_files=[],
                    planning_gate=harness / "missing.json", planning_credential={},
                    command=[sys.executable, str(wrapper)],
                    candidates_manifest=manifest,
                )

        self.assertTrue(digest)
        self.assertFalse(manifest.gate_cache_safe("structure"))

    def test_shared_dynamic_import_memo_marks_every_gate_uncacheable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / ".harness/scripts"
            scripts.mkdir(parents=True)
            wrapper = scripts / "gate.py"
            wrapper.write_text(
                "import importlib\nname='provider_sdk.client'\n"
                "importlib.import_module(name)\n",
                encoding="utf-8",
            )
            manifest = CandidateManifest(root, root)
            common = {
                "harness": root, "product": root, "changed_files": [],
                "planning_gate": root / "missing.json", "planning_credential": {},
                "command": [sys.executable, str(wrapper)],
                "candidates_manifest": manifest,
            }
            gate_input_digest("structure", **common)
            gate_input_digest("knowledge", **common)

        self.assertFalse(manifest.gate_cache_safe("structure"))
        self.assertFalse(manifest.gate_cache_safe("knowledge"))

    def test_aliased_importlib_dynamic_target_fails_closed(self) -> None:
        sources = (
            "import importlib as il\nNAME='provider_sdk'\nil.import_module(NAME)\n",
            "from importlib import import_module as load\n"
            "NAME='provider_sdk'\nload(NAME)\n",
            "import importlib\nload=importlib.import_module\n"
            "NAME='provider_sdk'\nload(NAME)\n",
            "import builtins\nNAME='provider_sdk'\nbuiltins.__import__(NAME)\n",
        )
        for source in sources:
            with self.subTest(source=source):
                with self.assertRaises(GateInputError) as raised:
                    python_modules(ast.parse(source))
                self.assertEqual(
                    raised.exception.reason, "GATE_INPUT_DYNAMIC_PYTHON_IMPORT",
                )

        self.assertIn(
            "provider_sdk", python_modules(ast.parse('exec("import provider_sdk")')),
        )

    def test_relative_importlib_target_is_resolved(self) -> None:
        self.assertIn(
            "provider_package.client",
            python_modules(ast.parse(
                "import importlib\n"
                "importlib.import_module('.client', 'provider_package')\n"
            )),
        )

    def test_assigned_dynamic_import_alias_downgrades_gate_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "gate.py"
            wrapper.write_text(
                "import importlib\nload=importlib.import_module\n"
                "NAME='provider_sdk'\nload(NAME)\n",
                encoding="utf-8",
            )
            manifest = CandidateManifest(root, root)
            gate_input_digest(
                "structure", harness=root, product=root, changed_files=[],
                planning_gate=root / "missing.json", planning_credential={},
                command=[sys.executable, str(wrapper)], candidates_manifest=manifest,
            )
        self.assertFalse(manifest.gate_cache_safe("structure"))

    def test_inline_dynamic_python_imports_downgrade_ordinary_gate_cache(self) -> None:
        dynamic_code = (
            "import importlib; name='provider_sdk.client'; "
            "importlib.import_module(name)"
        )
        commands = (
            [sys.executable, "-c", dynamic_code],
            ["sh", "-c", f"{sys.executable} -c \"{dynamic_code}\""],
        )
        for command in commands:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                manifest = CandidateManifest(root, root)
                digest = gate_input_digest(
                    "structure", harness=root, product=root, changed_files=[],
                    planning_gate=root / "missing.json", planning_credential={},
                    command=command, candidates_manifest=manifest,
                )
                self.assertTrue(digest)
                self.assertFalse(manifest.gate_cache_safe("structure"))

    def test_wrapper_inline_dynamic_python_import_downgrades_gate_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / ".harness/scripts"
            scripts.mkdir(parents=True)
            wrapper = scripts / "gate.sh"
            wrapper.write_text(
                "#!/bin/sh\npython3 -c \"import importlib; "
                "name='provider_sdk.client'; importlib.import_module(name)\"\n",
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            manifest = CandidateManifest(root, root)
            digest = gate_input_digest(
                "structure", harness=root, product=root, changed_files=[],
                planning_gate=root / "missing.json", planning_credential={},
                command=[str(wrapper)], candidates_manifest=manifest,
            )

        self.assertTrue(digest)
        self.assertFalse(manifest.gate_cache_safe("structure"))

    def test_strict_inline_dynamic_python_import_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = CandidateManifest(root, root)
            with self.assertRaises(GateInputError) as raised:
                tool_dependency_entries(
                    root, [
                        sys.executable, "-c",
                        "import importlib; name='provider_sdk'; importlib.import_module(name)",
                    ], manifest, strict_external_python=True,
                )

        self.assertEqual(raised.exception.reason, "GATE_INPUT_DYNAMIC_PYTHON_IMPORT")

    def test_dead_branch_does_not_prove_adapter_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = root / "adapter"
            helper = root / "provider-helper"
            for executable in (adapter, helper):
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)

            self.assertFalse(command_executes_path(
                ["/bin/sh", "-c", "false && ./adapter; ./provider-helper", "adapter"],
                root, adapter,
            ))
            self.assertFalse(command_executes_path(
                ["/bin/sh", "-c", "true() { ./adapter; }; ./provider-helper", "adapter"],
                root, adapter,
            ))
            self.assertFalse(command_executes_path(
                ["/bin/sh", "-c", "exec ./provider-helper; ./adapter", "adapter"],
                root, adapter,
            ))

    def test_loader_option_operands_do_not_replace_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = root / "adapter.py"
            adapter.write_text("VALUE=1\n", encoding="utf-8")

            self.assertTrue(command_executes_path(
                ["sh", "-o", "errexit", str(adapter)], root, adapter,
            ))
            self.assertTrue(command_executes_path(
                [sys.executable, "-W", "ignore", str(adapter)], root, adapter,
            ))
            rcfile = root / "bashrc"
            rcfile.write_text("true\n", encoding="utf-8")
            self.assertTrue(command_executes_path(
                ["bash", "--rcfile", str(rcfile), str(adapter)], root, adapter,
            ))

    def test_python_module_package_main_proves_adapter_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "provider_package"
            package.mkdir()
            adapter = package / "__main__.py"
            adapter.write_text("VALUE=1\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"PYTHONPATH": str(root)}, clear=False):
                self.assertTrue(command_executes_path(
                    [sys.executable, "-m", "provider_package"], root, adapter,
                ))

            prefix = root / "runtime"
            interpreter = prefix / "bin/python3"
            site_module = "site_provider_package"
            site_package = prefix / f"lib/python3.13/site-packages/{site_module}"
            site_package.mkdir(parents=True)
            interpreter.parent.mkdir(parents=True)
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o755)
            (prefix / "pyvenv.cfg").write_text("version = 3.13.7\n", encoding="utf-8")
            site_adapter = site_package / "__main__.py"
            site_adapter.write_text("VALUE=1\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"PYTHONPATH": ""}, clear=False):
                self.assertTrue(command_executes_path(
                    [str(interpreter), "-m", site_module], root, site_adapter,
                ))
                self.assertTrue(command_executes_path(
                    ["sh", "-c", f"{interpreter} -m {site_module}"],
                    root, site_adapter,
                ))

            shadow = root / f"shadow/{site_module}"
            shadow.mkdir(parents=True)
            (shadow / "__main__.py").write_text("SHADOW=1\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {"PYTHONPATH": f"{shadow.parent}{os.pathsep}{site_package.parent}"},
                clear=False,
            ):
                self.assertFalse(command_executes_path(
                    [str(interpreter), "-m", site_module], root, site_adapter,
                ))
                self.assertFalse(command_executes_path(
                    [str(interpreter), "-I", "-m", site_module],
                    root, shadow / "__main__.py",
                ))
            unproven = prefix / "lib/python999/site-packages/unproven/__main__.py"
            unproven.parent.mkdir(parents=True)
            unproven.write_text("VALUE=1\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"PYTHONPATH": ""}, clear=False):
                self.assertFalse(command_executes_path(
                    [str(interpreter), "-m", "unproven"], root, unproven,
                ))

    def test_language_parser_input_is_bounded(self) -> None:
        with self.assertRaises(GateInputError) as raised:
            shell_words("x" * (MAX_LANGUAGE_SOURCE_CHARS + 1))
        self.assertEqual(raised.exception.reason, "GATE_INPUT_LIMIT_EXCEEDED")

    def test_ordinary_python_parse_failure_downgrades_gate_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "future.py"
            wrapper.write_text("FUTURE_SYNTAX\n", encoding="utf-8")
            manifest = CandidateManifest(root, root)
            real_parse = ast.parse

            def versioned_parse(source, *args, **kwargs):
                if source == "FUTURE_SYNTAX\n":
                    raise SyntaxError("future syntax")
                return real_parse(source, *args, **kwargs)

            with mock.patch("harness_gate_python.ast.parse", side_effect=versioned_parse):
                gate_input_digest(
                    "structure", harness=root, product=root, changed_files=[],
                    planning_gate=root / "missing.json", planning_credential={},
                    command=[sys.executable, str(wrapper)], candidates_manifest=manifest,
                )
        self.assertFalse(manifest.gate_cache_safe("structure"))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "gate.sh"
            wrapper.write_text(
                "#!/bin/sh\npython3 -c 'if broken syntax'\n", encoding="utf-8",
            )
            wrapper.chmod(0o755)
            manifest = CandidateManifest(root, root)
            gate_input_digest(
                "structure", harness=root, product=root, changed_files=[],
                planning_gate=root / "missing.json", planning_credential={},
                command=[str(wrapper)], candidates_manifest=manifest,
            )
        self.assertFalse(manifest.gate_cache_safe("structure"))

    def test_absent_import_root_is_part_of_manifest_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "harness"
            scripts = harness / ".harness/scripts"
            scripts.mkdir(parents=True)
            wrapper = scripts / "gate.py"
            wrapper.write_text("import future_provider\n", encoding="utf-8")
            missing_root = root / "future/site-packages"
            manifest = CandidateManifest(harness, harness)
            with mock.patch.dict(
                os.environ, {"PYTHONPATH": str(missing_root)}, clear=False,
            ):
                gate_input_digest(
                    "structure", harness=harness, product=harness, changed_files=[],
                    planning_gate=harness / "missing.json", planning_credential={},
                    command=[sys.executable, str(wrapper)], candidates_manifest=manifest,
                )
            missing_root.mkdir(parents=True)
            (missing_root / "future_provider.py").write_text("VALUE=1\n", encoding="utf-8")

            with self.assertRaises(GateInputError) as raised:
                manifest.verify_fresh()

        self.assertEqual(raised.exception.reason, "GATE_INPUT_CHANGED")

    def test_manifest_detects_dependency_replacement_before_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dependency = root / "dependency.py"
            dependency.write_text("VALUE = 1\n", encoding="utf-8")
            manifest = CandidateManifest(root, root)
            manifest.digest(dependency, root=root)
            dependency.write_text("VALUE = 2\n", encoding="utf-8")

            with self.assertRaises(GateInputError) as raised:
                manifest.verify_fresh()

        self.assertEqual(raised.exception.reason, "GATE_INPUT_CHANGED")

    def test_interpreter_environment_scan_obeys_manifest_scan_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            interpreter = root / "venv/bin/python3"
            adapter = root / "adapter.py"
            interpreter.parent.mkdir(parents=True)
            for version in ("python3.11", "python3.12"):
                (root / f"venv/lib/{version}/site-packages").mkdir(parents=True)
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o755)
            adapter.write_text("VALUE = 1\n", encoding="utf-8")
            manifest = CandidateManifest(ROOT, root, max_scan_entries=1)

            with self.assertRaises(GateInputError) as raised:
                tool_dependency_entries(
                    ROOT, [str(interpreter), str(adapter)], manifest,
                    execution_cwd=root, bind_interpreter_environment=True,
                )

        self.assertEqual(raised.exception.reason, "GATE_INPUT_LIMIT_EXCEEDED")

    def test_pth_and_extension_scans_obey_shared_manifest_limit(self) -> None:
        for scan in ("pth", "extension"):
            with self.subTest(scan=scan), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                site_packages = root / "site-packages"
                site_packages.mkdir()
                manifest = CandidateManifest(root, root, max_scan_entries=1)
                if scan == "pth":
                    (site_packages / "first.pth").write_text("# first\n", encoding="utf-8")
                    (site_packages / "second.pth").write_text("# second\n", encoding="utf-8")
                    environment = PythonEnvironment(
                        root, root, root, manifest, lambda *_args, **_kwargs: None,
                        root,
                    )
                    action = lambda: environment.add_root(site_packages)
                else:
                    (site_packages / "provider.one.so").write_text("one\n", encoding="utf-8")
                    (site_packages / "provider.two.so").write_text("two\n", encoding="utf-8")
                    action = lambda: add_import_targets(
                        set(), "provider", [site_packages], manifest,
                    )
                with self.assertRaises(GateInputError) as raised:
                    action()
                self.assertEqual(raised.exception.reason, "GATE_INPUT_LIMIT_EXCEEDED")

    def test_pth_import_parser_input_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site_packages = root / "site-packages"
            site_packages.mkdir()
            (site_packages / "oversized.pth").write_text(
                "import " + "x" * MAX_LANGUAGE_SOURCE_CHARS + "\n", encoding="utf-8",
            )
            environment = PythonEnvironment(
                root, root, root, CandidateManifest(root, root),
                lambda *_args, **_kwargs: None, root,
            )
            with self.assertRaises(GateInputError) as raised:
                environment.add_root(site_packages)

        self.assertEqual(raised.exception.reason, "GATE_INPUT_LIMIT_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
