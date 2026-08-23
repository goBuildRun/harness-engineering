#!/usr/bin/env python3
"""Bounded, memoized candidate file discovery for Harness gate plans."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any, Callable

from harness_runtime import canonical_digest


MAX_CANDIDATE_FILES = 4096
MAX_CANDIDATE_BYTES = 64 * 1024 * 1024
MAX_SCAN_ENTRIES = 16384
READ_CHUNK_BYTES = 1024 * 1024


class GateInputError(RuntimeError):
    """Fail-closed input discovery error suitable for a structured gate receipt."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CandidateManifest:
    """One bounded path-to-digest manifest shared by every gate in a plan."""

    def __init__(
        self,
        harness: Path,
        product: Path,
        *,
        deadline: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_files: int = MAX_CANDIDATE_FILES,
        max_bytes: int = MAX_CANDIDATE_BYTES,
        max_scan_entries: int = MAX_SCAN_ENTRIES,
    ) -> None:
        self.harness = harness.resolve()
        self.product = product.resolve()
        self.deadline = deadline
        self.clock = clock
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.max_scan_entries = max_scan_entries
        self._digests: dict[str, str] = {}
        self._contents: dict[str, bytes] = {}
        self._sources: dict[str, Path] = {}
        self._file_count = 0
        self._byte_count = 0
        self._scan_count = 0
        self._derived: dict[tuple[str, str], Any] = {}
        self._children_cache: dict[tuple[str, str], tuple[Path, ...]] = {}
        self._directory_snapshots: dict[tuple[str, str], tuple[str, ...]] = {}
        self._uncacheable_gates: set[str] = set()
        self._dynamic_sources: set[tuple[str, str]] = set()

    @property
    def entries(self) -> dict[str, str]:
        return dict(self._digests)

    def check_deadline(self) -> None:
        if self.deadline is not None and self.clock() >= self.deadline:
            raise GateInputError("STAGE_BUDGET_EXCEEDED")

    def memoized(self, key: tuple[str, str], builder: Callable[[], Any]) -> Any:
        self.check_deadline()
        if key not in self._derived:
            self._derived[key] = builder()
        return self._derived[key]

    def mark_gate_uncacheable(self, gate: str) -> None:
        self._uncacheable_gates.add(gate)

    def gate_cache_safe(self, gate: str) -> bool:
        return gate not in self._uncacheable_gates

    def mark_dynamic_source(self, key: tuple[str, str]) -> None:
        self._dynamic_sources.add(key)

    def source_has_dynamic_import(self, key: tuple[str, str]) -> bool:
        return key in self._dynamic_sources

    @staticmethod
    def _coerce_path(path: object) -> Path | None:
        try:
            return Path(os.fspath(path))
        except TypeError:
            return None

    @staticmethod
    def _key(path: Path) -> str:
        return str(path.resolve(strict=False))

    @staticmethod
    def _relative_label(path: Path, root: Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path.resolve(strict=False).relative_to(root.resolve()))

    def _guard_path(self, path: Path, root: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(root.resolve())
        except (OSError, ValueError) as exc:
            raise GateInputError("GATE_INPUT_SYMLINK_ESCAPE") from exc

    def _read(self, path: Path, *, root: Path) -> bytes | None:
        self.check_deadline()
        self._guard_path(path, root)
        key = self._key(path)
        self._sources.setdefault(key, path.parent.resolve(strict=False) / path.name)
        if key in self._contents:
            return self._contents[key]
        if key in self._digests and self._digests[key] == "absent":
            return None
        try:
            stat = path.stat()
        except FileNotFoundError:
            self._digests[key] = "absent"
            return None
        except OSError as exc:
            raise GateInputError("GATE_INPUT_READ_FAILED") from exc
        if not path.is_file():
            self._digests[key] = "absent"
            return None
        if self._file_count + 1 > self.max_files or self._byte_count + stat.st_size > self.max_bytes:
            raise GateInputError("GATE_INPUT_LIMIT_EXCEEDED")
        content = bytearray()
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(READ_CHUNK_BYTES):
                    self.check_deadline()
                    content.extend(chunk)
                    digest.update(chunk)
                    if self._byte_count + len(content) > self.max_bytes:
                        raise GateInputError("GATE_INPUT_LIMIT_EXCEEDED")
        except GateInputError:
            raise
        except OSError as exc:
            raise GateInputError("GATE_INPUT_READ_FAILED") from exc
        self._file_count += 1
        self._byte_count += len(content)
        value = bytes(content)
        self._contents[key] = value
        self._digests[key] = digest.hexdigest()
        return value

    def digest(self, path: Path, *, root: Path | None = None) -> str:
        candidate = self._coerce_path(path)
        if candidate is None:
            return "absent"
        root = (root or (
            self.harness if candidate.resolve(strict=False).is_relative_to(self.harness)
            else self.product
        )).resolve()
        self.check_deadline()
        self._guard_path(candidate, root)
        key = self._key(candidate)
        if key not in self._digests:
            self._read(candidate, root=root)
        return self._digests[key]

    def read_text(self, path: Path, *, root: Path | None = None) -> str:
        candidate = self._coerce_path(path)
        if candidate is None:
            return ""
        root = (root or (
            self.harness if candidate.resolve(strict=False).is_relative_to(self.harness)
            else self.product
        )).resolve()
        content = self._read(candidate, root=root)
        return content.decode("utf-8", errors="replace") if content is not None else ""

    def read_external(self, path: Path) -> bytes | None:
        """Read an external dependency under the same plan-wide limits and cache."""
        self.check_deadline()
        candidate = path.resolve(strict=False)
        key = self._key(candidate)
        self._sources.setdefault(key, path.parent.resolve(strict=False) / path.name)
        if key in self._contents:
            return self._contents[key]
        if key in self._digests and self._digests[key] == "absent":
            return None
        try:
            stat = candidate.stat()
        except FileNotFoundError:
            self._digests[key] = "absent"
            return None
        except OSError as exc:
            raise GateInputError("GATE_INPUT_READ_FAILED") from exc
        if not candidate.is_file():
            self._digests[key] = "absent"
            return None
        if self._file_count + 1 > self.max_files or self._byte_count + stat.st_size > self.max_bytes:
            raise GateInputError("GATE_INPUT_LIMIT_EXCEEDED")
        content = bytearray()
        digest = hashlib.sha256()
        try:
            with candidate.open("rb") as stream:
                while chunk := stream.read(READ_CHUNK_BYTES):
                    self.check_deadline()
                    content.extend(chunk)
                    digest.update(chunk)
                    if self._byte_count + len(content) > self.max_bytes:
                        raise GateInputError("GATE_INPUT_LIMIT_EXCEEDED")
        except GateInputError:
            raise
        except OSError as exc:
            raise GateInputError("GATE_INPUT_READ_FAILED") from exc
        value = bytes(content)
        self._file_count += 1
        self._byte_count += len(value)
        self._contents[key] = value
        self._digests[key] = digest.hexdigest()
        return value

    def tree_digest(
        self, root: Path, *, allowed_root: Path, suffixes: set[str] | None = None,
    ) -> str:
        self.check_deadline()
        self._guard_path(root, allowed_root)
        if not root.is_dir():
            return "absent"
        entries: list[tuple[str, str]] = []
        pending = [root]
        while pending:
            directory = pending.pop()
            for path in self.children(directory, allowed_root=allowed_root):
                if path.is_dir():
                    if path.is_symlink():
                        raise GateInputError("GATE_INPUT_SYMLINK_DIRECTORY")
                    pending.append(path)
                elif path.is_file() and (not suffixes or path.suffix in suffixes):
                    entries.append((
                        self._relative_label(path, root), self.digest(path, root=allowed_root),
                    ))
        return canonical_digest(sorted(entries))

    def _list_children(
        self, root: Path, *, allowed_root: Path | None,
    ) -> list[Path]:
        self.check_deadline()
        if allowed_root is not None:
            self._guard_path(root, allowed_root)
        lexical_root = root.parent.resolve(strict=False) / root.name
        cache_key = (
            str(lexical_root), str(allowed_root.resolve()) if allowed_root is not None else "",
        )
        if cache_key in self._children_cache:
            return list(self._children_cache[cache_key])
        children: list[Path] = []
        try:
            for path in root.iterdir():
                self.check_deadline()
                self._scan_count += 1
                if self._scan_count > self.max_scan_entries:
                    raise GateInputError("GATE_INPUT_LIMIT_EXCEEDED")
                if allowed_root is not None:
                    self._guard_path(path, allowed_root)
                children.append(path)
        except FileNotFoundError:
            self._directory_snapshots[cache_key] = ("absent",)
            self._children_cache[cache_key] = ()
            return []
        except GateInputError:
            raise
        except OSError as exc:
            raise GateInputError("GATE_INPUT_READ_FAILED") from exc
        children.sort(key=lambda item: item.name)
        self._children_cache[cache_key] = tuple(children)
        self._directory_snapshots[cache_key] = (
            "present", *(f"{path.name}\0{path.resolve(strict=False)}" for path in children),
        )
        return children

    def children(self, root: Path, *, allowed_root: Path) -> list[Path]:
        return self._list_children(root, allowed_root=allowed_root)

    def external_children(self, root: Path) -> list[Path]:
        """Enumerate a selected external directory under the shared scan budget."""
        return self._list_children(root, allowed_root=None)

    def dependency_children(self, root: Path) -> list[Path]:
        """Snapshot internal boundaries strictly and selected external roots explicitly."""
        resolved = root.resolve(strict=False)
        if resolved.is_relative_to(self.harness):
            return self.children(root, allowed_root=self.harness)
        if resolved.is_relative_to(self.product):
            return self.children(root, allowed_root=self.product)
        return self.external_children(root)

    def verify_fresh(self) -> None:
        """Fail closed when any memoized dependency changed after discovery."""
        verified_bytes = 0
        for key, expected in sorted(self._digests.items()):
            self.check_deadline()
            source = self._sources.get(key, Path(key))
            try:
                current_key = self._key(source)
                is_file = source.is_file()
            except OSError as exc:
                raise GateInputError("GATE_INPUT_READ_FAILED") from exc
            if current_key != key or (expected == "absent") != (not is_file):
                raise GateInputError("GATE_INPUT_CHANGED")
            if expected == "absent":
                continue
            digest = hashlib.sha256()
            try:
                with source.open("rb") as stream:
                    while chunk := stream.read(READ_CHUNK_BYTES):
                        self.check_deadline()
                        verified_bytes += len(chunk)
                        if verified_bytes > self.max_bytes:
                            raise GateInputError("GATE_INPUT_LIMIT_EXCEEDED")
                        digest.update(chunk)
            except GateInputError:
                raise
            except OSError as exc:
                raise GateInputError("GATE_INPUT_READ_FAILED") from exc
            if digest.hexdigest() != expected:
                raise GateInputError("GATE_INPUT_CHANGED")
        verified_entries = 0
        for cache_key, expected in sorted(self._directory_snapshots.items()):
            self.check_deadline()
            root = Path(cache_key[0])
            allowed_root = Path(cache_key[1]) if cache_key[1] else None
            current: list[Path] = []
            try:
                for path in root.iterdir():
                    self.check_deadline()
                    verified_entries += 1
                    if verified_entries > self.max_scan_entries:
                        raise GateInputError("GATE_INPUT_LIMIT_EXCEEDED")
                    if allowed_root is not None:
                        self._guard_path(path, allowed_root)
                    current.append(path)
                current.sort(key=lambda item: item.name)
                actual = (
                    "present",
                    *(f"{path.name}\0{path.resolve(strict=False)}" for path in current),
                )
            except FileNotFoundError:
                actual = ("absent",)
            except GateInputError:
                raise
            except OSError as exc:
                raise GateInputError("GATE_INPUT_READ_FAILED") from exc
            if actual != expected:
                raise GateInputError("GATE_INPUT_CHANGED")

    def selected_digest(self, paths: set[Path], *, root: Path) -> str:
        self.check_deadline()
        entries = []
        for path in sorted(paths):
            self._guard_path(path, root)
            entries.append((self._relative_label(path, root), self.digest(path, root=root)))
        return canonical_digest(entries)
