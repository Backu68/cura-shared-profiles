import hashlib
import json
import os
import re
import socket
import sys
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, cast

from PyQt6.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot

from UM.Extension import Extension
from UM.Logger import Logger
from cura.CuraApplication import CuraApplication
from cura.Settings.CuraContainerRegistry import CuraContainerRegistry


class EventideSharedProfiles(QObject, Extension):
    """Shared profile library extension for Cura."""

    stateChanged = pyqtSignal()

    CONFIG_FILENAME = "eventide_shared_profiles.json"
    LIBRARY_FORMAT = 1
    RECORD_FORMAT = 1

    def __init__(self) -> None:
        QObject.__init__(self, None)
        Extension.__init__(self)

        self._application = CuraApplication.getInstance()
        self._window: Optional[QObject] = None

        self._active_printer_name = "No active printer"
        self._active_printer_id = ""
        self._active_material_name = "No active material"
        self._active_material_id = ""

        self._shared_library_path = ""
        self._client_id = ""
        self._status = "Plugin loaded."

        self._printer_count = 0
        self._filament_count = 0
        self._capability_count = 0
        self._quality_count = 0
        self._current_registration = "Not checked"

        # Current capability editor state. Keep everything as display strings
        # except revision so QML can represent "unset" values as blank fields.
        self._capability_loaded = False
        self._capability_record_id = ""
        self._capability_revision = 0
        self._capability_max_volumetric_flow = ""
        self._capability_max_linear_speed = ""
        self._capability_pressure_advance = ""
        self._capability_temperature_offset = ""
        self._capability_retraction_distance = ""
        self._capability_retraction_speed = ""
        self._capability_nozzle_diameter = ""
        self._capability_nozzle_material = ""
        self._capability_notes = ""
        self._capability_last_calibrated = ""
        self._capability_emit_klipper_pa = False

        # Active Cura toolhead identity.
        self._active_extruder_position = 0
        self._active_nozzle_diameter = ""

        # Transient slice-time integration state. Eventide never writes these
        # capability values into Cura's persistent userChanges container.
        self._slice_hook_installed = False
        self._slice_capability_snapshots: Dict[int, Dict[str, Any]] = {}
        self._last_slice_resolution = "Slice-time hook not checked yet."
        self._last_gcode_guardrail_summary = "No Eventide G-code guardrail run yet."

        self._plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self._legacy_config_path = os.path.join(self._plugin_dir, self.CONFIG_FILENAME)
        self._config_path = self._stable_config_path()
        self._load_config()

        if not self._client_id:
            self._client_id = str(uuid.uuid4())
            try:
                self._save_config()
            except Exception:
                Logger.logException("e", "Eventide Shared Profiles could not create client id")

        self._runtime_hooks_connected = False
        self._slice_hook_retry_count = 0

        # Safe startup activation: only connect the signal here. Do not touch
        # MachineManager, active stacks, or CuraEngine state until Cura tells
        # us initialization has completed.
        self._application.initializationFinished.connect(
            self._on_cura_initialized
        )

        # IMPORTANT: Do not touch MachineManager / active machine state here.
        # Cura loads extensions before all application services are ready.
        # Runtime hooks are connected lazily the first time the user opens
        # the Eventide window.
        self.addMenuItem("Eventide Shared Profiles", self.showWindow)

        Logger.log("i", "Eventide Shared Profiles v0.6.2 loaded")

    def _stable_config_path(self) -> str:
        """Local Eventide config that survives plugin-folder replacement."""
        base = (
            os.environ.get("APPDATA")
            or os.environ.get("XDG_CONFIG_HOME")
            or os.path.join(os.path.expanduser("~"), ".config")
        )
        return os.path.join(base, "EventideSharedProfiles", self.CONFIG_FILENAME)

    def _load_config(self) -> None:
        try:
            source_path = self._config_path
            if (
                not os.path.isfile(source_path)
                and os.path.isfile(self._legacy_config_path)
            ):
                source_path = self._legacy_config_path

            if not os.path.isfile(source_path):
                return

            with open(source_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self._shared_library_path = str(
                data.get("shared_library_path", "") or ""
            ).strip()
            self._client_id = str(data.get("client_id", "") or "").strip()

            if source_path == self._legacy_config_path:
                self._save_config()
        except Exception:
            Logger.logException(
                "e",
                "Eventide Shared Profiles could not load config",
            )

    def _save_config(self) -> None:
        self._atomic_write_json(
            self._config_path,
            {
                "format": 2,
                "shared_library_path": self._shared_library_path,
                "client_id": self._client_id,
            },
        )

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _writer_info(self) -> Dict[str, str]:
        return {"client_id": self._client_id, "hostname": socket.gethostname()}

    @staticmethod
    def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temp_path = "{}.tmp.{}.{}".format(path, os.getpid(), uuid.uuid4().hex[:8])
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

    @staticmethod
    def _read_json(path: str) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("JSON root must be an object")
        return data

    @staticmethod
    def _stable_id(prefix: str, *parts: str) -> str:
        raw = "\x1f".join(str(part or "") for part in parts).encode("utf-8")
        return "{}-{}".format(prefix, hashlib.sha256(raw).hexdigest()[:20])

    @staticmethod
    def _count_json_files(path: str) -> int:
        try:
            return sum(1 for name in os.listdir(path)
                       if name.lower().endswith(".json") and os.path.isfile(os.path.join(path, name)))
        except OSError:
            return 0

    def _library_root(self, requested_path: str) -> str:
        path = str(requested_path or self._shared_library_path or "").strip()
        if not path:
            raise ValueError("Shared library path is blank")
        return os.path.normpath(path)

    @staticmethod
    def _manifest_path(root: str) -> str:
        return os.path.join(root, ".eventide", "library.json")

    def _require_initialized_library(self, root: str) -> None:
        manifest_path = self._manifest_path(root)
        if not os.path.isfile(manifest_path):
            raise ValueError("No Eventide library manifest found")
        manifest = self._read_json(manifest_path)
        if manifest.get("schema") != "eventide.shared_profiles.library":
            raise ValueError("Library manifest has an unexpected schema")
        if int(manifest.get("format", 0)) != self.LIBRARY_FORMAT:
            raisY = "Library format is not supported"
            raise ValueError(raiseY)

    @pyqtSlot(str)
    def setSharedLibraryPath(self, path: str) -> None:
        self._shared_library_path = str(path or "").strip()
        self._status = "Path changed locally. Click Save Path to persist."
        self._refresh_library_state_internal()
        self.stateChanged.emit()

    @pyqtProperty(str, notify=stateChanged)
    def sliceHookStatus(self) -> str:
        if self._slice_hook_installed:
            return "ACTIVE"
        return "NOT ACTIVE"

    @pyqtSlot()r
    def _on_cura_initialized(self) -> None:
        """Cura has finished booting; arm slice hooks safely now."""
        try:
            self._ensure_runtime_hooks()
            installed = self._install_slice_settings_hook()
            if not installed:
                self._schedule_slice_hook_retry()
        except Exception:
            Logger.logException(
                "e", "Eventide startup slice-hook activation failed"
            )
            self._schedule_slice_hook_retry()
        self.stateChanged.emit()

    def _schedule_slice_hook_retry(self) -> None:
        if self._slice_hook_installed:
            return
        if self._slice_hook_retry_count >= 20:
            Logger.log("e", "Eventide gaã}8éÈZ®Ëkºwµç