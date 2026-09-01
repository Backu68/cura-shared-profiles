"""Cura preference persistence and one-time migration for Eventide Shared Profiles."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable

from UM.Logger import Logger


class EventidePreferences:
    """Persist local Eventide state in Cura's own preference store."""

    PREFIX = "eventide_shared_profiles"
    CONFIG_FILENAME = "eventide_shared_profiles.json"

    def __init__(self, application: Any, plugin_dir: str) -> None:
        self._preferences = application.getPreferences()
        self._plugin_dir = Path(plugin_dir)
        self._register_preferences()
        self._migrate_legacy_config_once()

    def _key(self, name: str) -> str:
        return f"{self.PREFIX}/{name}"

    def _register_preferences(self) -> None:
        defaults = {
            "shared_library_path": "",
            "client_id": "",
            "toolhead_bindings": "{}",
            "machine_bindings": "{}",
            "quality_sync_state": "{}",
            "migration_complete": False,
        }
        for name, default in defaults.items():
            self._preferences.addPreference(self._key(name), default)

    def _legacy_paths(self) -> Iterable[Path]:
        # v0.8.x first looked in a platform-agnostic path and, before that,
        # inside the plugin directory. Read either once for upgrade compatibility.
        base = (
            os.environ.get("APPDATA")
            or os.environ.get("XDG_CONFIG_HOME")
            or str(Path.home() / ".config")
        )
        yield Path(base) / "EventideSharedProfiles" / self.CONFIG_FILENAME
        yield self._plugin_dir / self.CONFIG_FILENAME

    @staticmethod
    def _read_legacy(path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("legacy Eventide configuration root is not an object")
        return data

    def _migrate_legacy_config_once(self) -> None:
        if bool(self._preferences.getValue(self._key("migration_complete"))):
            return

        for path in self._legacy_paths():
            if not path.is_file():
                continue
            try:
                data = self._read_legacy(path)
                self.save(
                    shared_library_path=str(data.get("shared_library_path", "") or "").strip(),
                    client_id=str(data.get("client_id", "") or "").strip(),
                    toolhead_bindings=self._dict_of_strings(data.get("toolhead_bindings", {})),
                    machine_bindings=self._dict_of_strings(data.get("machine_bindings", {})),
                    quality_sync_state=self._dict_of_dicts(data.get("quality_sync_state", {})),
                )
                Logger.log("i", "Eventide migrated legacy local configuration from %s into Cura preferences", str(path))
                break
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                Logger.logException("w", "Eventide could not migrate legacy configuration from %s", str(path))

        self._preferences.setValue(self._key("migration_complete"), True)

    @staticmethod
    def _dict_of_strings(value: Any) -> Dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {
            str(key): str(item).strip()
            for key, item in value.items()
            if str(key).strip() and str(item).strip()
        }

    @staticmethod
    def _dict_of_dicts(value: Any) -> Dict[str, Dict[str, Any]]:
        if not isinstance(value, dict):
            return {}
        return {
            str(key): dict(item)
            for key, item in value.items()
            if str(key).strip() and isinstance(item, dict)
        }

    def _json_preference(self, name: str) -> Dict[str, Any]:
        raw = self._preferences.getValue(self._key(name))
        if isinstance(raw, dict):
            return dict(raw)
        if not isinstance(raw, str) or not raw.strip():
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            Logger.log("w", "Eventide preference %s contains invalid JSON; using an empty object", name)
            return {}
        return dict(value) if isinstance(value, dict) else {}

    def load(self) -> Dict[str, Any]:
        return {
            "shared_library_path": str(self._preferences.getValue(self._key("shared_library_path")) or "").strip(),
            "client_id": str(self._preferences.getValue(self._key("client_id")) or "").strip(),
            "toolhead_bindings": self._dict_of_strings(self._json_preference("toolhead_bindings")),
            "machine_bindings": self._dict_of_strings(self._json_preference("machine_bindings")),
            "quality_sync_state": self._dict_of_dicts(self._json_preference("quality_sync_state")),
        }

    def save(
        self,
        *,
        shared_library_path: str,
        client_id: str,
        toolhead_bindings: Dict[str, str],
        machine_bindings: Dict[str, str],
        quality_sync_state: Dict[str, Dict[str, Any]],
    ) -> None:
        self._preferences.setValue(self._key("shared_library_path"), shared_library_path)
        self._preferences.setValue(self._key("client_id"), client_id)
        self._preferences.setValue(self._key("toolhead_bindings"), json.dumps(toolhead_bindings, sort_keys=True, separators=(",", ":")))
        self._preferences.setValue(self._key("machine_bindings"), json.dumps(machine_bindings, sort_keys=True, separators=(",", ":")))
        self._preferences.setValue(self._key("quality_sync_state"), json.dumps(quality_sync_state, sort_keys=True, separators=(",", ":")))
