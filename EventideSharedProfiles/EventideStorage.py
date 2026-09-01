"""Filesystem and record-format helpers for Eventide Shared Profiles.

This module intentionally contains no Cura or Qt dependencies. Keeping shared-library
I/O isolated makes it independently testable and keeps network/filesystem behaviour
out of the Cura-facing extension class.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple


class EventideStorage:
    """Read/write Eventide JSON records with atomic same-directory replacement."""

    def __init__(self, publisher_plugin_version: str) -> None:
        self._publisher_plugin_version = publisher_plugin_version

    @staticmethod
    def utc_now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def version_tuple(version: str) -> Tuple[int, int, int]:
        """Return a numeric three-part version, ignoring prerelease/build suffixes."""
        core = str(version or "").strip().lstrip("vV")
        core = core.split("-", 1)[0].split("+", 1)[0]
        values = []
        for part in core.split(".")[:3]:
            try:
                values.append(int(part))
            except ValueError:
                values.append(0)
        while len(values) < 3:
            values.append(0)
        return tuple(values[:3])

    def assert_publisher_compatible(self, payload: Dict[str, Any], context: str = "shared data") -> None:
        published = str(payload.get("publisher_plugin_version", "") or "").strip()
        if not published:
            return
        if self.version_tuple(published) > self.version_tuple(self._publisher_plugin_version):
            raise RuntimeError(
                f"PLUGIN UPDATE REQUIRED: {context} was published by Eventide {published}, "
                f"newer than this plugin ({self._publisher_plugin_version})."
            )

    def stamp_publisher_version(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        stamped = dict(payload)
        schema = str(stamped.get("schema", "") or "")
        is_manifest = (
            str(stamped.get("name", "") or "") == "Eventide Shared Profiles"
            and "record_format" in stamped
        )
        if schema.startswith("eventide.shared_profiles.") or is_manifest:
            stamped["publisher_plugin_version"] = self._publisher_plugin_version
        return stamped

    def write_json(self, path: str | Path, payload: Dict[str, Any]) -> None:
        """Atomically replace a JSON file using a temporary file in the same directory."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        stamped = self.stamp_publisher_version(payload)

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=str(target.parent),
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(stamped, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # The replace already succeeded or cleanup is best-effort. The caller's
                # primary write exception, if any, must remain the visible failure.
                pass

    def read_json(self, path: str | Path) -> Dict[str, Any]:
        target = Path(path)
        with target.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"JSON root must be an object: {target.name}")
        self.assert_publisher_compatible(data, target.name)
        return data

    @staticmethod
    def stable_id(prefix: str, *parts: str) -> str:
        raw = "\x1f".join(str(part or "") for part in parts).encode("utf-8")
        return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:20]}"

    @staticmethod
    def count_json_files(path: str | Path) -> int:
        directory = Path(path)
        try:
            return sum(1 for entry in directory.iterdir() if entry.is_file() and entry.suffix.casefold() == ".json")
        except OSError:
            return 0

    @staticmethod
    def library_content_signature(root: str | Path) -> Optional[str]:
        """Fingerprint shared record metadata without reading record contents.

        This function has no Cura/Qt dependencies and is safe to run on the
        Eventide filesystem worker. Missing libraries return ``None``; individual
        files disappearing during a scan are represented as ``missing`` so a
        concurrent writer does not crash the monitor.
        """
        library = Path(root)
        if not library.is_dir():
            return None

        digest = hashlib.sha256()
        paths = [library / ".eventide" / "library.json"]
        for folder_name in ("printers", "filaments", "capabilities", "quality"):
            folder = library / folder_name
            try:
                paths.extend(
                    sorted(
                        (entry for entry in folder.iterdir() if entry.is_file() and entry.suffix.casefold() == ".json"),
                        key=lambda entry: entry.name.casefold(),
                    )
                )
            except OSError:
                continue

        for path in paths:
            try:
                relative = path.relative_to(library).as_posix()
            except ValueError:
                relative = path.name
            digest.update(relative.encode("utf-8", errors="replace"))
            try:
                stat = path.stat()
                digest.update(str(int(stat.st_mtime_ns)).encode("ascii"))
                digest.update(str(int(stat.st_size)).encode("ascii"))
            except OSError:
                digest.update(b"missing")
        return digest.hexdigest()

    @staticmethod
    def json_safe_value(value: Any) -> Any:
        """Convert supported values to JSON-safe structures; reject opaque objects."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [EventideStorage.json_safe_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): EventideStorage.json_safe_value(item)
                for key, item in value.items()
            }
        raise TypeError(f"unsupported JSON value type: {type(value).__name__}")
