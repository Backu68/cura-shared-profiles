import hashlib
import json
import os
import re
import socket
import sys
import platform
import uuid
import tempfile
from datetime import datetime
from typing import Any, Dict, Optional, Tuple, cast, List

from PyQt6.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QFileDialog

from UM.Extension import Extension
from UM.Logger import Logger
from UM.Settings.InstanceContainer import InstanceContainer
from cura.CuraApplication import CuraApplication
from cura.CuraVersion import CuraVersion
from cura.Settings.CuraContainerRegistry import CuraContainerRegistry
from cura.Settings.ExtruderManager import ExtruderManager
from cura.Machines.ContainerTree import ContainerTree

from .EventidePreferences import EventidePreferences
from .EventideLibraryMonitor import EventideLibraryScanJob, LibraryScanResult
from .EventideStorage import EventideStorage


class EventideSharedProfiles(QObject, Extension):
    """Shared profile library extension for Cura."""

    stateChanged = pyqtSignal()

    PLUGIN_VERSION = "0.9.0-alpha.4"
    PUBLISHER_PLUGIN_VERSION = "0.9.0-alpha.4"
    QUALITY_SCHEMA = "eventide.shared_profiles.quality"
    # Legacy 0.8.6 destructive tombstones are read-only compatibility input.
    # 0.8.7+ uses QUALITY_SCHEMA with is_deleted=true so the full payload survives.
    QUALITY_TOMBSTONE_SCHEMA = "eventide.shared_profiles.quality_tombstone"
    LIBRARY_FORMAT = 1
    RECORD_FORMAT = 1
    LIBRARY_POLL_INTERVAL_MS = 10000

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
        self._capability_emit_klipper_pa = False
        self._capability_flow_percent = ""
        self._capability_temperature_offset = ""
        self._capability_retraction_distance = ""
        self._capability_retraction_speed = ""
        self._capability_nozzle_diameter = ""
        self._capability_nozzle_material = ""
        self._capability_notes = ""
        self._capability_last_calibrated = ""
        self._capability_calibration_status = "uncalibrated"

        # Active Cura toolhead identity.
        self._active_extruder_position = 0
        self._active_nozzle_diameter = ""
        self._active_nozzle_material = ""

        # Transient slice-time integration state. Eventide never writes these
        # capability values into Cura's persistent userChanges container.
        self._slice_hook_installed = False
        self._slice_capability_snapshots: Dict[int, Dict[str, Any]] = {}
        self._last_slice_resolution = "Slice-time hook not checked yet."
        self._toolhead_bindings: Dict[str, str] = {}
        self._library_manifest_signature: Optional[Tuple[int, int]] = None  # legacy field; v0.8 fingerprints all record dirs
        self._library_content_signature: Optional[str] = None
        self._last_library_event = "Live sync waiting for a shared library."
        self._library_validation_summary = "Library not validated yet."
        self._last_sync_summary = "Not synchronized yet."
        self._last_quality_sync_summary = "No shared quality-profile activity yet."
        self._machine_bindings: Dict[str, str] = {}
        self._quality_sync_state: Dict[str, Dict[str, Any]] = {}
        # Runtime conflict queue. Conflicts are rediscovered from local/shared
        # hashes after restart, so the queue itself does not need persistence.
        self._quality_conflicts: Dict[str, Dict[str, Any]] = {}
        self._quality_conflict_order: List[str] = []
        self._quality_conflict_index = 0
        self._quality_conflict_dialog: Optional[QObject] = None
        self._last_quality_conflict_popup_token = ""
        self._live_sync_busy = False
        self._local_quality_dirty = True
        self._library_available = False
        self._quality_signal_sources: List[Any] = []

        self._plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self._storage = EventideStorage(self.PUBLISHER_PLUGIN_VERSION)
        self._library_scan_job: Optional[EventideLibraryScanJob] = None
        self._preferences = EventidePreferences(self._application, self._plugin_dir)
        self._load_config()

        if not self._client_id:
            self._client_id = str(uuid.uuid4())
            try:
                self._save_config()
            except Exception:
                Logger.logException("e", "Eventide Shared Profiles could not create client id")

        self._runtime_hooks_connected = False
        self._slice_hook_retry_count = 0
        self._library_poll_timer = QTimer(self)
        self._library_poll_timer.setInterval(self.LIBRARY_POLL_INTERVAL_MS)
        self._library_poll_timer.timeout.connect(self._poll_shared_library)

        # Safe startup activation: only connect the signal here. Do not touch
        # MachineManager, active stacks, or CuraEngine state until Cura tells
        # us initialization has completed.
        self._application.initializationFinished.connect(
            self._on_cura_initialized
        )

        # IMPORTANT: Do not touch MachineManager / active machine state here.
        # Cura loads extensions before all application services are ready.
        # Runtime hooks are armed from initializationFinished, never from the
        # constructor.
        self.addMenuItem("Eventide Shared Profiles", self.showWindow)

        Logger.log("i", "Eventide Shared Profiles v%s loaded", self.PLUGIN_VERSION)

    def _load_config(self) -> None:
        data = self._preferences.load()
        self._shared_library_path = data["shared_library_path"]
        self._client_id = data["client_id"]
        self._toolhead_bindings = data["toolhead_bindings"]
        self._machine_bindings = data["machine_bindings"]
        self._quality_sync_state = data["quality_sync_state"]

    def _save_config(self) -> None:
        self._preferences.save(
            shared_library_path=self._shared_library_path,
            client_id=self._client_id,
            toolhead_bindings=self._toolhead_bindings,
            machine_bindings=self._machine_bindings,
            quality_sync_state=self._quality_sync_state,
        )

    def _utc_now(self) -> str:
        return self._storage.utc_now()

    def _writer_info(self) -> Dict[str, str]:
        return {"client_id": self._client_id, "hostname": socket.gethostname()}

    @staticmethod
    def _version_tuple(version: str) -> Tuple[int, int, int]:
        return EventideStorage.version_tuple(version)

    def _atomic_write_json(self, path: str, payload: Dict[str, Any]) -> None:
        self._storage.write_json(path, payload)

    def _read_json(self, path: str) -> Dict[str, Any]:
        return self._storage.read_json(path)

    @staticmethod
    def _stable_id(prefix: str, *parts: str) -> str:
        return EventideStorage.stable_id(prefix, *parts)

    @staticmethod
    def _count_json_files(path: str) -> int:
        return EventideStorage.count_json_files(path)

    def _library_root(self, requested_path: str) -> str:
        path = str(requested_path or "").strip()
        if not path:
            raise ValueError("enter a shared library path first")
        return os.path.normpath(path)

    def _manifest_path(self, root: str) -> str:
        return os.path.join(root, ".eventide", "library.json")

    def _require_initialized_library(self, root: str) -> Dict[str, Any]:
        manifest_path = self._manifest_path(root)
        if not os.path.isfile(manifest_path):
            raise FileNotFoundError("library is not initialized; click Initialize Library first")
        manifest = self._read_json(manifest_path)
        if int(manifest.get("format", 0) or 0) != self.LIBRARY_FORMAT:
            raise ValueError(f"unsupported library format {manifest.get('format')}")
        return manifest

    def _save_library_path_from_ui(self, requested_path: str) -> str:
        root = self._library_root(requested_path)
        new_path = str(requested_path or "").strip()
        if new_path != self._shared_library_path:
            self._library_content_signature = None
            self._library_available = False
        self._shared_library_path = new_path
        self._save_config()
        QTimer.singleShot(0, self._poll_shared_library)
        return root

    def _identity_upsert(self, path: str, schema: str, record_id: str,
                         cura_id: str, cura_name: str) -> Tuple[Dict[str, Any], bool]:
        now = self._utc_now()
        writer = self._writer_info()
        if os.path.isfile(path):
            record = self._read_json(path)
            if record.get("schema") != schema:
                raise ValueError(f"record schema mismatch in {os.path.basename(path)}")
            if record.get("id") != record_id:
                raise ValueError(f"record id mismatch in {os.path.basename(path)}")
            changed = False
            cura = record.setdefault("cura", {})
            if cura.get("id") != cura_id:
                cura["id"] = cura_id
                changed = True
            if cura.get("name") != cura_name:
                cura["name"] = cura_name
                changed = True
            if changed:
                record["revision"] = int(record.get("revision", 0) or 0) + 1
                record["updated_utc"] = now
                record["updated_by"] = writer
                self._atomic_write_json(path, record)
            return record, changed

        record = {
            "schema": schema,
            "format": self.RECORD_FORMAT,
            "id": record_id,
            "revision": 1,
            "created_utc": now,
            "updated_utc": now,
            "updated_by": writer,
            "cura": {"id": cura_id, "name": cura_name},
        }
        self._atomic_write_json(path, record)
        return record, True

    @staticmethod
    def _json_safe_value(value: Any) -> Any:
        return EventideStorage.json_safe_value(value)

    def _instance_values(self, container: Any) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        if container is None:
            return values
        try:
            keys = container.getAllKeys()
        except (AttributeError, TypeError):
            Logger.logException("w", "Eventide could not enumerate container settings")
            return values
        for key in keys:
            try:
                values[str(key)] = self._json_safe_value(
                    container.getProperty(key, "value")
                )
            except Exception:
                Logger.logException("w", "Eventide could not snapshot setting %s", key)
        return values

    def _capture_active_material_definition(self) -> Dict[str, Any]:
        """Serialize the logical root material so another Cura can import it."""
        stack = self._get_active_extruder_stack()
        if stack is None:
            raise ValueError("Cura has no active extruder stack")
        material = getattr(stack, "material", None)
        if material is None:
            raise ValueError("Cura has no active material")

        registry = CuraContainerRegistry.getInstance()
        base_file = ""
        guid = ""
        try:
            base_file = str(material.getMetaDataEntry("base_file", "") or "").strip()
            guid = str(material.getMetaDataEntry("GUID", "") or "").strip()
        except (AttributeError, TypeError):
            Logger.logException("w", "Eventide could not read active material metadata")
        root = material
        if base_file:
            try:
                roots = registry.findInstanceContainers(id=base_file)
                if roots:
                    root = roots[0]
            except (KeyError, RuntimeError, ValueError):
                Logger.logException("w", "Eventide could not resolve the root material container %s", base_file)
        if not guid:
            try:
                guid = str(root.getMetaDataEntry("GUID", "") or "").strip()
            except (AttributeError, TypeError):
                Logger.logException("w", "Eventide could not read root material GUID")

        root_id = str(root.getId() or "").strip()
        # Failing to determine mutability is not something we should silently
        # reinterpret as a custom/writable material. Let the caller surface it.
        readonly = bool(registry.isReadOnly(root_id))

        metadata: Dict[str, Any] = {}
        try:
            raw_metadata = root.getMetaData()
            if isinstance(raw_metadata, dict):
                for key in ("GUID", "base_file", "name", "brand", "material", "color_name", "color_code", "diameter", "setting_version"):
                    if key in raw_metadata:
                        metadata[key] = self._json_safe_value(raw_metadata.get(key))
        except (AttributeError, TypeError, ValueError):
            Logger.logException("w", "Eventide could not capture optional material metadata")

        serialized = ""
        if not readonly:
            serialized = str(root.serialize() or "")
            if not serialized.strip():
                raise ValueError("active custom material could not be serialized")

        return {
            "format": "cura_material_xml_v1",
            "guid": guid,
            "source_container_id": root_id,
            "source_base_file": base_file or root_id,
            "name": str(root.getName() or self._active_material_name),
            "readonly_builtin": readonly,
            "metadata": metadata,
            "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest() if serialized else "",
            "serialized": serialized,
        }

    def _capture_active_machine_definition(self) -> Dict[str, Any]:
        """Capture machine-instance definition changes, not transient slicing userChanges."""
        global_stack = self._application.getGlobalContainerStack()
        if global_stack is None:
            raise ValueError("Cura has no active machine stack")

        definition = getattr(global_stack, "definition", None)
        if definition is None:
            raise ValueError("active machine has no definition")

        extruders = []
        for extruder in list(getattr(global_stack, "extruderList", []) or []):
            position = 0
            try:
                position = int(extruder.getMetaDataEntry("position") or 0)
            except (AttributeError, TypeError, ValueError):
                Logger.logException("w", "Eventide could not read extruder position while capturing the machine")
            extruder_definition = getattr(extruder, "definition", None)
            extruders.append({
                "position": position,
                "definition_id": str(extruder_definition.getId() if extruder_definition is not None else ""),
                "definition_changes": self._instance_values(getattr(extruder, "definitionChanges", None)),
                "enabled": bool(getattr(extruder, "isEnabled", True)),
            })

        return {
            "format": "cura_machine_instance_v1",
            "source_stack_id": str(global_stack.getId() or ""),
            "name": str(global_stack.getName() or self._active_printer_name),
            "definition_id": str(definition.getId() or ""),
            "definition_name": str(definition.getName() or ""),
            "global_definition_changes": self._instance_values(getattr(global_stack, "definitionChanges", None)),
            "extruders": sorted(extruders, key=lambda item: int(item.get("position", 0))),
        }

    def _update_record_section(self, path: str, section: str, payload: Dict[str, Any]) -> bool:
        record = self._read_json(path)
        if record.get(section) == payload:
            return False
        record[section] = payload
        record["revision"] = int(record.get("revision", 0) or 0) + 1
        record["updated_utc"] = self._utc_now()
        record["updated_by"] = self._writer_info()
        self._atomic_write_json(path, record)
        return True

    def _capability_upsert(self, path: str, capability_id: str,
                           printer_record_id: str, filament_record_id: str) -> Tuple[Dict[str, Any], bool]:
        if os.path.isfile(path):
            record = self._read_json(path)
            if record.get("schema") != "eventide.shared_profiles.capability":
                raise ValueError("capability schema mismatch")
            if record.get("id") != capability_id:
                raise ValueError("capability id mismatch")
            return record, False

        now = self._utc_now()
        record = {
            "schema": "eventide.shared_profiles.capability",
            "format": self.RECORD_FORMAT,
            "id": capability_id,
            "revision": 1,
            "created_utc": now,
            "updated_utc": now,
            "updated_by": self._writer_info(),
            "printer_id": printer_record_id,
            "filament_id": filament_record_id,
            "extruder": 0,
            "hotend": {"nozzle_diameter_mm": None, "nozzle_material": None},
            "limits": {"max_volumetric_flow_mm3_s": None, "max_linear_speed_mm_s": None},
            "tuning": {
                "pressure_advance": None,
                "emit_klipper_pressure_advance": False,
                "flow_percent": None,
                "temperature_offset_c": None,
                "retraction_distance_mm": None,
                "retraction_speed_mm_s": None,
            },
            "calibration": {"status": "uncalibrated", "last_calibrated_utc": None, "notes": ""},
        }
        self._atomic_write_json(path, record)
        return record, True

    def _touch_manifest(self, root: str) -> None:
        manifest_path = self._manifest_path(root)
        manifest = self._require_initialized_library(root)
        manifest["updated_utc"] = self._utc_now()
        manifest["updated_by"] = self._writer_info()
        manifest["record_format"] = self.RECORD_FORMAT
        self._atomic_write_json(manifest_path, manifest)

    def _shared_printer_record_id(self, local_printer_id: Optional[str] = None) -> str:
        """Resolve the stable Eventide printer record for the active/local Cura machine.

        Imported machine instances may receive a different local Cura container id on
        each computer.  Eventide stamps the shared record id onto imported stacks and
        also persists a record->local binding; prefer those identities before falling
        back to the legacy hash of the local Cura id.
        """
        local_id = str(local_printer_id or self._active_printer_id or "").strip()
        if not local_id:
            raise ValueError("Cura does not have an active printer id")

        try:
            global_stack = self._application.getGlobalContainerStack()
            if global_stack is not None and str(global_stack.getId() or "").strip() == local_id:
                shared_id = str(global_stack.getMetaDataEntry("eventide_record_id", "") or "").strip()
                if shared_id:
                    return shared_id
        except (AttributeError, TypeError):
            Logger.logException("w", "Eventide could not read the active machine's shared identity")

        for record_id, bound_local_id in self._machine_bindings.items():
            if str(bound_local_id or "").strip() == local_id and str(record_id or "").strip():
                return str(record_id).strip()

        return self._stable_id("printer", local_id)

    def _base_record_ids(self) -> Tuple[str, str]:
        if not self._active_printer_id:
            raise ValueError("Cura does not have an active printer id")
        if not self._active_material_id:
            raise ValueError("Cura does not have an active material id")

        return (
            self._shared_printer_record_id(self._active_printer_id),
            self._stable_id("filament", self._active_material_id),
        )

    @staticmethod
    def _normalize_toolhead_text(value: Any) -> str:
        text_value = str(value or "").strip().lower()
        return re.sub(r"[^a-z0-9._+-]+", "-", text_value).strip("-") or "unspecified"

    @staticmethod
    def _normalize_nozzle_diameter(value: Any) -> str:
        try:
            number = float(value)
            return "{:.3f}".format(number).rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            return "unknown"

    def _canonical_capability_id(
        self,
        printer_record_id: str,
        filament_record_id: str,
        extruder_position: int,
        nozzle_diameter: Any,
        nozzle_material: Any,
    ) -> str:
        toolhead_key = "extruder-{}|nozzle-{}|material-{}".format(
            int(extruder_position),
            self._normalize_nozzle_diameter(nozzle_diameter),
            self._normalize_toolhead_text(nozzle_material),
        )
        return self._stable_id(
            "capability",
            printer_record_id,
            filament_record_id,
            toolhead_key,
        )

    def _toolhead_binding_key(
        self,
        printer_id: str,
        extruder_position: int,
        nozzle_diameter: Any,
    ) -> str:
        return "{}|extruder-{}|nozzle-{}".format(
            str(printer_id or "").strip(),
            int(extruder_position),
            self._normalize_nozzle_diameter(nozzle_diameter),
        )

    def _bound_nozzle_material(
        self,
        printer_id: str,
        extruder_position: int,
        nozzle_diameter: Any,
    ) -> str:
        key = self._toolhead_binding_key(
            printer_id, extruder_position, nozzle_diameter
        )
        return str(self._toolhead_bindings.get(key, "") or "").strip()

    def _bind_nozzle_material(
        self,
        printer_id: str,
        extruder_position: int,
        nozzle_diameter: Any,
        nozzle_material: str,
    ) -> None:
        key = self._toolhead_binding_key(
            printer_id, extruder_position, nozzle_diameter
        )
        material = str(nozzle_material or "").strip()
        if material:
            self._toolhead_bindings[key] = material
        else:
            self._toolhead_bindings.pop(key, None)
        self._save_config()

    def _active_toolhead(self) -> Tuple[int, Optional[float]]:
        stack = self._get_active_extruder_stack()
        if stack is None:
            return 0, None

        try:
            position = int(stack.getMetaDataEntry("position", 0) or 0)
        except (TypeError, ValueError):
            position = 0

        try:
            nozzle = stack.getProperty("machine_nozzle_size", "value")
            nozzle = float(nozzle) if nozzle is not None else None
        except (TypeError, ValueError):
            nozzle = None

        return position, nozzle

    @staticmethod
    def _display_number(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            return "{:g}".format(value)
        return str(value)

    @staticmethod
    def _optional_float(
        raw_value: Any,
        field_name: str,
        minimum: Optional[float] = None,
        strictly_positive: bool = False,
    ) -> Optional[float]:
        text_value = str(raw_value if raw_value is not None else "").strip()
        if not text_value:
            return None

        try:
            value = float(text_value)
        except (TypeError, ValueError):
            raise ValueError("{} must be a number or blank".format(field_name))

        if strictly_positive and value <= 0:
            raise ValueError("{} must be greater than 0".format(field_name))
        if minimum is not None and value < minimum:
            raise ValueError("{} must be at least {}".format(field_name, minimum))
        return value

    def _clear_capability_editor(self) -> None:
        self._capability_loaded = False
        self._capability_record_id = ""
        self._capability_revision = 0
        self._capability_max_volumetric_flow = ""
        self._capability_max_linear_speed = ""
        self._capability_pressure_advance = ""
        self._capability_emit_klipper_pa = False
        self._capability_flow_percent = ""
        self._capability_temperature_offset = ""
        self._capability_retraction_distance = ""
        self._capability_retraction_speed = ""
        self._capability_nozzle_diameter = ""
        self._capability_nozzle_material = ""
        self._capability_notes = ""
        self._capability_last_calibrated = ""
        self._capability_calibration_status = "uncalibrated"

    def _set_capability_editor_from_record(self, record: Dict[str, Any]) -> None:
        limits = record.get("limits", {})
        tuning = record.get("tuning", {})
        hotend = record.get("hotend", {})
        calibration = record.get("calibration", {})

        self._capability_loaded = True
        self._capability_record_id = str(record.get("id", "") or "")
        self._capability_revision = int(record.get("revision", 0) or 0)

        self._capability_max_volumetric_flow = self._display_number(
            limits.get("max_volumetric_flow_mm3_s")
        )
        self._capability_max_linear_speed = self._display_number(
            limits.get("max_linear_speed_mm_s")
        )
        self._capability_pressure_advance = self._display_number(
            tuning.get("pressure_advance")
        )
        self._capability_emit_klipper_pa = bool(
            tuning.get("emit_klipper_pressure_advance", False)
        )
        self._capability_flow_percent = self._display_number(
            tuning.get("flow_percent")
        )
        self._capability_temperature_offset = self._display_number(
            tuning.get("temperature_offset_c")
        )
        self._capability_retraction_distance = self._display_number(
            tuning.get("retraction_distance_mm")
        )
        self._capability_retraction_speed = self._display_number(
            tuning.get("retraction_speed_mm_s")
        )
        self._capability_nozzle_diameter = self._display_number(
            hotend.get("nozzle_diameter_mm")
        )
        self._capability_nozzle_material = str(
            hotend.get("nozzle_material", "") or ""
        )
        self._capability_notes = str(calibration.get("notes", "") or "")
        self._capability_last_calibrated = str(
            calibration.get("last_calibrated_utc", "") or ""
        )
        self._capability_calibration_status = str(
            calibration.get("status", "uncalibrated") or "uncalibrated"
        )

    def _capability_candidates(
        self,
        root: str,
        preferred_nozzle_material: Optional[str] = None,
    ) -> list:
        printer_record_id, filament_record_id = self._base_record_ids()
        position, active_nozzle = self._active_toolhead()
        folder = os.path.join(root, "capabilities")
        results = []

        if not os.path.isdir(folder):
            return results

        preferred_norm = (
            self._normalize_toolhead_text(preferred_nozzle_material)
            if preferred_nozzle_material
            else None
        )

        for name in os.listdir(folder):
            if not name.lower().endswith(".json"):
                continue

            path = os.path.join(folder, name)
            try:
                record = self._read_json(path)
            except Exception:
                continue

            if record.get("schema") != "eventide.shared_profiles.capability":
                continue
            if record.get("printer_id") != printer_record_id:
                continue
            if record.get("filament_id") != filament_record_id:
                continue

            try:
                record_position = int(record.get("extruder", 0) or 0)
            except (TypeError, ValueError):
                record_position = 0
            if record_position != position:
                continue

            hotend = record.get("hotend", {})
            record_nozzle = hotend.get("nozzle_diameter_mm")

            # Legacy records may have a blank nozzle. Accept them as a migration
            # candidate. Otherwise require the active Cura nozzle to match.
            if active_nozzle is not None and record_nozzle not in (None, ""):
                try:
                    if abs(float(record_nozzle) - float(active_nozzle)) > 0.0005:
                        continue
                except (TypeError, ValueError):
                    continue

            record_material = str(hotend.get("nozzle_material", "") or "")
            if preferred_norm is not None:
                if self._normalize_toolhead_text(record_material) != preferred_norm:
                    continue

            results.append((path, record))

        return results

    def _find_current_capability(
        self,
        root: str,
        preferred_nozzle_material: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        candidates = self._capability_candidates(
            root,
            preferred_nozzle_material=preferred_nozzle_material,
        )

        if not candidates:
            raise FileNotFoundError(
                "no capability record matches the current printer/material/nozzle"
            )

        if len(candidates) == 1:
            return candidates[0]

        # Prefer the persistent local toolhead binding. Cura tracks nozzle
        # diameter but generally does not expose nozzle material.
        bound_material = str(self._active_nozzle_material or "").strip()
        if bound_material and not preferred_nozzle_material:
            narrowed = self._capability_candidates(
                root,
                preferred_nozzle_material=bound_material,
            )
            if len(narrowed) == 1:
                return narrowed[0]

        # Editor state remains a fallback for legacy local configs.
        editor_material = str(self._capability_nozzle_material or "").strip()
        if editor_material and not preferred_nozzle_material:
            narrowed = self._capability_candidates(
                root,
                preferred_nozzle_material=editor_material,
            )
            if len(narrowed) == 1:
                return narrowed[0]

        raise ValueError(
            "multiple capability records match this printer/material/nozzle; "
            "select the intended nozzle material"
        )

    def _create_capability_record(
        self,
        path: str,
        capability_id: str,
        printer_record_id: str,
        filament_record_id: str,
        nozzle_diameter: Optional[float],
        nozzle_material: str,
        extruder_position: int,
    ) -> Dict[str, Any]:
        now = self._utc_now()
        record = {
            "schema": "eventide.shared_profiles.capability",
            "format": self.RECORD_FORMAT,
            "id": capability_id,
            "revision": 1,
            "created_utc": now,
            "updated_utc": now,
            "updated_by": self._writer_info(),
            "printer_id": printer_record_id,
            "filament_id": filament_record_id,
            "extruder": int(extruder_position),
            "hotend": {
                "nozzle_diameter_mm": nozzle_diameter,
                "nozzle_material": nozzle_material,
            },
            "limits": {
                "max_volumetric_flow_mm3_s": None,
                "max_linear_speed_mm_s": None,
            },
            "tuning": {
                "pressure_advance": None,
                "emit_klipper_pressure_advance": False,
                "flow_percent": None,
                "temperature_offset_c": None,
                "retraction_distance_mm": None,
                "retraction_speed_mm_s": None,
            },
            "calibration": {
                "status": "uncalibrated",
                "last_calibrated_utc": None,
                "notes": "",
            },
        }
        self._atomic_write_json(path, record)
        return record

    def _current_capability_path(self, root: str) -> Tuple[str, str]:
        path, record = self._find_current_capability(root)
        return str(record.get("id", "") or ""), path

    @staticmethod
    def _dict_float(values: Dict[str, Any], key: str) -> Optional[float]:
        try:
            value = values.get(key)
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _slice_identity_for_stack(
        self,
        stack: Any,
        settings: Dict[str, Any],
    ) -> Tuple[str, str, str, str, int, Optional[float]]:
        global_stack = self._application.getGlobalContainerStack()
        if global_stack is None:
            raise ValueError("Cura has no active printer stack")

        printer_name = str(global_stack.getName() or "No active printer")
        printer_id = str(global_stack.getId() or "").strip()
        if not printer_id:
            raise ValueError("Cura does not have an active printer id")

        material = getattr(stack, "material", None)
        if material is None:
            raise ValueError("stack has no material")

        material_name = str(material.getName() or "No active material")
        material_id, _ = self._resolve_material_identity(
            material,
            material_name,
        )
        if not material_id:
            raise ValueError("Cura does not have an active material id")

        try:
            position = int(stack.getMetaDataEntry("position", 0) or 0)
        except (TypeError, ValueError):
            position = 0

        nozzle = self._dict_float(settings, "machine_nozzle_size")
        if nozzle is None:
            try:
                raw_nozzle = stack.getProperty("machine_nozzle_size", "value")
                nozzle = float(raw_nozzle) if raw_nozzle is not None else None
            except (TypeError, ValueError, AttributeError):
                nozzle = None

        return (
            printer_name,
            printer_id,
            material_name,
            material_id,
            position,
            nozzle,
        )

    def _find_capability_for_slice_stack(
        self,
        root: str,
        stack: Any,
        settings: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        (
            printer_name,
            printer_id,
            material_name,
            material_id,
            position,
            nozzle,
        ) = self._slice_identity_for_stack(stack, settings)

        printer_record_id = self._shared_printer_record_id(printer_id)
        filament_record_id = self._stable_id("filament", material_id)
        folder = os.path.join(root, "capabilities")
        candidates = []

        if os.path.isdir(folder):
            for name in os.listdir(folder):
                if not name.lower().endswith(".json"):
                    continue
                path = os.path.join(folder, name)
                try:
                    record = self._read_json(path)
                except Exception:
                    continue

                if record.get("schema") != "eventide.shared_profiles.capability":
                    continue
                if record.get("printer_id") != printer_record_id:
                    continue
                if record.get("filament_id") != filament_record_id:
                    continue

                try:
                    record_position = int(record.get("extruder", 0) or 0)
                except (TypeError, ValueError):
                    record_position = 0
                if record_position != position:
                    continue

                record_nozzle = record.get("hotend", {}).get("nozzle_diameter_mm")
                if nozzle is not None and record_nozzle not in (None, ""):
                    try:
                        if abs(float(record_nozzle) - nozzle) > 0.0005:
                            continue
                    except (TypeError, ValueError):
                        continue

                candidates.append((path, record))

        if not candidates:
            raise FileNotFoundError(
                "no Eventide capability matches {} + {} + extruder {} + nozzle {} mm".format(
                    printer_name,
                    material_name,
                    position,
                    self._display_number(nozzle) or "unknown",
                )
            )

        if len(candidates) > 1:
            # Cura normally knows nozzle diameter but not nozzle material. Eventide
            # stores a local, per-printer/extruder/nozzle material binding so slice
            # resolution remains automatic after Cura/plugin restarts.
            bound_material = self._bound_nozzle_material(
                printer_id, position, nozzle
            )
            chosen_path = None
            chosen_record = None
            if bound_material:
                bound_norm = self._normalize_toolhead_text(bound_material)
                for path, record in candidates:
                    record_material = record.get("hotend", {}).get(
                        "nozzle_material", ""
                    )
                    if self._normalize_toolhead_text(record_material) == bound_norm:
                        if chosen_record is not None:
                            raise ValueError(
                                "multiple Eventide capabilities match the bound nozzle material {}".format(
                                    bound_material
                                )
                            )
                        chosen_path, chosen_record = path, record

            # Editor state remains a backward-compatible fallback for old local
            # configs that do not have a toolhead binding yet.
            if chosen_record is None and self._capability_loaded and self._capability_record_id:
                for path, record in candidates:
                    if str(record.get("id", "") or "") == self._capability_record_id:
                        chosen_path, chosen_record = path, record
                        break

            if chosen_record is None:
                raise ValueError(
                    "multiple Eventide capabilities match this printer/material/nozzle; "
                    "set the active nozzle material in Eventide"
                )
        else:
            chosen_path, chosen_record = candidates[0]

        context = {
            "path": chosen_path,
            "printer_name": printer_name,
            "printer_id": printer_id,
            "material_name": material_name,
            "material_id": material_id,
            "extruder": position,
            "nozzle_diameter_mm": nozzle,
        }
        return chosen_record, context

    def _apply_capability_to_slice_values(
        self,
        settings: Dict[str, Any],
        record: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], list]:
        values = dict(settings)
        limits = record.get("limits", {})
        tuning = record.get("tuning", {})
        changed = []

        # Optional calibrated material-flow multiplier. StartSliceJob has already
        # resolved Cura setting functions into concrete numbers, so changing only
        # the parent material_flow value would not recalculate child flow values.
        # Scale the copied material-flow family together, preserving intentional
        # per-feature differences from the selected Cura profile.
        target_flow = tuning.get("flow_percent")
        if target_flow not in (None, ""):
            target_flow = float(target_flow)
            base_flow = self._dict_float(values, "material_flow") or 100.0
            if base_flow <= 0:
                base_flow = 100.0
            scale = target_flow / base_flow
            for key in list(values.keys()):
                if key == "material_flow" or key == "material_flow_layer_0" or key.endswith("_material_flow") or key.endswith("_material_flow_layer_0"):
                    current_value = self._dict_float(values, key)
                    if current_value is None:
                        continue
                    new_value = current_value * scale
                    values[key] = new_value
            changed.append(
                "material flow family scaled to {:g}% (base {:g}%)".format(
                    target_flow, base_flow
                )
            )

        max_flow = limits.get("max_volumetric_flow_mm3_s")
        max_linear = limits.get("max_linear_speed_mm_s")
        max_flow = float(max_flow) if max_flow not in (None, "") else None
        max_linear = float(max_linear) if max_linear not in (None, "") else None

        layer_height = self._dict_float(values, "layer_height")
        layer_height_0 = self._dict_float(values, "layer_height_0")
        material_flow = self._dict_float(values, "material_flow") or 100.0
        flow_factor = max(material_flow / 100.0, 0.0001)

        speed_map = [
            ("speed_print", "line_width", False),
            ("speed_infill", "infill_line_width", False),
            ("speed_wall", "wall_line_width", False),
            ("speed_wall_0", "wall_line_width_0", False),
            ("speed_wall_x", "wall_line_width_x", False),
            ("speed_topbottom", "skin_line_width", False),
            ("speed_roofing", "skin_line_width", False),
            ("speed_support", "support_line_width", False),
            ("speed_support_interface", "support_interface_line_width", False),
            ("speed_prime_tower", "prime_tower_line_width", False),
            ("speed_skirt_brim", "skirt_brim_line_width", False),
            ("speed_layer_0", "line_width", True),
        ]

        for speed_key, width_key, first_layer in speed_map:
            current_speed = self._dict_float(values, speed_key)
            if current_speed is None:
                continue

            caps = [current_speed]
            if max_linear is not None:
                caps.append(max_linear)

            current_layer_height = layer_height_0 if first_layer else layer_height
            line_width = self._dict_float(values, width_key)
            if (
                max_flow is not None
                and current_layer_height is not None
                and current_layer_height > 0
                and line_width is not None
                and line_width > 0
            ):
                caps.append(
                    max_flow / (current_layer_height * line_width * flow_factor)
                )

            target = min(caps)
            if target < current_speed - 0.0001:
                values[speed_key] = target
                changed.append(
                    "{}={:.3f} (was {:.3f})".format(
                        speed_key,
                        target,
                        current_speed,
                    )
                )

        # CuraEngine can otherwise increase thin Arachne wall speed after the
        # nominal wall speed has been capped. Disable that compensation only in
        # the transient slice copy when a hard linear ceiling is requested.
        if max_linear is not None and "speed_equalize_flow_width_factor" in values:
            old_equalize = self._dict_float(values, "speed_equalize_flow_width_factor")
            if old_equalize is None or abs(old_equalize) > 0.000001:
                values["speed_equalize_flow_width_factor"] = 0.0
                changed.append("speed_equalize_flow_width_factor=0 (slice-only)")

        retract_distance = tuning.get("retraction_distance_mm")
        if retract_distance not in (None, "") and "retraction_amount" in values:
            values["retraction_amount"] = float(retract_distance)
            changed.append("retraction_amount={:g}".format(float(retract_distance)))

        retract_speed = tuning.get("retraction_speed_mm_s")
        if retract_speed not in (None, ""):
            retract_speed = float(retract_speed)
            for key in ("retraction_retract_speed", "retraction_prime_speed"):
                if key in values:
                    values[key] = retract_speed
                    changed.append("{}={:g}".format(key, retract_speed))

        temp_offset = tuning.get("temperature_offset_c")
        if temp_offset not in (None, ""):
            offset = float(temp_offset)
            for key in (
                "material_print_temperature",
                "material_print_temperature_layer_0",
            ):
                base_value = self._dict_float(values, key)
                if base_value is not None:
                    values[key] = base_value + offset
                    changed.append("{}={:g}".format(key, base_value + offset))

        return values, changed

    def _apply_klipper_pa_to_global_slice_values(
        self,
        settings: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], str]:
        """Add capability PA to Cura's transient copied machine start G-code.

        This runs inside the existing StartSliceJob copied-settings hook. It does
        not mutate Cura's live stack and does not rewrite finished G-code. Alpha.4
        deliberately supports exactly one enabled extruder until Eventide has an
        explicit Cura-to-Klipper extruder-name mapping.
        """
        values = dict(settings)
        if not self._shared_library_path:
            return values, ""

        root = os.path.normpath(self._shared_library_path)
        if not os.path.isfile(self._manifest_path(root)):
            return values, ""

        global_stack = self._application.getGlobalContainerStack()
        if global_stack is None:
            return values, "Klipper PA not emitted: no active printer stack"

        enabled_extruders = [
            extruder
            for extruder in list(getattr(global_stack, "extruderList", []) or [])
            if bool(getattr(extruder, "isEnabled", True))
        ]
        if len(enabled_extruders) != 1:
            return values, "Klipper PA not emitted: requires exactly one enabled extruder"

        extruder_stack = enabled_extruders[0]
        effective_settings: Dict[str, Any] = {}
        try:
            keys = extruder_stack.getAllKeys()
        except (AttributeError, TypeError):
            keys = []
        for key in keys:
            try:
                effective_settings[str(key)] = extruder_stack.getProperty(key, "value")
            except Exception:
                Logger.logException(
                    "w",
                    "Eventide could not read extruder setting %s while resolving Klipper PA",
                    key,
                )

        try:
            record, context = self._find_capability_for_slice_stack(
                root, extruder_stack, effective_settings
            )
        except FileNotFoundError:
            return values, ""
        except Exception as error:
            Logger.logException("e", "Eventide Klipper PA slice resolution failed")
            return values, "Klipper PA not emitted: {}".format(error)

        tuning = record.get("tuning", {})
        if not bool(tuning.get("emit_klipper_pressure_advance", False)):
            return values, ""

        pressure_advance = tuning.get("pressure_advance")
        if pressure_advance in (None, ""):
            return values, "Klipper PA not emitted: enabled but value is unset"
        try:
            pressure_advance = float(pressure_advance)
        except (TypeError, ValueError):
            return values, "Klipper PA not emitted: invalid value"
        if pressure_advance < 0:
            return values, "Klipper PA not emitted: value must be at least 0"

        if "machine_start_gcode" not in values:
            return values, "Klipper PA not emitted: machine_start_gcode unavailable"

        start_gcode = str(values.get("machine_start_gcode", "") or "")
        command = "SET_PRESSURE_ADVANCE ADVANCE={:g} ; Eventide Shared Profiles".format(
            pressure_advance
        )
        separator = "" if not start_gcode or start_gcode.endswith(("\n", "\r")) else "\n"
        values["machine_start_gcode"] = start_gcode + separator + command
        return values, "Klipper PA={:g} ({})".format(
            pressure_advance, context.get("material_name", "material")
        )

    def _transform_slice_settings(
        self,
        stack: Any,
        original_values: Dict[str, Any],
    ) -> Dict[str, Any]:
        # StartSliceJob asks for global settings first. GlobalStack has no
        # material, so use that call as the boundary that clears the previous
        # slice's capability snapshots. This prevents stale material data from
        # ever leaking into a later slice.
        if getattr(stack, "material", None) is None:
            self._slice_capability_snapshots = {}
            transformed, pa_note = self._apply_klipper_pa_to_global_slice_values(
                original_values
            )
            self._last_slice_resolution = (
                "Slice started; resolving Eventide capabilities per extruder."
                + ((" " + pa_note) if pa_note else "")
            )
            return transformed

        if not self._shared_library_path:
            return original_values

        root = os.path.normpath(self._shared_library_path)
        if not os.path.isfile(self._manifest_path(root)):
            return original_values

        try:
            record, context = self._find_capability_for_slice_stack(
                root,
                stack,
                original_values,
            )
            transformed, changed = self._apply_capability_to_slice_values(
                original_values,
                record,
            )

            position = int(context["extruder"])
            filament_diameter = self._dict_float(
                transformed,
                "material_diameter",
            ) or 1.75
            snapshot = {
                "record": json.loads(json.dumps(record)),
                "printer_name": context["printer_name"],
                "material_name": context["material_name"],
                "extruder": position,
                "nozzle_diameter_mm": context["nozzle_diameter_mm"],
                "filament_diameter_mm": filament_diameter,
                "applied_changes": list(changed),
            }
            self._slice_capability_snapshots[position] = snapshot
            self._last_slice_resolution = (
                "SLICE RESOLVED: extruder {} -> {} ({})"
            ).format(
                position,
                record.get("id", ""),
                context["material_name"],
            )
            Logger.log("i", "Eventide %s", self._last_slice_resolution)
            return transformed

        except FileNotFoundError as error:
            # No capability for this exact material is a valid state. Slice with
            # Cura's untouched settings and, critically, do not retain the old
            # material's Eventide snapshot.
            try:
                position = int(stack.getMetaDataEntry("position", 0) or 0)
                self._slice_capability_snapshots.pop(position, None)
            except Exception:
                pass
            self._last_slice_resolution = "SLICE UNMANAGED: {}".format(error)
            Logger.log("i", "Eventide %s", self._last_slice_resolution)
            return original_values

        except Exception as error:
            try:
                position = int(stack.getMetaDataEntry("position", 0) or 0)
                self._slice_capability_snapshots.pop(position, None)
            except Exception:
                pass
            self._last_slice_resolution = "SLICE CAPABILITY ERROR: {}".format(error)
            Logger.logException("e", "Eventide slice capability resolution failed")
            return original_values

    def _install_slice_settings_hook(self) -> bool:
        """Patch Cura 5.13 StartSliceJob's copied-settings stage, not live stacks."""
        if self._slice_hook_installed:
            return True

        target_class = None
        for module in list(sys.modules.values()):
            if module is None:
                continue
            candidate = getattr(module, "StartSliceJob", None)
            if (
                isinstance(candidate, type)
                and hasattr(candidate, "_buildReplacementTokens")
            ):
                target_class = candidate
                break

        if target_class is None:
            self._last_slice_resolution = (
                "Slice-time hook unavailable: CuraEngine StartSliceJob is not loaded yet."
            )
            return False

        if hasattr(target_class, "_eventide_original_buildReplacementTokens"):
            target_class._eventide_owner = self
            self._slice_hook_installed = True
            self._last_slice_resolution = "Slice-time Eventide hook is active."
            self.stateChanged.emit()
            return True

        original = target_class._buildReplacementTokens
        target_class._eventide_original_buildReplacementTokens = original
        target_class._eventide_owner = self

        def eventide_build_replacement_tokens(job_self: Any, stack: Any) -> Dict[str, Any]:
            base_values = target_class._eventide_original_buildReplacementTokens(
                job_self,
                stack,
            )
            owner = getattr(target_class, "_eventide_owner", None)
            if owner is None:
                return base_values
            return owner._transform_slice_settings(stack, base_values)

        target_class._buildReplacementTokens = eventide_build_replacement_tokens
        self._slice_hook_installed = True
        self._last_slice_resolution = "Slice-time Eventide hook is active."
        self.stateChanged.emit()
        Logger.log("i", "Eventide installed transient StartSliceJob settings hook")
        return True

    @pyqtSlot(str, result=str)
    def checkSliceResolver(self, requested_path: str) -> str:
        try:
            root = self._save_library_path_from_ui(requested_path)
            self._require_initialized_library(root)
            self.refreshSelection()
            self._install_slice_settings_hook()

            stack = self._get_active_extruder_stack()
            if stack is None:
                raise ValueError("Cura has no active extruder stack")

            settings = {
                key: stack.getProperty(key, "value")
                for key in stack.getAllKeys()
            }
            record, context = self._find_capability_for_slice_stack(
                root,
                stack,
                settings,
            )
            self._last_slice_resolution = (
                "SLICE RESOLVER OK: {} + {} + extruder {} + {} mm -> {}"
            ).format(
                context["printer_name"],
                context["material_name"],
                context["extruder"],
                self._display_number(context["nozzle_diameter_mm"]),
                record.get("id", ""),
            )
            self._status = self._last_slice_resolution
        except Exception as error:
            self._last_slice_resolution = "SLICE RESOLVER: unmanaged ({})".format(error)
            self._status = self._last_slice_resolution

        self.stateChanged.emit()
        return self._status

    def _on_cura_initialized(self) -> None:
        """Arm Eventide automatically once Cura startup is complete."""
        self._slice_hook_retry_count = 0
        self._ensure_runtime_hooks()
        if not self._library_poll_timer.isActive():
            self._library_poll_timer.start()
            self._last_library_event = "Background library polling active ({} s, Uranium Job).".format(
                int(self.LIBRARY_POLL_INTERVAL_MS / 1000)
            )
            QTimer.singleShot(0, self._poll_shared_library)

        # Plugin load ordering can theoretically leave StartSliceJob unavailable
        # on the first initialization callback. Retry briefly without touching
        # any live machine state.
        if not self._slice_hook_installed:
            QTimer.singleShot(500, self._retry_slice_hook_after_init)

    def _retry_slice_hook_after_init(self) -> None:
        if self._slice_hook_installed:
            return

        self._slice_hook_retry_count += 1
        self._install_slice_settings_hook()

        if (
            not self._slice_hook_installed
            and self._slice_hook_retry_count < 20
        ):
            QTimer.singleShot(500, self._retry_slice_hook_after_init)

    def _mark_local_quality_dirty(self, *_args: Any) -> None:
        """Debounce local quality publication and request an early background scan."""
        self._local_quality_dirty = True
        # Routine polling is deliberately slow for NAS friendliness, but a local
        # Cura edit should not have to wait the full interval. Multiple queued
        # callbacks are harmless because _start_library_scan rejects overlap.
        QTimer.singleShot(750, self._poll_shared_library)

    def _connect_quality_change_signals(self) -> None:
        """Watch the active Cura stacks instead of rescanning local profiles every 2.5s."""
        for source in self._quality_signal_sources:
            signal = getattr(source, "propertyChanged", None)
            if signal is None:
                continue
            try:
                signal.disconnect(self._mark_local_quality_dirty)
            except (TypeError, RuntimeError):
                pass
        self._quality_signal_sources = []

        global_stack = self._application.getGlobalContainerStack()
        if global_stack is None:
            return
        candidates = [global_stack, *list(getattr(global_stack, "extruderList", []) or [])]
        for source in candidates:
            signal = getattr(source, "propertyChanged", None)
            if signal is None:
                continue
            try:
                signal.connect(self._mark_local_quality_dirty)
                self._quality_signal_sources.append(source)
            except (TypeError, RuntimeError):
                Logger.logException("w", "Eventide could not watch a Cura settings stack")

    def _on_global_container_stack_changed(self, *_args: Any) -> None:
        self._mark_local_quality_dirty()
        self._connect_quality_change_signals()
        self.refreshSelection()

    def _ensure_runtime_hooks(self) -> None:
        """Connect Cura-state signals only after Cura has finished startup."""
        if self._runtime_hooks_connected:
            self._install_slice_settings_hook()
            return

        self._application.globalContainerStackChanged.connect(self._on_global_container_stack_changed)
        registry = CuraContainerRegistry.getInstance()
        registry.containerAdded.connect(self._mark_local_quality_dirty)
        registry.containerRemoved.connect(self._mark_local_quality_dirty)
        registry.containerMetaDataChanged.connect(self._mark_local_quality_dirty)
        self._connect_quality_change_signals()
        self._install_slice_settings_hook()
        self._runtime_hooks_connected = True

    def _get_manifest_signature(self) -> Optional[Tuple[int, int]]:
        """Legacy helper retained for diagnostics/backward compatibility."""
        if not self._shared_library_path:
            return None
        path = self._manifest_path(os.path.normpath(self._shared_library_path))
        try:
            stat = os.stat(path)
            return int(stat.st_mtime_ns), int(stat.st_size)
        except OSError:
            return None

    def _get_library_content_signature(self) -> Optional[str]:
        """Synchronous fingerprint helper retained for explicit diagnostics/actions."""
        if not self._shared_library_path:
            return None
        return self._storage.library_content_signature(os.path.normpath(self._shared_library_path))

    def _poll_shared_library(self) -> None:
        """Start one Uranium Job for shared-library change detection.

        The main thread only schedules work. File enumeration/stat calls happen in
        Uranium's JobQueue, and _library_scan_job prevents overlapping scans.
        """
        if self._live_sync_busy or not self._shared_library_path:
            return
        self._start_library_scan()

    def _start_library_scan(self) -> None:
        if not self._shared_library_path:
            return
        if self._library_scan_job is not None:
            return

        root = os.path.normpath(self._shared_library_path)
        job = EventideLibraryScanJob(self._storage.library_content_signature, root)
        self._library_scan_job = job
        job.finished.connect(self._on_library_scan_finished)
        job.start()

    def _on_library_scan_finished(self, job: Any) -> None:
        """Apply one completed filesystem observation on Cura's main side."""
        if job is not self._library_scan_job:
            return
        self._library_scan_job = None
        result = job.getResult()
        if not isinstance(result, LibraryScanResult):
            error = job.getError() if hasattr(job, "getError") else None
            self._library_available = False
            self._last_library_event = f"Live sync scan failed: {error or 'no scan result'}"
            Logger.log("e", "Eventide background library scan returned no usable result")
            self.stateChanged.emit()
            return

        current_root = os.path.normpath(self._shared_library_path) if self._shared_library_path else ""
        if os.path.normpath(result.root) != current_root:
            return

        changed_ui = False
        if result.error:
            self._library_available = False
            message = f"Live sync scan failed: {result.error}"
            if message != self._last_library_event:
                self._last_library_event = message
                changed_ui = True
        elif result.signature is None:
            self._library_available = False
            message = "Live sync waiting: shared library is unavailable."
            if message != self._last_library_event:
                self._last_library_event = message
                changed_ui = True
        else:
            self._library_available = True
            if result.signature != self._library_content_signature:
                self._live_sync_busy = True
                try:
                    # Cura objects must be installed/updated on the application side.
                    # The expensive routine network discovery already happened in
                    # the Job; quality-engine I/O separation is the next refactor.
                    sync_result = self.syncLibraryToCura(self._shared_library_path)
                    if str(sync_result).startswith("SYNC FAILED"):
                        self._last_library_event = sync_result
                    else:
                        self._library_content_signature = result.signature
                        self._last_library_event = (
                            f"Live sync applied shared-library changes at {self._utc_now()}."
                        )
                    changed_ui = True
                except (OSError, ValueError, RuntimeError) as error:
                    self._last_library_event = f"Live sync error: {error}"
                    Logger.logException("e", "Eventide live shared-library sync failed")
                    changed_ui = True
                except Exception as error:  # Cura integration boundary; preserve diagnostics.
                    self._last_library_event = f"Live sync unexpected error: {error}"
                    Logger.logException("e", "Eventide live shared-library sync failed unexpectedly")
                    changed_ui = True
                finally:
                    self._live_sync_busy = False

        # Publish only after a real local Cura change; there is no periodic local
        # profile enumeration anymore. Network writes here are being separated from
        # Cura-object capture in the quality-sync refactor that follows this alpha.
        if self._library_available and self._local_quality_dirty and not self._live_sync_busy:
            self._live_sync_busy = True
            try:
                publish = self._publish_local_quality_profiles(current_root)
                self._local_quality_dirty = False
                if publish.get("changed", 0) or publish.get("conflicts", 0):
                    changed_ui = True
            except (OSError, ValueError, RuntimeError) as error:
                self._last_library_event = f"Live quality publish error: {error}"
                Logger.logException("e", "Eventide live quality publication failed")
                changed_ui = True
            except Exception as error:  # Cura integration boundary; preserve diagnostics.
                self._last_library_event = f"Live quality publish unexpected error: {error}"
                Logger.logException("e", "Eventide live quality publication failed unexpectedly")
                changed_ui = True
            finally:
                self._live_sync_busy = False

        if changed_ui:
            self.stateChanged.emit()

    @pyqtSlot(str, result=str)
    def setActiveNozzleMaterial(self, nozzle_material: str) -> str:
        try:
            self.refreshSelection()
            if not self._active_printer_id:
                raise ValueError("Cura does not have an active printer id")
            position, nozzle = self._active_toolhead()
            material = str(nozzle_material or "").strip()
            self._bind_nozzle_material(
                self._active_printer_id, position, nozzle, material
            )
            self._active_nozzle_material = material
            self._status = (
                "TOOLHEAD BOUND: extruder {} / nozzle {} mm -> {}".format(
                    position,
                    self._display_number(nozzle) or "unknown",
                    material or "Unspecified",
                )
                if material
                else "TOOLHEAD BINDING CLEARED"
            )
        except Exception as error:
            self._status = "TOOLHEAD BIND FAILED: {}".format(error)
            Logger.logException("e", "Eventide toolhead binding failed")
        self.stateChanged.emit()
        return self._status

    def _material_guid_exists_locally(self, guid: str) -> bool:
        if not str(guid or "").strip():
            return False
        try:
            return bool(CuraContainerRegistry.getInstance().findInstanceContainersMetadata(GUID=str(guid).strip()))
        except Exception:
            return False

    def _install_material_record(self, record: Dict[str, Any]) -> str:
        definition = record.get("material_definition", {})
        if not isinstance(definition, dict):
            return "unpublished"
        guid = str(definition.get("guid", "") or "").strip()
        if guid and self._material_guid_exists_locally(guid):
            return "existing"
        if bool(definition.get("readonly_builtin", False)):
            return "builtin-missing"
        serialized = str(definition.get("serialized", "") or "")
        if not serialized.strip():
            return "unpublished"
        expected_hash = str(definition.get("sha256", "") or "").strip()
        if expected_hash and hashlib.sha256(serialized.encode("utf-8")).hexdigest() != expected_hash:
            raise ValueError("material payload hash mismatch for {}".format(record.get("id", "")))

        from cura.Settings.ContainerManager import ContainerManager
        manager = ContainerManager.getInstance()
        if manager is None:
            raise RuntimeError("Cura ContainerManager is not available")

        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".xml.fdm_material",
                prefix="eventide-material-",
                delete=False,
            ) as handle:
                handle.write(serialized)
                temp_path = handle.name
            result = manager.importMaterialContainer(temp_path)
            if not isinstance(result, dict) or result.get("status") != "success":
                message = result.get("message", "unknown import error") if isinstance(result, dict) else str(result)
                raise RuntimeError(f"Cura material import failed: {message}")
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass
                except OSError:
                    Logger.logException("w", "Eventide could not remove temporary material import file")

        if guid and not self._material_guid_exists_locally(guid):
            raise RuntimeError("material import completed but GUID {} is still not registered".format(guid))
        return "installed"

    def _find_local_machine_for_record(self, record: Dict[str, Any]) -> Optional[Any]:
        registry = CuraContainerRegistry.getInstance()
        record_id = str(record.get("id", "") or "").strip()
        source_id = str(record.get("cura", {}).get("id", "") or "").strip()
        candidate_ids = []
        bound = str(self._machine_bindings.get(record_id, "") or "").strip()
        if bound:
            candidate_ids.append(bound)
        if source_id:
            candidate_ids.append(source_id)
        for machine_id in candidate_ids:
            try:
                stacks = registry.findContainerStacks(id=machine_id)
                if stacks:
                    return stacks[0]
            except Exception:
                pass
        try:
            metadata = registry.findContainerStacksMetadata(type="machine", eventide_record_id=record_id)
            for item in metadata:
                machine_id = str(item.get("id", "") or "").strip()
                if machine_id:
                    stacks = registry.findContainerStacks(id=machine_id)
                    if stacks:
                        return stacks[0]
        except Exception:
            pass
        return None

    @staticmethod
    def _apply_instance_values(container: Any, values: Dict[str, Any]) -> int:
        changed = 0
        if container is None or not isinstance(values, dict):
            return changed
        for key, value in values.items():
            try:
                if container.getProperty(key, "value") != value:
                    container.setProperty(key, "value", value)
                    changed += 1
            except Exception:
                Logger.logException("w", "Eventide could not restore machine setting %s", key)
        return changed

    def _install_machine_record(self, record: Dict[str, Any]) -> str:
        existing = self._find_local_machine_for_record(record)
        record_id = str(record.get("id", "") or "").strip()
        if existing is not None:
            try:
                self._machine_bindings[record_id] = str(existing.getId() or "")
                self._save_config()
            except Exception:
                pass
            return "existing"

        definition = record.get("machine_definition", {})
        if not isinstance(definition, dict) or definition.get("format") != "cura_machine_instance_v1":
            return "unpublished"
        definition_id = str(definition.get("definition_id", "") or "").strip()
        if not definition_id:
            return "unpublished"

        registry = CuraContainerRegistry.getInstance()
        if not registry.findDefinitionContainers(id=definition_id):
            return "missing-definition:{}".format(definition_id)

        machine_manager = self._application.getMachineManager()
        previous = getattr(machine_manager, "activeMachine", None)
        previous_id = str(previous.getId() or "") if previous is not None else ""
        name = str(definition.get("name", "") or record.get("cura", {}).get("name", "") or definition_id)

        try:
            if not machine_manager.addMachine(definition_id, name):
                raise RuntimeError("Cura refused to create machine from definition {}".format(definition_id))
            created = getattr(machine_manager, "activeMachine", None)
            if created is None:
                raise RuntimeError("Cura did not expose the newly created machine")

            expected_extruders = list(definition.get("extruders", []) or [])
            actual_extruders = list(getattr(created, "extruderList", []) or [])
            if len(expected_extruders) != len(actual_extruders):
                raise RuntimeError(
                    "extruder count mismatch: shared {} vs local definition {}".format(
                        len(expected_extruders), len(actual_extruders)
                    )
                )

            self._apply_instance_values(
                getattr(created, "definitionChanges", None),
                definition.get("global_definition_changes", {}),
            )
            actual_by_position = {}
            for extruder in actual_extruders:
                try:
                    position = int(extruder.getMetaDataEntry("position") or 0)
                except Exception:
                    position = len(actual_by_position)
                actual_by_position[position] = extruder
            for extruder_data in expected_extruders:
                position = int(extruder_data.get("position", 0) or 0)
                target = actual_by_position.get(position)
                if target is None:
                    raise RuntimeError("missing target extruder position {}".format(position))
                source_def = str(extruder_data.get("definition_id", "") or "")
                target_def = str(getattr(getattr(target, "definition", None), "getId", lambda: "")() or "")
                if source_def and target_def and source_def != target_def:
                    raise RuntimeError(
                        "extruder definition mismatch at position {}: {} vs {}".format(position, source_def, target_def)
                    )
                self._apply_instance_values(
                    getattr(target, "definitionChanges", None),
                    extruder_data.get("definition_changes", {}),
                )

            try:
                created.setMetaDataEntry("eventide_record_id", record_id)
                created.setMetaDataEntry("eventide_source_machine_id", str(definition.get("source_stack_id", "") or ""))
            except Exception:
                pass
            local_id = str(created.getId() or "")
            self._machine_bindings[record_id] = local_id
            self._save_config()
            try:
                machine_manager.correctExtruderSettings()
                machine_manager.correctPrintSequence()
            except Exception:
                pass
            return "installed"
        finally:
            if previous_id:
                try:
                    machine_manager.setActiveMachine(previous_id)
                except Exception:
                    Logger.logException("w", "Eventide could not restore previously active machine")

    @staticmethod
    def _quality_hash(payload: Dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _current_printer_record_id(self) -> str:
        return self._shared_printer_record_id(self._active_printer_id)

    @staticmethod
    def _quality_resolution(record: Dict[str, Any]) -> Dict[str, Any]:
        """Return validated human-resolution metadata from a shared quality record.

        A Keep This PC resolution is authoritative only for the conflict state it
        resolved. Receivers may automatically accept the winning hash when their
        local copy is the known losing hash (or is still at their last synchronized
        baseline). A later independent local edit remains protected as a conflict.
        """
        raw = record.get("resolution", {})
        if not isinstance(raw, dict):
            return {}
        resolution_id = str(raw.get("id", "") or "").strip()
        if not resolution_id or str(raw.get("strategy", "") or "").strip() != "keep_local":
            return {}
        losing_hashes = raw.get("losing_hashes", [])
        if not isinstance(losing_hashes, list):
            losing_hashes = []
        return {
            "id": resolution_id,
            "strategy": "keep_local",
            "winning_hash": str(raw.get("winning_hash", "") or record.get("content_hash", "") or ""),
            "losing_hashes": [str(value) for value in losing_hashes if str(value or "").strip()],
            "supersedes_revision": int(raw.get("supersedes_revision", 0) or 0),
        }

    def _register_quality_conflict(self, quality_id: str, record: Dict[str, Any], local_payload: Dict[str, Any]) -> None:
        """Remember an edit conflict so the normal Profiles UI can resolve it."""
        quality_id = str(quality_id or "").strip()
        if not quality_id:
            return
        remote_writer = record.get("updated_by", {}) if isinstance(record.get("updated_by", {}), dict) else {}
        entry = {
            "kind": "edit",
            "quality_id": quality_id,
            "name": str(local_payload.get("name", "") or record.get("name", "") or "Shared Profile"),
            "printer_id": str(record.get("printer_id", "") or local_payload.get("printer_id", "") or ""),
            "remote_revision": int(record.get("revision", 0) or 0),
            "remote_hostname": str(remote_writer.get("hostname", "") or "another computer"),
            "remote_client_id": str(remote_writer.get("client_id", "") or ""),
            "remote_hash": str(record.get("content_hash", "") or ""),
            "local_hash": str(local_payload.get("content_hash", "") or ""),
        }
        self._quality_conflicts[quality_id] = entry
        if quality_id not in self._quality_conflict_order:
            self._quality_conflict_order.append(quality_id)
        if self._quality_conflict_index >= len(self._quality_conflict_order):
            self._quality_conflict_index = max(0, len(self._quality_conflict_order) - 1)

        # Surface a newly detected conflict even when the main Eventide window
        # is closed. The token includes both sides' hashes so the same conflict
        # is shown once, while a later independent conflict can alert again.
        popup_token = "{}:{}:{}:{}:{}".format(
            entry.get("kind", "edit"),
            quality_id,
            entry["remote_revision"],
            entry["remote_hash"],
            entry["local_hash"],
        )
        if popup_token != self._last_quality_conflict_popup_token:
            self._last_quality_conflict_popup_token = popup_token
            QTimer.singleShot(0, self._show_quality_conflict_dialog)

    def _clear_quality_conflict(self, quality_id: str) -> None:
        quality_id = str(quality_id or "").strip()
        self._quality_conflicts.pop(quality_id, None)
        self._quality_conflict_order = [item for item in self._quality_conflict_order if item != quality_id]
        if not self._quality_conflict_order:
            self._quality_conflict_index = 0
            if self._quality_conflict_dialog is not None:
                try:
                    self._quality_conflict_dialog.hide()
                except Exception:
                    pass
        elif self._quality_conflict_index >= len(self._quality_conflict_order):
            self._quality_conflict_index = len(self._quality_conflict_order) - 1

    def _show_quality_conflict_dialog(self) -> None:
        """Show one modal conflict resolver without requiring the main window."""
        if not self._quality_conflicts:
            return
        try:
            if self._quality_conflict_dialog is None:
                plugin_path = cast(str, self._application.getPluginRegistry().getPluginPath(self.getPluginId()))
                qml_path = os.path.join(plugin_path, "qml", "EventideQualityConflictDialog.qml")
                self._quality_conflict_dialog = self._application.createQmlComponent(
                    qml_path, {"eventideBridge": self}
                )
            if self._quality_conflict_dialog is not None:
                self._quality_conflict_dialog.show()
                try:
                    self._quality_conflict_dialog.requestActivate()
                except Exception:
                    pass
        except Exception:
            Logger.logException("e", "Eventide could not show quality conflict dialog")

    def _current_quality_conflict(self) -> Optional[Dict[str, Any]]:
        self._quality_conflict_order = [
            quality_id for quality_id in self._quality_conflict_order
            if quality_id in self._quality_conflicts
        ]
        if not self._quality_conflict_order:
            self._quality_conflict_index = 0
            return None
        self._quality_conflict_index %= len(self._quality_conflict_order)
        quality_id = self._quality_conflict_order[self._quality_conflict_index]
        return self._quality_conflicts.get(quality_id)

    def _unique_quality_copy_name(self, root: str, quality_definition: str, requested_name: str) -> str:
        base = str(requested_name or "").strip() or "Conflict copy"
        registry = CuraContainerRegistry.getInstance()
        remote_names = set()
        quality_dir = os.path.join(root, "quality")
        try:
            for filename in os.listdir(quality_dir):
                if not filename.lower().endswith(".json"):
                    continue
                try:
                    record = self._read_json(os.path.join(quality_dir, filename))
                    remote_names.add(str(record.get("name", "") or "").strip().lower())
                except Exception:
                    continue
        except OSError:
            pass

        candidate = base
        suffix = 2
        while True:
            try:
                local_matches = registry.findInstanceContainersMetadata(
                    type="quality_changes",
                    definition=quality_definition,
                    name=candidate,
                )
            except Exception:
                local_matches = []
            if not local_matches and candidate.lower() not in remote_names:
                return candidate
            candidate = "{} ({})".format(base, suffix)
            suffix += 1

    def _create_quality_copy_from_payload(
        self,
        root: str,
        source_record: Dict[str, Any],
        local_payload: Dict[str, Any],
        requested_name: str,
    ) -> Tuple[str, str]:
        """Preserve the local side of a conflict as a new shared Cura profile."""
        printer_id = str(source_record.get("printer_id", "") or local_payload.get("printer_id", "") or "").strip()
        if not printer_id:
            raise ValueError("conflicting quality profile has no shared printer identity")
        printer_path = os.path.join(root, "printers", printer_id + ".json")
        if not os.path.isfile(printer_path):
            raise ValueError("shared printer record is missing")
        printer_record = self._read_json(printer_path)
        target_machine = self._find_local_machine_for_record(printer_record)
        if target_machine is None:
            raise ValueError("the shared printer is not installed in this Cura")

        quality_definition = str(
            local_payload.get("quality_definition", "")
            or source_record.get("quality_definition", "")
            or "fdmprinter"
        ).strip()
        registry = CuraContainerRegistry.getInstance()
        if not registry.findDefinitionContainers(id=quality_definition):
            raise ValueError("quality definition {} is not installed".format(quality_definition))

        copy_name = self._unique_quality_copy_name(root, quality_definition, requested_name)
        copy_id = self._stable_id("quality", printer_id, uuid.uuid4().hex)
        copy_payload = {
            "printer_id": printer_id,
            "name": copy_name,
            "quality_type": str(local_payload.get("quality_type", "not_supported") or "not_supported"),
            "intent_category": str(local_payload.get("intent_category", "default") or "default"),
            "quality_definition": quality_definition,
            "global_values": dict(local_payload.get("global_values", {}) or {}),
            "extruders": [
                {"position": int(item.get("position", 0)), "values": dict(item.get("values", {}) or {})}
                for item in list(local_payload.get("extruders", []) or [])
                if isinstance(item, dict)
            ],
        }
        copy_payload["content_hash"] = self._quality_hash(copy_payload)
        now = self._utc_now()
        copy_record = {
            "schema": self.QUALITY_SCHEMA,
            "format": self.RECORD_FORMAT,
            "id": copy_id,
            "revision": 1,
            "created_utc": now,
            "updated_utc": now,
            "updated_by": self._writer_info(),
            "is_deleted": False,
            **copy_payload,
        }

        # Write the copy to the shared library first. Even if Cura container
        # creation subsequently fails, the user's local edit is preserved in
        # the NAS and will be installable on the next synchronization pass.
        self._atomic_write_json(os.path.join(root, "quality", copy_id + ".json"), copy_record)
        self._quality_sync_state[copy_id] = {
            "revision": 1,
            "content_hash": str(copy_payload["content_hash"]),
            "printer_id": printer_id,
        }

        machine_definition_id = str(target_machine.definition.getId() or "")
        self._create_quality_container(
            machine_definition_id,
            copy_record,
            quality_definition,
            None,
            dict(copy_payload.get("global_values", {}) or {}),
        )
        target_extruders: Dict[int, Any] = {}
        for extruder in list(getattr(target_machine, "extruderList", []) or []):
            try:
                target_extruders[int(extruder.getMetaDataEntry("position") or 0)] = extruder
            except Exception:
                continue
        for item in list(copy_payload.get("extruders", []) or []):
            position = int(item.get("position", 0) or 0)
            target = target_extruders.get(position)
            if target is None:
                continue
            self._create_quality_container(
                target.getId(),
                copy_record,
                quality_definition,
                position,
                dict(item.get("values", {}) or {}),
            )
        return copy_id, copy_name

    def _quality_group_payload(self, printer_record_id: str, group: Any) -> Optional[Tuple[str, Dict[str, Any]]]:
        registry = CuraContainerRegistry.getInstance()
        global_meta = dict(getattr(group, "metadata_for_global", {}) or {})
        extruder_meta = dict(getattr(group, "metadata_per_extruder", {}) or {})
        if not global_meta:
            return None
        global_id = str(global_meta.get("id", "") or "").strip()
        containers = registry.findInstanceContainers(id=global_id) if global_id else []
        if not containers:
            return None
        global_container = containers[0]
        quality_id = str(global_container.getMetaDataEntry("eventide_record_id", "") or "").strip()
        if not quality_id:
            quality_id = self._stable_id("quality", printer_record_id, global_id or str(group.name))

        # Stamp a stable Eventide identity onto every member of the local group.
        # This lets a profile round-trip to another PC, be edited there, and
        # publish back to the same shared record rather than creating a duplicate.
        member_containers: List[Any] = [global_container]
        extruders_payload = []
        for position in sorted(extruder_meta, key=lambda value: int(value)):
            metadata = extruder_meta[position]
            container_id = str(metadata.get("id", "") or "").strip()
            matches = registry.findInstanceContainers(id=container_id) if container_id else []
            if not matches:
                continue
            container = matches[0]
            member_containers.append(container)
            extruders_payload.append({
                "position": int(position),
                "values": self._instance_values(container),
            })

        for container in member_containers:
            try:
                if str(container.getMetaDataEntry("eventide_record_id", "") or "") != quality_id:
                    container.setMetaDataEntry("eventide_record_id", quality_id)
                if str(container.getMetaDataEntry("eventide_printer_id", "") or "") != printer_record_id:
                    container.setMetaDataEntry("eventide_printer_id", printer_record_id)
            except Exception:
                Logger.logException("w", "Eventide could not stamp quality profile identity")

        quality_definition = str(global_meta.get("definition", "") or global_container.getMetaDataEntry("definition", "") or "").strip()
        payload = {
            "printer_id": printer_record_id,
            "name": str(getattr(group, "name", "") or global_container.getName() or "Shared Profile"),
            "quality_type": str(getattr(group, "quality_type", "") or global_container.getMetaDataEntry("quality_type", "not_supported") or "not_supported"),
            "intent_category": str(getattr(group, "intent_category", "default") or "default"),
            "quality_definition": quality_definition,
            "global_values": self._instance_values(global_container),
            "extruders": extruders_payload,
        }
        payload["content_hash"] = self._quality_hash(payload)
        return quality_id, payload

    def _publish_local_quality_profiles(self, root: str) -> Dict[str, int]:
        stats = {"changed": 0, "current": 0, "conflicts": 0, "skipped": 0, "failed": 0}
        try:
            if not self._active_printer_id or self._application.getGlobalContainerStack() is None:
                return stats
            printer_record_id = self._current_printer_record_id()
            printer_path = os.path.join(root, "printers", printer_record_id + ".json")
            if not os.path.isfile(printer_path):
                stats["skipped"] += 1
                self._last_quality_sync_summary = "Quality sync waiting: share the current printer first."
                return stats

            groups = ContainerTree.getInstance().getCurrentQualityChangesGroups()
            present_quality_ids = set()
            touched = False
            state_changed = False
            for group in groups:
                try:
                    captured = self._quality_group_payload(printer_record_id, group)
                    if captured is None:
                        stats["skipped"] += 1
                        continue
                    quality_id, payload = captured
                    present_quality_ids.add(quality_id)
                    path = os.path.join(root, "quality", quality_id + ".json")
                    local_hash = str(payload["content_hash"])
                    state = dict(self._quality_sync_state.get(quality_id, {}) or {})

                    if os.path.isfile(path):
                        record = self._read_json(path)
                        if record.get("id") != quality_id:
                            raise ValueError("quality record identity mismatch")
                        if self._quality_is_deleted(record):
                            deletion = self._quality_tombstone_deletion(record)
                            state = dict(self._quality_sync_state.get(quality_id, {}) or {})
                            seen_hash = str(state.get("content_hash", "") or "")
                            deleted_hash = str(
                                deletion.get("deleted_content_hash", "")
                                or record.get("content_hash", "")
                                or ""
                            )
                            # A stale/baseline copy must not resurrect a soft-deleted profile.
                            # An independently edited local copy still becomes a deletion conflict.
                            if local_hash == deleted_hash or (seen_hash and local_hash == seen_hash):
                                stats["skipped"] += 1
                                continue
                            self._register_quality_deletion_conflict(quality_id, record, payload)
                            stats["conflicts"] += 1
                            continue
                        if record.get("schema") != self.QUALITY_SCHEMA:
                            raise ValueError("quality record identity mismatch")
                        remote_hash = str(record.get("content_hash", "") or "")
                        remote_revision = int(record.get("revision", 0) or 0)
                        resolution = self._quality_resolution(record)
                        resolution_id = str(resolution.get("id", "") or "")
                        seen_resolution_id = str(state.get("resolution_id", "") or "")
                        if remote_hash == local_hash:
                            self._clear_quality_conflict(quality_id)
                            stats["current"] += 1
                            next_state = {
                                "revision": remote_revision,
                                "content_hash": local_hash,
                                "printer_id": printer_record_id,
                            }
                            if resolution_id:
                                next_state["resolution_id"] = resolution_id
                            self._quality_sync_state[quality_id] = next_state
                            state_changed = True
                            continue

                        seen_revision = int(state.get("revision", 0) or 0)
                        seen_hash = str(state.get("content_hash", "") or "")
                        remote_writer = str(record.get("updated_by", {}).get("client_id", "") or "")

                        # A human explicitly chose Keep This PC on another client.
                        # Do not let this client's old losing copy immediately
                        # republish over that decision before the receive pass can
                        # apply the winner. Only the known losing/baseline state is
                        # auto-preempted; a genuinely new local edit remains a
                        # conflict and is never silently discarded.
                        if resolution_id and resolution_id != seen_resolution_id and remote_writer != self._client_id:
                            losing_hashes = set(resolution.get("losing_hashes", []) or [])
                            if local_hash in losing_hashes or (seen_hash and local_hash == seen_hash):
                                stats["skipped"] += 1
                                continue
                            self._register_quality_conflict(quality_id, record, payload)
                            stats["conflicts"] += 1
                            continue

                        # Remote moved after our last synchronized revision. If
                        # local stayed unchanged, receive the remote version on
                        # this poll instead of writing stale data back. If both
                        # moved, flag a real conflict.
                        if remote_writer != self._client_id:
                            if seen_revision and remote_revision != seen_revision:
                                if local_hash == seen_hash:
                                    stats["skipped"] += 1
                                    continue
                                self._register_quality_conflict(quality_id, record, payload)
                                stats["conflicts"] += 1
                                continue
                            if not seen_revision and remote_writer:
                                self._register_quality_conflict(quality_id, record, payload)
                                stats["conflicts"] += 1
                                continue
                        revision = remote_revision + 1
                        created_utc = record.get("created_utc", self._utc_now())
                    else:
                        revision = 1
                        created_utc = self._utc_now()

                    now = self._utc_now()
                    record = {
                        "schema": self.QUALITY_SCHEMA,
                        "format": self.RECORD_FORMAT,
                        "id": quality_id,
                        "revision": revision,
                        "created_utc": created_utc,
                        "updated_utc": now,
                        "updated_by": self._writer_info(),
                        "is_deleted": False,
                        **payload,
                    }
                    self._atomic_write_json(path, record)
                    self._clear_quality_conflict(quality_id)
                    self._quality_sync_state[quality_id] = {
                        "revision": revision,
                        "content_hash": local_hash,
                        "printer_id": printer_record_id,
                    }
                    touched = True
                    state_changed = True
                    stats["changed"] += 1
                except Exception:
                    stats["failed"] += 1
                    Logger.logException("e", "Eventide could not publish a local quality profile")

            deletion_stats = self._publish_local_quality_deletions(
                root, printer_record_id, present_quality_ids
            )
            if deletion_stats.get("changed", 0):
                touched = True
                state_changed = True
                stats["changed"] += deletion_stats.get("changed", 0)
                stats["deleted"] = stats.get("deleted", 0) + deletion_stats.get("deleted", 0)
            if deletion_stats.get("conflicts", 0):
                stats["conflicts"] += deletion_stats.get("conflicts", 0)

            if touched:
                self._touch_manifest(root)
            if state_changed or deletion_stats.get("current", 0):
                self._save_config()
            if stats["conflicts"]:
                self._last_quality_sync_summary = "QUALITY CONFLICT: {} profile(s) changed both locally and in the shared library.".format(stats["conflicts"])
            elif stats["changed"]:
                if stats.get("deleted", 0):
                    self._last_quality_sync_summary = "Live quality sync published {} change(s), including {} deletion(s).".format(
                        stats["changed"], stats.get("deleted", 0)
                    )
                else:
                    self._last_quality_sync_summary = "Live quality sync published {} profile change(s).".format(stats["changed"])
            elif groups:
                self._last_quality_sync_summary = "Live quality sync: {} local custom profile(s) current.".format(len(groups))
        except Exception as error:
            stats["failed"] += 1
            self._last_quality_sync_summary = "Quality publish error: {}".format(error)
            Logger.logException("e", "Eventide quality-profile publication failed")
        return stats

    def _quality_local_containers(self, quality_id: str) -> Tuple[Optional[Any], Dict[int, Any]]:
        registry = CuraContainerRegistry.getInstance()
        global_container = None
        extruders: Dict[int, Any] = {}
        try:
            metadata_list = registry.findInstanceContainersMetadata(type="quality_changes", eventide_record_id=quality_id)
        except Exception:
            metadata_list = []
        for metadata in metadata_list:
            container_id = str(metadata.get("id", "") or "").strip()
            matches = registry.findInstanceContainers(id=container_id) if container_id else []
            if not matches:
                continue
            position = metadata.get("position")
            if position is None or str(position) == "None":
                global_container = matches[0]
            else:
                try:
                    extruders[int(position)] = matches[0]
                except Exception:
                    continue
        return global_container, extruders

    def _register_quality_deletion_conflict(self, quality_id: str, tombstone: Dict[str, Any], local_payload: Dict[str, Any]) -> None:
        """Protect a local edit when a remote deletion arrives."""
        quality_id = str(quality_id or "").strip()
        if not quality_id:
            return
        remote_writer = tombstone.get("updated_by", {}) if isinstance(tombstone.get("updated_by", {}), dict) else {}
        deletion = tombstone.get("deletion", {}) if isinstance(tombstone.get("deletion", {}), dict) else {}
        entry = {
            "kind": "deletion",
            "quality_id": quality_id,
            "name": str(local_payload.get("name", "") or tombstone.get("name", "") or "Shared Profile"),
            "printer_id": str(tombstone.get("printer_id", "") or local_payload.get("printer_id", "") or ""),
            "remote_revision": int(tombstone.get("revision", 0) or 0),
            "remote_hostname": str(remote_writer.get("hostname", "") or "another computer"),
            "remote_client_id": str(remote_writer.get("client_id", "") or ""),
            "remote_hash": str(deletion.get("deleted_content_hash", "") or ""),
            "local_hash": str(local_payload.get("content_hash", "") or ""),
            "deletion_id": str(deletion.get("id", "") or ""),
        }
        self._quality_conflicts[quality_id] = entry
        if quality_id not in self._quality_conflict_order:
            self._quality_conflict_order.append(quality_id)
        if self._quality_conflict_index >= len(self._quality_conflict_order):
            self._quality_conflict_index = max(0, len(self._quality_conflict_order) - 1)
        popup_token = "deletion:{}:{}:{}:{}".format(
            quality_id,
            entry["remote_revision"],
            entry["remote_hash"],
            entry["local_hash"],
        )
        if popup_token != self._last_quality_conflict_popup_token:
            self._last_quality_conflict_popup_token = popup_token
            QTimer.singleShot(0, self._show_quality_conflict_dialog)

    def _register_local_delete_remote_edit_conflict(self, quality_id: str, record: Dict[str, Any]) -> None:
        """Protect a newer shared edit when this PC deleted its older local copy."""
        remote_writer = record.get("updated_by", {}) if isinstance(record.get("updated_by", {}), dict) else {}
        entry = {
            "kind": "local_delete_remote_edit",
            "quality_id": quality_id,
            "name": str(record.get("name", "") or "Shared Profile"),
            "printer_id": str(record.get("printer_id", "") or ""),
            "remote_revision": int(record.get("revision", 0) or 0),
            "remote_hostname": str(remote_writer.get("hostname", "") or "another computer"),
            "remote_client_id": str(remote_writer.get("client_id", "") or ""),
            "remote_hash": str(record.get("content_hash", "") or ""),
            "local_hash": "",
        }
        self._quality_conflicts[quality_id] = entry
        if quality_id not in self._quality_conflict_order:
            self._quality_conflict_order.append(quality_id)
        popup_token = "local-delete:{}:{}:{}".format(
            quality_id, entry["remote_revision"], entry["remote_hash"]
        )
        if popup_token != self._last_quality_conflict_popup_token:
            self._last_quality_conflict_popup_token = popup_token
            QTimer.singleShot(0, self._show_quality_conflict_dialog)

    @staticmethod
    def _quality_tombstone_deletion(record: Dict[str, Any]) -> Dict[str, Any]:
        """Return deletion metadata from both 0.8.6 tombstones and 0.8.7 soft-deleted records."""
        deletion = record.get("deletion", {})
        return dict(deletion) if isinstance(deletion, dict) else {}

    def _quality_is_deleted(self, record: Dict[str, Any]) -> bool:
        """Deletion is a reversible record state in 0.8.7+.

        The separate tombstone schema is accepted only for 0.8.6 backward
        compatibility. New records keep QUALITY_SCHEMA and their complete
        profile payload, with only is_deleted toggled.
        """
        if str(record.get("schema", "") or "") == self.QUALITY_TOMBSTONE_SCHEMA:
            return True
        return record.get("is_deleted", False) is True

    def _make_quality_tombstone(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Soft-delete a quality record without discarding any profile data.

        Kept under the historical helper name to minimize churn in the conflict
        machinery. Unlike 0.8.6, this no longer creates a destructive tombstone.
        """
        now = self._utc_now()
        remote_revision = int(record.get("revision", 0) or 0)
        deletion_id = str(uuid.uuid4())
        deleted_record = dict(record)
        deleted_record.update({
            "schema": self.QUALITY_SCHEMA,
            "format": self.RECORD_FORMAT,
            "id": str(record.get("id", "") or ""),
            "revision": remote_revision + 1,
            "created_utc": record.get("created_utc", now),
            "updated_utc": now,
            "updated_by": self._writer_info(),
            "is_deleted": True,
            "deletion": {
                "id": deletion_id,
                "deleted_utc": now,
                "deleted_by": self._writer_info(),
                "deleted_from_revision": remote_revision,
                "deleted_content_hash": str(record.get("content_hash", "") or ""),
            },
        })
        # Never emit the old destructive tombstone flag from 0.8.7+.
        deleted_record.pop("deleted", None)
        return deleted_record

    def _quality_is_active(self, quality_id: str) -> bool:
        global_container, extruders = self._quality_local_containers(quality_id)
        ids = set()
        if global_container is not None:
            ids.add(str(global_container.getId() or ""))
        ids.update(str(container.getId() or "") for container in extruders.values())
        if not ids:
            return False
        stack = self._application.getGlobalContainerStack()
        if stack is None:
            return False
        try:
            if str(stack.qualityChanges.getId() or "") in ids:
                return True
        except Exception:
            pass
        for extruder in list(getattr(stack, "extruderList", []) or []):
            try:
                if str(extruder.qualityChanges.getId() or "") in ids:
                    return True
            except Exception:
                continue
        return False

    def _deactivate_quality_if_active(self, quality_id: str) -> None:
        if not self._quality_is_active(quality_id):
            return
        manager = self._application.getMachineManager()
        try:
            # Keep Cura's current base quality, but detach the custom
            # quality_changes containers before removing them from the registry.
            manager._setQualityGroup(manager.activeQualityGroup(), empty_quality_changes=True)
            return
        except Exception:
            Logger.logException("w", "Eventide could not detach active quality through MachineManager")
        # Conservative fallback for Cura variants where the private helper moves.
        try:
            from cura.Settings.cura_empty_instance_containers import empty_quality_changes_container
            stack = self._application.getGlobalContainerStack()
            if stack is not None:
                stack.qualityChanges = empty_quality_changes_container
                for extruder in list(getattr(stack, "extruderList", []) or []):
                    extruder.qualityChanges = empty_quality_changes_container
                manager.activeQualityChanged.emit()
                manager.activeQualityChangesGroupChanged.emit()
                manager.activeQualityGroupChanged.emit()
        except Exception:
            Logger.logException("e", "Eventide could not safely detach active quality profile")
            raise RuntimeError("could not detach the active custom quality before deletion")

    def _remove_quality_containers(self, quality_id: str) -> int:
        self._deactivate_quality_if_active(quality_id)
        registry = CuraContainerRegistry.getInstance()
        global_container, extruders = self._quality_local_containers(quality_id)
        containers = list(extruders.values())
        if global_container is not None:
            containers.append(global_container)
        removed = 0
        for container in containers:
            container_id = str(container.getId() or "").strip()
            if not container_id:
                continue
            registry.removeContainer(container_id)
            removed += 1
        try:
            manager = self._application.getMachineManager()
            manager.activeQualityChanged.emit()
            manager.activeQualityChangesGroupChanged.emit()
            manager.activeQualityGroupChanged.emit()
        except Exception:
            pass
        return removed

    def _publish_local_quality_deletions(self, root: str, printer_id: str, present_quality_ids: set) -> Dict[str, int]:
        """Soft-delete confirmed removals while retaining the complete shared profile payload."""
        stats = {"changed": 0, "deleted": 0, "conflicts": 0, "current": 0}
        for quality_id, raw_state in list(self._quality_sync_state.items()):
            state = dict(raw_state or {}) if isinstance(raw_state, dict) else {}
            if str(state.get("printer_id", "") or "") != printer_id:
                continue
            if bool(state.get("deleted", False)) or quality_id in present_quality_ids:
                continue
            # Do not infer deletion from ContainerTree filtering. Only publish a
            # mark the record deleted only once the stamped containers are actually gone from the registry.
            global_container, extruders = self._quality_local_containers(quality_id)
            if global_container is not None or extruders:
                continue
            path = os.path.join(root, "quality", quality_id + ".json")
            if not os.path.isfile(path):
                continue
            record = self._read_json(path)
            schema = str(record.get("schema", "") or "")
            if self._quality_is_deleted(record):
                deletion = self._quality_tombstone_deletion(record)
                self._quality_sync_state[quality_id] = {
                    "revision": int(record.get("revision", 0) or 0),
                    "content_hash": str(
                        deletion.get("deleted_content_hash", "")
                        or record.get("content_hash", "")
                        or state.get("content_hash", "")
                        or ""
                    ),
                    "printer_id": printer_id,
                    "deleted": True,
                    "deletion_id": str(deletion.get("id", "") or ""),
                }
                stats["current"] += 1
                continue
            if schema != self.QUALITY_SCHEMA:
                continue

            remote_revision = int(record.get("revision", 0) or 0)
            remote_hash = str(record.get("content_hash", "") or "")
            seen_revision = int(state.get("revision", 0) or 0)
            seen_hash = str(state.get("content_hash", "") or "")
            remote_writer = str(record.get("updated_by", {}).get("client_id", "") or "") if isinstance(record.get("updated_by", {}), dict) else ""
            remote_moved = bool(
                remote_writer != self._client_id
                and (
                    (seen_revision and remote_revision != seen_revision)
                    or (seen_hash and remote_hash != seen_hash)
                    or not seen_revision
                )
            )
            if remote_moved:
                self._register_local_delete_remote_edit_conflict(quality_id, record)
                stats["conflicts"] += 1
                continue

            tombstone = self._make_quality_tombstone(record)
            self._atomic_write_json(path, tombstone)
            deletion = self._quality_tombstone_deletion(tombstone)
            self._quality_sync_state[quality_id] = {
                "revision": int(tombstone.get("revision", 0) or 0),
                "content_hash": str(deletion.get("deleted_content_hash", "") or tombstone.get("content_hash", "") or remote_hash),
                "printer_id": printer_id,
                "deleted": True,
                "deletion_id": str(deletion.get("id", "") or ""),
            }
            self._clear_quality_conflict(quality_id)
            stats["changed"] += 1
            stats["deleted"] += 1
        return stats

    def _apply_quality_tombstone(self, root: str, record: Dict[str, Any], force: bool = False) -> str:
        quality_id = str(record.get("id", "") or "").strip()
        printer_id = str(record.get("printer_id", "") or "").strip()
        if not quality_id:
            raise ValueError("deleted quality record missing id")
        deletion = self._quality_tombstone_deletion(record)
        deleted_hash = str(deletion.get("deleted_content_hash", "") or record.get("content_hash", "") or "")
        remote_revision = int(record.get("revision", 0) or 0)
        state = dict(self._quality_sync_state.get(quality_id, {}) or {})
        global_container, extruders = self._quality_local_containers(quality_id)

        if global_container is not None:
            local_payload = self._quality_payload_from_local_containers(printer_id, global_container, extruders)
            local_hash = str(local_payload.get("content_hash", "") or "")
            seen_hash = str(state.get("content_hash", "") or "")
            safe_baseline = bool(
                (deleted_hash and local_hash == deleted_hash)
                or (seen_hash and local_hash == seen_hash and not bool(state.get("deleted", False)))
            )
            if not force and not safe_baseline:
                self._register_quality_deletion_conflict(quality_id, record, local_payload)
                return "deletion-conflict"
            self._remove_quality_containers(quality_id)
            outcome = "deleted"
        else:
            outcome = "deleted-existing"

        self._clear_quality_conflict(quality_id)
        self._quality_sync_state[quality_id] = {
            "revision": remote_revision,
            "content_hash": deleted_hash or str(state.get("content_hash", "") or ""),
            "printer_id": printer_id or str(state.get("printer_id", "") or ""),
            "deleted": True,
            "deletion_id": str(deletion.get("id", "") or ""),
        }
        self._save_config()
        return outcome

    def _quality_payload_from_local_containers(self, printer_id: str, global_container: Any, extruders: Dict[int, Any]) -> Dict[str, Any]:
        payload = {
            "printer_id": printer_id,
            "name": str(global_container.getName() or "Shared Profile"),
            "quality_type": str(global_container.getMetaDataEntry("quality_type", "not_supported") or "not_supported"),
            "intent_category": str(global_container.getMetaDataEntry("intent_category", "default") or "default"),
            "quality_definition": str(global_container.getMetaDataEntry("definition", "") or ""),
            "global_values": self._instance_values(global_container),
            "extruders": [
                {"position": int(position), "values": self._instance_values(extruders[position])}
                for position in sorted(extruders)
            ],
        }
        payload["content_hash"] = self._quality_hash(payload)
        return payload

    def _create_quality_container(self, base_id: str, record: Dict[str, Any], quality_definition: str, position: Optional[int], values: Dict[str, Any]) -> Any:
        registry = CuraContainerRegistry.getInstance()
        name = str(record.get("name", "") or "Shared Profile")
        seed = "{}_{}".format(base_id, name).lower().replace(" ", "_")
        container_id = registry.uniqueName(seed)
        container = InstanceContainer(container_id)
        container.setName(name)
        container.setMetaDataEntry("type", "quality_changes")
        container.setMetaDataEntry("quality_type", str(record.get("quality_type", "not_supported") or "not_supported"))
        intent = str(record.get("intent_category", "default") or "default")
        if intent != "default":
            container.setMetaDataEntry("intent_category", intent)
        if position is not None:
            container.setMetaDataEntry("position", int(position))
        container.setMetaDataEntry("eventide_record_id", str(record.get("id", "") or ""))
        container.setMetaDataEntry("eventide_printer_id", str(record.get("printer_id", "") or ""))
        container.setDefinition(quality_definition)
        container.setMetaDataEntry("setting_version", self._application.SettingVersion)
        self._apply_instance_values(container, values)
        registry.addContainer(container)
        return container

    def _apply_quality_record(self, root: str, record: Dict[str, Any], force: bool = False) -> str:
        quality_id = str(record.get("id", "") or "").strip()
        printer_id = str(record.get("printer_id", "") or "").strip()
        if not quality_id or not printer_id:
            raise ValueError("quality record missing id or printer_id")
        if self._quality_is_deleted(record):
            return self._apply_quality_tombstone(root, record, force=force)

        # Soft deletion was introduced after some beta clients were already in
        # circulation. A pre-0.8.7 client does not understand is_deleted and can
        # reconstruct an ordinary active record if its stale local copy is later
        # edited. If this client previously synchronized the deletion, distinguish
        # that stale resurrection from an intentional 0.8.7 recovery:
        #   * unchanged old baseline -> reassert the soft deletion automatically
        #   * independently edited old copy -> raise the normal delete/edit conflict
        # A manual is_deleted:false edit retains the 0.8.7 publisher stamp and is
        # therefore accepted below as an intentional recovery.
        prior_state = dict(self._quality_sync_state.get(quality_id, {}) or {})
        if bool(prior_state.get("deleted", False)):
            published = str(record.get("publisher_plugin_version", "") or "").strip()
            is_pre_soft_delete_writer = (
                not published
                or self._version_tuple(published) < self._version_tuple("0.8.7-beta")
            )
            if is_pre_soft_delete_writer:
                remote_hash = str(record.get("content_hash", "") or "")
                deleted_hash = str(prior_state.get("content_hash", "") or "")
                if deleted_hash and remote_hash == deleted_hash:
                    re_deleted = self._make_quality_tombstone(record)
                    path = os.path.join(root, "quality", quality_id + ".json")
                    self._atomic_write_json(path, re_deleted)
                    self._touch_manifest(root)
                    return self._apply_quality_tombstone(root, re_deleted, force=True)
                self._register_local_delete_remote_edit_conflict(quality_id, record)
                return "deletion-conflict"
        printer_path = os.path.join(root, "printers", printer_id + ".json")
        if not os.path.isfile(printer_path):
            return "missing-printer-record"
        printer_record = self._read_json(printer_path)
        target_machine = self._find_local_machine_for_record(printer_record)
        if target_machine is None:
            return "missing-machine"

        machine_definition_id = str(target_machine.definition.getId() or "")
        try:
            quality_definition = str(ContainerTree.getInstance().machines[machine_definition_id].quality_definition or "")
        except Exception:
            quality_definition = str(record.get("quality_definition", "") or "fdmprinter")
        registry = CuraContainerRegistry.getInstance()
        if not registry.findDefinitionContainers(id=quality_definition):
            return "missing-quality-definition"

        global_container, extruder_containers = self._quality_local_containers(quality_id)
        if global_container is None:
            try:
                same_name = registry.findInstanceContainersMetadata(
                    type="quality_changes", definition=quality_definition, name=str(record.get("name", "") or "Shared Profile")
                )
            except Exception:
                same_name = []
            for metadata in same_name:
                existing_eventide_id = str(metadata.get("eventide_record_id", "") or "").strip()
                if existing_eventide_id != quality_id:
                    return "name-conflict"
        remote_hash = str(record.get("content_hash", "") or "")
        remote_revision = int(record.get("revision", 0) or 0)
        state = dict(self._quality_sync_state.get(quality_id, {}) or {})
        resolution = self._quality_resolution(record)
        resolution_id = str(resolution.get("id", "") or "")
        seen_resolution_id = str(state.get("resolution_id", "") or "")

        if global_container is not None:
            local_payload = self._quality_payload_from_local_containers(printer_id, global_container, extruder_containers)
            local_hash = str(local_payload.get("content_hash", "") or "")
            if local_hash == remote_hash:
                self._clear_quality_conflict(quality_id)
                next_state = {"revision": remote_revision, "content_hash": remote_hash, "printer_id": printer_id}
                if resolution_id:
                    next_state["resolution_id"] = resolution_id
                self._quality_sync_state[quality_id] = next_state
                self._save_config()
                return "existing"
            seen_hash = str(state.get("content_hash", "") or "")
            seen_revision = int(state.get("revision", 0) or 0)
            authoritative_resolution = False
            if resolution_id and resolution_id != seen_resolution_id:
                losing_hashes = set(resolution.get("losing_hashes", []) or [])
                authoritative_resolution = (
                    local_hash in losing_hashes
                    or bool(seen_hash and local_hash == seen_hash)
                )
            if not force and not authoritative_resolution:
                if not seen_revision:
                    self._register_quality_conflict(quality_id, record, local_payload)
                    return "conflict"
                if local_hash != seen_hash:
                    if remote_revision != seen_revision:
                        self._register_quality_conflict(quality_id, record, local_payload)
                        return "conflict"
                    # Local moved while shared did not. Never overwrite it from a
                    # manual/automatic receive pass; the publisher will send it.
                    return "local-newer"

        name = str(record.get("name", "") or "Shared Profile")
        quality_type = str(record.get("quality_type", "not_supported") or "not_supported")
        intent = str(record.get("intent_category", "default") or "default")
        global_values = dict(record.get("global_values", {}) or {})
        remote_extruders = {int(item.get("position", 0)): dict(item.get("values", {}) or {}) for item in list(record.get("extruders", []) or []) if isinstance(item, dict)}

        if global_container is None:
            global_container = self._create_quality_container(machine_definition_id, record, quality_definition, None, global_values)
            created = True
        else:
            created = False
            global_container.setName(name, supress_signals=True)
            global_container.clear()
            global_container.setDefinition(quality_definition)
            global_container.setMetaDataEntry("quality_type", quality_type)
            if intent != "default":
                global_container.setMetaDataEntry("intent_category", intent)
            global_container.setMetaDataEntry("eventide_record_id", quality_id)
            global_container.setMetaDataEntry("eventide_printer_id", printer_id)
            self._apply_instance_values(global_container, global_values)

        target_extruders = {}
        for extruder in list(getattr(target_machine, "extruderList", []) or []):
            try:
                target_extruders[int(extruder.getMetaDataEntry("position") or 0)] = extruder
            except Exception:
                continue
        for position, values in remote_extruders.items():
            if position not in target_extruders:
                continue
            container = extruder_containers.get(position)
            if container is None:
                container = self._create_quality_container(target_extruders[position].getId(), record, quality_definition, position, values)
            else:
                container.setName(name, supress_signals=True)
                container.clear()
                container.setDefinition(quality_definition)
                container.setMetaDataEntry("quality_type", quality_type)
                if intent != "default":
                    container.setMetaDataEntry("intent_category", intent)
                container.setMetaDataEntry("position", position)
                container.setMetaDataEntry("eventide_record_id", quality_id)
                container.setMetaDataEntry("eventide_printer_id", printer_id)
                self._apply_instance_values(container, values)

        self._clear_quality_conflict(quality_id)
        next_state = {"revision": remote_revision, "content_hash": remote_hash, "printer_id": printer_id}
        if resolution_id:
            next_state["resolution_id"] = resolution_id
        self._quality_sync_state[quality_id] = next_state
        self._save_config()
        try:
            manager = self._application.getMachineManager()
            manager.activeQualityChanged.emit()
            manager.activeQualityGroupChanged.emit()
        except Exception:
            pass
        return "installed" if created else "updated"

    def _sync_quality_records(self, root: str) -> Tuple[Dict[str, int], List[str]]:
        stats = {
            "installed": 0, "updated": 0, "existing": 0, "local-newer": 0,
            "deleted": 0, "deleted-existing": 0, "deletion-conflict": 0,
            "conflict": 0, "name-conflict": 0, "missing-machine": 0,
            "missing-printer-record": 0, "missing-quality-definition": 0, "failed": 0
        }
        failures: List[str] = []
        quality_dir = os.path.join(root, "quality")
        try:
            names = sorted(name for name in os.listdir(quality_dir) if name.lower().endswith(".json"))
        except OSError:
            names = []
        # Read first, then apply deleted records before active records. A deleted
        # name may legitimately be reused by a newly-created profile with a new
        # Eventide ID; removing the old identity first avoids a transient false
        # name conflict.
        queued: List[Tuple[str, Dict[str, Any]]] = []
        for name in names:
            try:
                record = self._read_json(os.path.join(quality_dir, name))
                schema = str(record.get("schema", "") or "")
                if schema in (self.QUALITY_SCHEMA, self.QUALITY_TOMBSTONE_SCHEMA):
                    queued.append((name, record))
            except Exception as error:
                stats["failed"] += 1
                failures.append("{}: {}".format(name, error))
                Logger.logException("e", "Eventide quality sync could not read %s", name)

        queued.sort(
            key=lambda item: (
                0 if self._quality_is_deleted(item[1]) else 1,
                item[0],
            )
        )
        for name, record in queued:
            try:
                schema = str(record.get("schema", "") or "")
                if self._quality_is_deleted(record):
                    outcome = self._apply_quality_tombstone(root, record)
                else:
                    outcome = self._apply_quality_record(root, record)
                stats[outcome] = stats.get(outcome, 0) + 1
                if outcome == "conflict":
                    failures.append("{}: local and shared profile both changed".format(name))
                elif outcome == "deletion-conflict":
                    failures.append("{}: shared profile was deleted while this PC has an independent local edit".format(name))
                elif outcome == "name-conflict":
                    failures.append("{}: Cura already has a different custom profile with this name".format(name))
            except Exception as error:
                stats["failed"] += 1
                failures.append("{}: {}".format(name, error))
                Logger.logException("e", "Eventide quality sync failed for %s", name)
        if stats["conflict"] or stats["deletion-conflict"] or stats["name-conflict"]:
            self._last_quality_sync_summary = (
                "QUALITY CONFLICT: {} edit conflict(s), {} deletion conflict(s), {} name conflict(s)."
            ).format(stats["conflict"], stats["deletion-conflict"], stats["name-conflict"])
        elif stats["installed"] or stats["updated"] or stats["deleted"]:
            self._last_quality_sync_summary = "Live quality sync: {} installed, {} updated, {} deleted.".format(
                stats["installed"], stats["updated"], stats["deleted"]
            )
        elif sum(stats.values()):
            self._last_quality_sync_summary = "Live quality sync: shared profiles are current."
        return stats, failures

    @pyqtSlot(str, str, str, result=str)
    def resolveQualityConflict(self, requested_path: str, strategy: str, copy_name: str) -> str:
        """Resolve edit and deletion conflicts without silent data loss."""
        try:
            root = self._save_library_path_from_ui(requested_path)
            self._require_initialized_library(root)
            conflict = self._current_quality_conflict()
            if conflict is None:
                raise ValueError("there is no quality-profile conflict to resolve")
            quality_id = str(conflict.get("quality_id", "") or "").strip()
            path = os.path.join(root, "quality", quality_id + ".json")
            if not os.path.isfile(path):
                raise ValueError("the conflicting shared profile no longer exists")
            record = self._read_json(path)
            conflict_kind = str(conflict.get("kind", "edit") or "edit")
            action = str(strategy or "").strip().lower()

            if conflict_kind == "deletion":
                if not self._quality_is_deleted(record):
                    raise ValueError("the shared deletion was superseded; synchronize again")
                printer_id = str(record.get("printer_id", "") or "").strip()
                global_container, extruder_containers = self._quality_local_containers(quality_id)
                if global_container is None:
                    outcome = self._apply_quality_tombstone(root, record, force=True)
                    self._clear_quality_conflict(quality_id)
                    result = "CONFLICT RESOLVED: accepted deletion of '{}'.".format(record.get("name", "Shared Profile"))
                else:
                    local_payload = self._quality_payload_from_local_containers(
                        printer_id, global_container, extruder_containers
                    )
                    if action == "accept_deletion":
                        outcome = self._apply_quality_tombstone(root, record, force=True)
                        if outcome not in ("deleted", "deleted-existing"):
                            raise RuntimeError("shared deletion could not be applied ({})".format(outcome))
                        result = "CONFLICT RESOLVED: accepted deletion of '{}'.".format(local_payload.get("name", "Shared Profile"))
                    elif action == "keep_as_new":
                        requested = str(copy_name or "").strip() or "{} (Preserved - {})".format(
                            local_payload.get("name", "Shared Profile"), socket.gethostname()
                        )
                        _copy_id, final_name = self._create_quality_copy_from_payload(
                            root, record, local_payload, requested
                        )
                        outcome = self._apply_quality_tombstone(root, record, force=True)
                        if outcome not in ("deleted", "deleted-existing"):
                            raise RuntimeError("shared deletion could not be applied ({})".format(outcome))
                        self._touch_manifest(root)
                        result = "CONFLICT RESOLVED: preserved this PC's edit as '{}' and accepted deletion of the original.".format(final_name)
                    elif action == "restore_profile":
                        now = self._utc_now()
                        restored = dict(record)
                        restored.update({
                            "schema": self.QUALITY_SCHEMA,
                            "format": self.RECORD_FORMAT,
                            "id": quality_id,
                            "revision": int(record.get("revision", 0) or 0) + 1,
                            "created_utc": record.get("created_utc", now),
                            "updated_utc": now,
                            "updated_by": self._writer_info(),
                            "is_deleted": False,
                            **local_payload,
                            "restored_from_deletion": {
                                "deletion_id": str(self._quality_tombstone_deletion(record).get("id", "") or ""),
                                "restored_utc": now,
                                "restored_by": self._writer_info(),
                            },
                        })
                        restored.pop("deleted", None)
                        self._atomic_write_json(path, restored)
                        self._quality_sync_state[quality_id] = {
                            "revision": int(restored["revision"]),
                            "content_hash": str(local_payload.get("content_hash", "") or ""),
                            "printer_id": printer_id,
                        }
                        self._touch_manifest(root)
                        self._save_config()
                        self._clear_quality_conflict(quality_id)
                        result = "CONFLICT RESOLVED: restored '{}' as the shared profile.".format(local_payload.get("name", "Shared Profile"))
                    else:
                        raise ValueError("unknown deletion-conflict action")

            elif conflict_kind == "local_delete_remote_edit":
                if record.get("schema") != self.QUALITY_SCHEMA:
                    raise ValueError("the shared edit was superseded; synchronize again")
                if action == "accept_deletion":
                    tombstone = self._make_quality_tombstone(record)
                    self._atomic_write_json(path, tombstone)
                    deletion = self._quality_tombstone_deletion(tombstone)
                    self._quality_sync_state[quality_id] = {
                        "revision": int(tombstone.get("revision", 0) or 0),
                        "content_hash": str(deletion.get("deleted_content_hash", "") or tombstone.get("content_hash", "") or ""),
                        "printer_id": str(tombstone.get("printer_id", "") or ""),
                        "deleted": True,
                        "deletion_id": str(deletion.get("id", "") or ""),
                    }
                    self._touch_manifest(root)
                    self._save_config()
                    self._clear_quality_conflict(quality_id)
                    result = "CONFLICT RESOLVED: deleted '{}' after reviewing the newer shared edit.".format(record.get("name", "Shared Profile"))
                elif action == "restore_profile":
                    outcome = self._apply_quality_record(root, record, force=True)
                    if outcome not in ("installed", "updated", "existing"):
                        raise RuntimeError("shared profile could not be restored ({})".format(outcome))
                    self._clear_quality_conflict(quality_id)
                    result = "CONFLICT RESOLVED: restored the newer shared '{}' on this PC.".format(record.get("name", "Shared Profile"))
                else:
                    raise ValueError("choose Accept Deletion or Restore Profile for this conflict")

            else:
                if record.get("schema") != self.QUALITY_SCHEMA or self._quality_is_deleted(record):
                    raise ValueError("the shared profile changed state; synchronize again")
                printer_id = str(record.get("printer_id", "") or "").strip()
                global_container, extruder_containers = self._quality_local_containers(quality_id)
                if global_container is None:
                    raise ValueError("the conflicting local Cura profile is no longer installed")
                local_payload = self._quality_payload_from_local_containers(
                    printer_id, global_container, extruder_containers
                )

                if action == "keep_local":
                    remote_revision = int(record.get("revision", 0) or 0)
                    now = self._utc_now()
                    resolution_id = str(uuid.uuid4())
                    winning_hash = str(local_payload.get("content_hash", "") or "")
                    losing_hash = str(record.get("content_hash", "") or "")
                    winning = {
                        "schema": self.QUALITY_SCHEMA,
                        "format": self.RECORD_FORMAT,
                        "id": quality_id,
                        "revision": remote_revision + 1,
                        "created_utc": record.get("created_utc", now),
                        "updated_utc": now,
                        "updated_by": self._writer_info(),
                        "is_deleted": False,
                        **local_payload,
                        "resolution": {
                            "id": resolution_id,
                            "strategy": "keep_local",
                            "resolved_utc": now,
                            "resolved_by": self._writer_info(),
                            "supersedes_revision": remote_revision,
                            "winning_hash": winning_hash,
                            "losing_hashes": [losing_hash] if losing_hash and losing_hash != winning_hash else [],
                        },
                    }
                    self._atomic_write_json(path, winning)
                    self._quality_sync_state[quality_id] = {
                        "revision": remote_revision + 1,
                        "content_hash": winning_hash,
                        "printer_id": printer_id,
                        "resolution_id": resolution_id,
                    }
                    self._touch_manifest(root)
                    self._clear_quality_conflict(quality_id)
                    self._save_config()
                    result = "CONFLICT RESOLVED: kept this PC's '{}' as the shared version.".format(local_payload.get("name", "Shared Profile"))

                elif action == "use_shared":
                    outcome = self._apply_quality_record(root, record, force=True)
                    if outcome not in ("installed", "updated", "existing"):
                        raise RuntimeError("shared profile could not be applied ({})".format(outcome))
                    self._clear_quality_conflict(quality_id)
                    result = "CONFLICT RESOLVED: this PC now uses the shared '{}'.".format(record.get("name", "Shared Profile"))

                elif action == "create_copy":
                    requested = str(copy_name or "").strip()
                    if not requested:
                        requested = "{} (Conflict copy - {})".format(
                            local_payload.get("name", "Shared Profile"), socket.gethostname()
                        )
                    _copy_id, final_name = self._create_quality_copy_from_payload(
                        root, record, local_payload, requested
                    )
                    outcome = self._apply_quality_record(root, record, force=True)
                    if outcome not in ("installed", "updated", "existing"):
                        raise RuntimeError("shared original could not be restored ({})".format(outcome))
                    self._touch_manifest(root)
                    self._clear_quality_conflict(quality_id)
                    self._save_config()
                    result = "CONFLICT RESOLVED: preserved this PC's edit as '{}' and restored the shared original.".format(final_name)
                else:
                    raise ValueError("unknown conflict-resolution action")

            self._library_content_signature = None
            QTimer.singleShot(0, self._poll_shared_library)
            self._last_quality_sync_summary = result
            remaining_conflicts = len(self._quality_conflicts)
            if remaining_conflicts:
                self._last_sync_summary = "SYNC: conflict resolved; {} unresolved quality conflict(s) remain.".format(remaining_conflicts)
            else:
                self._last_sync_summary = "SYNC: conflict resolved; no unresolved quality-profile conflicts."
            self._last_library_event = "Quality conflict resolved at {}.".format(self._utc_now())
            self._status = result
        except Exception as error:
            result = "CONFLICT RESOLUTION FAILED: {}".format(error)
            self._status = result
            self._last_quality_sync_summary = result
            Logger.logException("e", "Eventide quality conflict resolution failed")
        self.stateChanged.emit()
        return result

    @pyqtSlot()
    def nextQualityConflict(self) -> None:
        if self._quality_conflict_order:
            self._quality_conflict_index = (self._quality_conflict_index + 1) % len(self._quality_conflict_order)
            self.stateChanged.emit()

    @pyqtSlot()
    def previousQualityConflict(self) -> None:
        if self._quality_conflict_order:
            self._quality_conflict_index = (self._quality_conflict_index - 1) % len(self._quality_conflict_order)
            self.stateChanged.emit()

    @pyqtSlot(str, result=str)
    def syncLibraryToCura(self, requested_path: str) -> str:
        """Install missing published materials and machine instances into this Cura profile."""
        material_stats = {"installed": 0, "existing": 0, "unpublished": 0, "builtin-missing": 0, "failed": 0}
        machine_stats = {"installed": 0, "existing": 0, "unpublished": 0, "missing-definition": 0, "failed": 0}
        quality_stats = {"installed": 0, "updated": 0, "existing": 0, "local-newer": 0, "deleted": 0, "deleted-existing": 0, "deletion-conflict": 0, "conflict": 0, "name-conflict": 0, "failed": 0}
        failures = []
        try:
            root = self._save_library_path_from_ui(requested_path)
            self._require_initialized_library(root)

            filament_dir = os.path.join(root, "filaments")
            for name in sorted(os.listdir(filament_dir)):
                if not name.lower().endswith(".json"):
                    continue
                try:
                    record = self._read_json(os.path.join(filament_dir, name))
                    if record.get("schema") != "eventide.shared_profiles.filament":
                        continue
                    outcome = self._install_material_record(record)
                    material_stats[outcome] = material_stats.get(outcome, 0) + 1
                except Exception as error:
                    material_stats["failed"] += 1
                    failures.append("{}: {}".format(name, error))
                    Logger.logException("e", "Eventide material sync failed for %s", name)

            printer_dir = os.path.join(root, "printers")
            for name in sorted(os.listdir(printer_dir)):
                if not name.lower().endswith(".json"):
                    continue
                try:
                    record = self._read_json(os.path.join(printer_dir, name))
                    if record.get("schema") != "eventide.shared_profiles.printer":
                        continue
                    outcome = self._install_machine_record(record)
                    if outcome.startswith("missing-definition:"):
                        machine_stats["missing-definition"] += 1
                        failures.append("{}: target Cura lacks {}".format(name, outcome.split(":", 1)[1]))
                    else:
                        machine_stats[outcome] = machine_stats.get(outcome, 0) + 1
                except Exception as error:
                    machine_stats["failed"] += 1
                    failures.append("{}: {}".format(name, error))
                    Logger.logException("e", "Eventide machine sync failed for %s", name)

            quality_stats, quality_failures = self._sync_quality_records(root)
            failures.extend(quality_failures)

            self._refresh_library_state_internal(require_manifest=True)
            self.refreshSelection()
            self._last_sync_summary = (
                "SYNC: materials +{} / {} local; machines +{} / {} local; "
                "quality +{} / {} updated / {} local / {} deleted; {} conflict(s); {} failure(s)."
            ).format(
                material_stats.get("installed", 0), material_stats.get("existing", 0),
                machine_stats.get("installed", 0), machine_stats.get("existing", 0),
                quality_stats.get("installed", 0), quality_stats.get("updated", 0), quality_stats.get("existing", 0), quality_stats.get("deleted", 0),
                quality_stats.get("conflict", 0) + quality_stats.get("deletion-conflict", 0) + quality_stats.get("name-conflict", 0), len(failures),
            )
            if failures:
                self._last_sync_summary += " First: {}".format(failures[0])
            self._status = self._last_sync_summary
        except Exception as error:
            self._last_sync_summary = "SYNC FAILED: {}".format(error)
            self._status = self._last_sync_summary
            Logger.logException("e", "Eventide library-to-Cura sync failed")
        self.stateChanged.emit()
        return self._status

    @pyqtSlot(str, result=str)
    def validateLibrary(self, requested_path: str) -> str:
        errors = []
        warnings = []
        try:
            root = self._save_library_path_from_ui(requested_path)
            manifest = self._require_initialized_library(root)
            if int(manifest.get("record_format", self.RECORD_FORMAT) or 0) > self.RECORD_FORMAT:
                errors.append("library record format is newer than this plugin")

            expected = {
                "printers": "eventide.shared_profiles.printer",
                "filaments": "eventide.shared_profiles.filament",
                "capabilities": "eventide.shared_profiles.capability",
                "quality": self.QUALITY_SCHEMA,
            }
            ids_by_folder: Dict[str, set] = {name: set() for name in expected}
            records_by_folder: Dict[str, list] = {name: [] for name in expected}

            for folder, schema in expected.items():
                folder_path = os.path.join(root, folder)
                if not os.path.isdir(folder_path):
                    errors.append("missing folder {}".format(folder))
                    continue
                for name in sorted(os.listdir(folder_path)):
                    if not name.lower().endswith(".json"):
                        continue
                    path = os.path.join(folder_path, name)
                    try:
                        record = self._read_json(path)
                    except Exception as error:
                        errors.append("{}: invalid JSON ({})".format(name, error))
                        continue
                    record_schema = str(record.get("schema", "") or "")
                    allowed_schemas = {schema}
                    if folder == "quality":
                        allowed_schemas.add(self.QUALITY_TOMBSTONE_SCHEMA)
                    if record_schema not in allowed_schemas:
                        errors.append("{}: schema {}".format(name, record.get("schema")))
                        continue
                    record_id = str(record.get("id", "") or "").strip()
                    if not record_id:
                        errors.append("{}: missing id".format(name))
                        continue
                    if record_id in ids_by_folder[folder]:
                        errors.append("{}: duplicate id {}".format(name, record_id))
                    ids_by_folder[folder].add(record_id)
                    records_by_folder[folder].append((name, record))
                    try:
                        if int(record.get("revision", 0) or 0) < 1:
                            errors.append("{}: invalid revision".format(name))
                    except Exception:
                        errors.append("{}: invalid revision".format(name))

            for name, record in records_by_folder["filaments"]:
                definition = record.get("material_definition")
                if not isinstance(definition, dict):
                    warnings.append("{}: material definition not published; other PCs cannot install it yet".format(name))
                elif definition.get("serialized"):
                    serialized = str(definition.get("serialized") or "")
                    expected_hash = str(definition.get("sha256", "") or "")
                    if expected_hash and hashlib.sha256(serialized.encode("utf-8")).hexdigest() != expected_hash:
                        errors.append("{}: material payload hash mismatch".format(name))

            for name, record in records_by_folder["printers"]:
                definition = record.get("machine_definition")
                if not isinstance(definition, dict):
                    warnings.append("{}: machine definition changes not published; other PCs cannot recreate it yet".format(name))

            for name, record in records_by_folder["quality"]:
                if record.get("printer_id") not in ids_by_folder["printers"]:
                    errors.append("{}: missing printer reference".format(name))
                if not str(record.get("name", "") or "").strip():
                    errors.append("{}: quality profile name is blank".format(name))
                if record.get("schema") == self.QUALITY_TOMBSTONE_SCHEMA:
                    # Legacy 0.8.6 destructive tombstone. Readable for migration,
                    # but 0.8.7 never writes this shape.
                    deletion = self._quality_tombstone_deletion(record)
                    if not record.get("deleted") or not str(deletion.get("id", "") or ""):
                        errors.append("{}: invalid legacy quality tombstone".format(name))
                    continue
                if "is_deleted" in record and not isinstance(record.get("is_deleted"), bool):
                    errors.append("{}: is_deleted must be true or false".format(name))
                if self._quality_is_deleted(record):
                    deletion = self._quality_tombstone_deletion(record)
                    if not str(deletion.get("id", "") or ""):
                        errors.append("{}: deleted quality record is missing deletion metadata".format(name))
                content_hash = str(record.get("content_hash", "") or "")
                payload = {
                    "printer_id": record.get("printer_id"),
                    "name": record.get("name"),
                    "quality_type": record.get("quality_type"),
                    "intent_category": record.get("intent_category", "default"),
                    "quality_definition": record.get("quality_definition", ""),
                    "global_values": record.get("global_values", {}),
                    "extruders": record.get("extruders", []),
                }
                if content_hash and self._quality_hash(payload) != content_hash:
                    errors.append("{}: quality content hash mismatch".format(name))

            for name, record in records_by_folder["capabilities"]:
                if record.get("printer_id") not in ids_by_folder["printers"]:
                    errors.append("{}: missing printer reference".format(name))
                if record.get("filament_id") not in ids_by_folder["filaments"]:
                    errors.append("{}: missing filament reference".format(name))
                hotend = record.get("hotend", {})
                if hotend.get("nozzle_diameter_mm") in (None, ""):
                    warnings.append("{}: nozzle diameter unset".format(name))
                tuning = record.get("tuning", {})
                if (
                    tuning.get("emit_klipper_pressure_advance")
                    and tuning.get("pressure_advance") in (None, "")
                ):
                    warnings.append(
                        "{}: Klipper PA emit enabled but PA is unset".format(name)
                    )

            self._refresh_library_state_internal(require_manifest=True)
            if errors:
                self._library_validation_summary = "VALIDATION FAILED: {} error(s), {} warning(s). First: {}".format(
                    len(errors), len(warnings), errors[0]
                )
            elif warnings:
                self._library_validation_summary = "VALIDATION OK WITH WARNINGS: {} warning(s). First: {}".format(
                    len(warnings), warnings[0]
                )
            else:
                self._library_validation_summary = "VALIDATION OK: library structure and references are consistent."
            self._status = self._library_validation_summary
        except Exception as error:
            self._library_validation_summary = "VALIDATION FAILED: {}".format(error)
            self._status = self._library_validation_summary
            Logger.logException("e", "Eventide library validation failed")
        self.stateChanged.emit()
        return self._status

    @pyqtSlot(result=str)
    def exportDiagnostics(self) -> str:
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            suggested = os.path.join(
                os.path.expanduser("~"), f"eventide-diagnostics-{stamp}.txt"
            )
            path, _selected_filter = QFileDialog.getSaveFileName(
                None,
                "Save Eventide diagnostics",
                suggested,
                "Text files (*.txt);;All files (*)",
            )
            if not path:
                self._status = "DIAGNOSTICS CANCELLED"
                self.stateChanged.emit()
                return self._status
            lines = [
                "Eventide Shared Profiles diagnostics",
                "generated_utc={}".format(self._utc_now()),
                "plugin_version={}".format(self.PLUGIN_VERSION),
                "cura_version={}".format(CuraVersion),
                "python={}".format(sys.version.replace("\n", " ")),
                "platform={}".format(platform.platform()),
                "local_state=Cura preferences",
                "shared_library_path={}".format(self._shared_library_path),
                "client_id={}".format(self._client_id),
                "hostname={}".format(socket.gethostname()),
                "slice_hook_active={}".format(self._slice_hook_installed),
                "last_slice_resolution={}".format(self._last_slice_resolution),
                "gcode_postprocessing=disabled; capabilities apply before slicing",
                "last_library_event={}".format(self._last_library_event),
                "library_validation={}".format(self._library_validation_summary),
                "last_sync={}".format(self._last_sync_summary),
                "inventory=printers:{},filaments:{},capabilities:{},quality:{}".format(
                    self._printer_count, self._filament_count, self._capability_count, self._quality_count
                ),
                "active_printer_name={}".format(self._active_printer_name),
                "active_printer_id={}".format(self._active_printer_id),
                "active_material_name={}".format(self._active_material_name),
                "active_material_id={}".format(self._active_material_id),
                "active_extruder={}".format(self._active_extruder_position),
                "active_nozzle_diameter={}".format(self._active_nozzle_diameter),
                "active_nozzle_material={}".format(self._active_nozzle_material),
                "status={}".format(self._status),
            ]
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            self._status = "DIAGNOSTICS EXPORTED: {}".format(path)
        except Exception as error:
            self._status = "DIAGNOSTICS FAILED: {}".format(error)
            Logger.logException("e", "Eventide diagnostics export failed")
        self.stateChanged.emit()
        return self._status

    @pyqtSlot()
    def showWindow(self) -> None:
        self._ensure_runtime_hooks()
        self.refreshSelection()
        self._refresh_library_state_internal()
        if self._window is None:
            plugin_path = cast(str, self._application.getPluginRegistry().getPluginPath(self.getPluginId()))
            qml_path = os.path.join(plugin_path, "qml", "EventideSharedProfilesWindow.qml")
            self._window = self._application.createQmlComponent(qml_path, {"eventideBridge": self})
        if self._window is not None:
            self._window.show()

    @pyqtSlot(result=str)
    def ping(self) -> str:
        return "Python hook OK — Eventide Shared Profiles v{}".format(self.PLUGIN_VERSION)

    @pyqtSlot(result=str)
    def browseForLibraryPath(self) -> str:
        """Open the native folder picker; native Windows dialog supports UNC/network browsing."""
        try:
            start = self._shared_library_path or os.path.expanduser("~")
            selected = QFileDialog.getExistingDirectory(None, "Select Eventide Shared Library", start, QFileDialog.Option.ShowDirsOnly)
            return str(selected or "")
        except Exception as error:
            Logger.logException("e", "Eventide library folder browser failed")
            self._status = "BROWSE FAILED: {}".format(error)
            self.stateChanged.emit()
            return ""

    @pyqtSlot(str, result=str)
    def connectLibrary(self, requested_path: str) -> str:
        """Normal first-run action: save an existing library, refresh, and synchronize it."""
        try:
            root = self._save_library_path_from_ui(requested_path)
            self._require_initialized_library(root)
            self._refresh_library_state_internal(require_manifest=True)
            self._library_content_signature = None  # Force an immediate full live-sync pass.
            result = self.syncLibraryToCura(requested_path)
            if result.startswith("SYNC FAILED"):
                return result
            self._library_content_signature = None
            self._library_available = True
            self._local_quality_dirty = True
            QTimer.singleShot(0, self._poll_shared_library)
            self._status = "CONNECTED: live sync is active."
        except Exception as error:
            self._status = "CONNECT FAILED: {}".format(error)
            Logger.logException("e", "Eventide could not connect shared library")
        self.stateChanged.emit()
        return self._status

    @pyqtSlot(str, result=str)
    def saveSharedLibraryPath(self, requested_path: str) -> str:
        try:
            self._save_library_path_from_ui(requested_path)
            self._status = "PATH SAVED: {}".format(self._shared_library_path)
        except Exception as error:
            self._status = "SAVE FAILED: {}".format(error)
            Logger.logException("e", "Eventide Shared Profiles path save failed")
        self.stateChanged.emit()
        return self._status

    @pyqtSlot(str, result=str)
    def testConnection(self, requested_path: str) -> str:
        try:
            normalized = self._save_library_path_from_ui(requested_path)
        except Exception as error:
            self._status = "TEST FAILED: {}".format(error)
            self.stateChanged.emit()
            return self._status

        probe_directory = normalized
        target_exists = os.path.isdir(normalized)
        if not target_exists:
            parent = os.path.dirname(normalized.rstrip("\\/"))
            if not parent or not os.path.isdir(parent):
                self._status = "NOT REACHABLE: {}".format(normalized)
                self.stateChanged.emit()
                return self._status
            probe_directory = parent

        probe_path = os.path.join(probe_directory, ".eventide_probe_{}_{}.tmp".format(socket.gethostname(), os.getpid()))
        payload = "Eventide Shared Profiles connection test\n"
        try:
            with open(probe_path, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            with open(probe_path, "r", encoding="utf-8") as handle:
                if handle.read() != payload:
                    raise OSError("write verification failed")
            os.remove(probe_path)
            if target_exists:
                self._status = "CONNECTION OK: library path is read/write."
            else:
                self._status = "CONNECTION OK: parent share is read/write; library folder does not exist yet."
        except Exception as error:
            try:
                if os.path.exists(probe_path):
                    os.remove(probe_path)
            except OSError:
                pass
            self._status = "CONNECTION FAILED: {}".format(error)
            Logger.logException("e", "Eventide Shared Profiles connection test failed")
        self.stateChanged.emit()
        return self._status

    @pyqtSlot(str, result=str)
    def initializeLibrary(self, requested_path: str) -> str:
        try:
            normalized = self._save_library_path_from_ui(requested_path)
            os.makedirs(normalized, exist_ok=True)
            for name in (".eventide", "printers", "filaments", "capabilities", "quality"):
                os.makedirs(os.path.join(normalized, name), exist_ok=True)
            manifest_path = self._manifest_path(normalized)
            if not os.path.isfile(manifest_path):
                now = self._utc_now()
                self._atomic_write_json(manifest_path, {
                    "format": self.LIBRARY_FORMAT,
                    "record_format": self.RECORD_FORMAT,
                    "name": "Eventide Shared Profiles",
                    "created_utc": now,
                    "updated_utc": now,
                    "updated_by": self._writer_info(),
                })
            else:
                manifest = self._read_json(manifest_path)
                manifest_changed = False
                if "record_format" not in manifest:
                    manifest["record_format"] = self.RECORD_FORMAT
                    manifest_changed = True
                if str(manifest.get("publisher_plugin_version", "") or "") != self.PUBLISHER_PLUGIN_VERSION:
                    manifest_changed = True
                if manifest_changed:
                    manifest["updated_utc"] = self._utc_now()
                    manifest["updated_by"] = self._writer_info()
                    self._atomic_write_json(manifest_path, manifest)
            self._refresh_library_state_internal()
            self._library_manifest_signature = self._get_manifest_signature()
            self._status = "LIBRARY READY: {}".format(normalized)
        except Exception as error:
            self._status = "INITIALIZE FAILED: {}".format(error)
            Logger.logException("e", "Eventide Shared Profiles initialization failed")
        self.stateChanged.emit()
        return self._status

    @pyqtSlot(str, result=str)
    def refreshLibrary(self, requested_path: str) -> str:
        try:
            self._save_library_path_from_ui(requested_path)
            self._refresh_library_state_internal(require_manifest=True)
            self._library_manifest_signature = self._get_manifest_signature()
            self._status = ("LIBRARY REFRESHED: {} printer(s), {} filament(s), "
                            "{} capability record(s), {} quality profile(s).").format(
                self._printer_count, self._filament_count, self._capability_count, self._quality_count)
        except Exception as error:
            self._status = "REFRESH FAILED: {}".format(error)
            Logger.logException("e", "Eventide Shared Profiles library refresh failed")
        self.stateChanged.emit()
        return self._status

    @pyqtSlot(str, result=str)
    def registerCurrentSelection(self, requested_path: str) -> str:
        try:
            root = self._save_library_path_from_ui(requested_path)
            self._require_initialized_library(root)
            self.refreshSelection()

            printer_record_id, filament_record_id = self._base_record_ids()
            position, active_nozzle = self._active_toolhead()

            printer_path = os.path.join(
                root,
                "printers",
                printer_record_id + ".json",
            )
            filament_path = os.path.join(
                root,
                "filaments",
                filament_record_id + ".json",
            )

            _, printer_changed = self._identity_upsert(
                printer_path,
                "eventide.shared_profiles.printer",
                printer_record_id,
                self._active_printer_id,
                self._active_printer_name,
            )
            _, filament_changed = self._identity_upsert(
                filament_path,
                "eventide.shared_profiles.filament",
                filament_record_id,
                self._active_material_id,
                self._active_material_name,
            )

            # Pin this local Cura machine instance to the shared Eventide printer
            # identity.  Imported machines already carry this metadata; stamping the
            # source machine too makes identity resolution symmetric on every PC.
            try:
                global_stack = self._application.getGlobalContainerStack()
                if global_stack is not None:
                    global_stack.setMetaDataEntry("eventide_record_id", printer_record_id)
                    self._machine_bindings[printer_record_id] = str(global_stack.getId() or "").strip()
                    self._save_config()
            except Exception:
                Logger.logException("w", "Eventide could not bind source machine to shared printer identity")

            # Publish portable sync payloads. Built-in materials are recorded as
            # built-in identities without duplicating Cura's read-only XML. Machine
            # snapshots intentionally capture definitionChanges only, never current
            # quality/user slicing overrides.
            material_payload_changed = self._update_record_section(
                filament_path, "material_definition", self._capture_active_material_definition()
            )
            machine_payload_changed = self._update_record_section(
                printer_path, "machine_definition", self._capture_active_machine_definition()
            )

            candidates = self._capability_candidates(
                root, preferred_nozzle_material=(self._active_nozzle_material or None)
            )
            capability_changed = False

            if len(candidates) == 1:
                capability_path, record = candidates[0]

                # Upgrade the legacy record in place with current toolhead data
                # when those fields were previously blank.
                hotend = record.setdefault("hotend", {})
                changed = False

                if hotend.get("nozzle_diameter_mm") in (None, "") and active_nozzle is not None:
                    hotend["nozzle_diameter_mm"] = active_nozzle
                    changed = True

                if not str(hotend.get("nozzle_material", "") or "").strip():
                    hotend["nozzle_material"] = "Unspecified"
                    changed = True

                if int(record.get("extruder", 0) or 0) != position:
                    record["extruder"] = position
                    changed = True

                if changed:
                    record["revision"] = int(record.get("revision", 0) or 0) + 1
                    record["updated_utc"] = self._utc_now()
                    record["updated_by"] = self._writer_info()
                    self._atomic_write_json(capability_path, record)
                    capability_changed = True

            elif len(candidates) > 1:
                raise ValueError(
                    "multiple capability records already match the current "
                    "printer/material/nozzle"
                )
            else:
                nozzle_material = "Unspecified"
                capability_id = self._canonical_capability_id(
                    printer_record_id,
                    filament_record_id,
                    position,
                    active_nozzle,
                    nozzle_material,
                )
                capability_path = os.path.join(
                    root,
                    "capabilities",
                    capability_id + ".json",
                )

                if os.path.isfile(capability_path):
                    record = self._read_json(capability_path)
                else:
                    record = self._create_capability_record(
                        capability_path,
                        capability_id,
                        printer_record_id,
                        filament_record_id,
                        active_nozzle,
                        nozzle_material,
                        position,
                    )
                    capability_changed = True

            self._touch_manifest(root)
            self._publish_local_quality_profiles(root)
            self._refresh_library_state_internal(require_manifest=True)
            self._set_capability_editor_from_record(record)
            registered_material = str(record.get("hotend", {}).get("nozzle_material", "") or "").strip()
            if registered_material and self._active_printer_id:
                self._bind_nozzle_material(
                    self._active_printer_id, position, active_nozzle, registered_material
                )
                self._active_nozzle_material = registered_material

            changed_parts = []
            if printer_changed or machine_payload_changed:
                changed_parts.append("printer")
            if filament_changed or material_payload_changed:
                changed_parts.append("filament")
            if capability_changed:
                changed_parts.append("capability")
            action = (
                "wrote " + ", ".join(changed_parts)
                if changed_parts
                else "records already current"
            )

            self._status = (
                "REGISTERED: {} + {} + nozzle {} mm / {} ({})"
            ).format(
                self._active_printer_name,
                self._active_material_name,
                self._display_number(active_nozzle),
                record.get("hotend", {}).get("nozzle_material", "Unspecified"),
                action,
            )

        except Exception as error:
            self._status = "REGISTER FAILED: {}".format(error)
            Logger.logException(
                "e",
                "Eventide Shared Profiles registration failed",
            )

        self.stateChanged.emit()
        return self._status

    @pyqtSlot(str, result=str)
    def loadCurrentCapability(self, requested_path: str) -> str:
        try:
            root = self._save_library_path_from_ui(requested_path)
            self._require_initialized_library(root)
            self.refreshSelection()

            preferred_material = (
                self._active_nozzle_material
                or (self._capability_nozzle_material if self._capability_loaded else "")
                or None
            )
            _, record = self._find_current_capability(
                root,
                preferred_nozzle_material=preferred_material,
            )

            self._set_capability_editor_from_record(record)
            position, active_nozzle = self._active_toolhead()
            loaded_material = str(record.get("hotend", {}).get("nozzle_material", "") or "").strip()
            if loaded_material and self._active_printer_id:
                self._bind_nozzle_material(
                    self._active_printer_id, position, active_nozzle, loaded_material
                )
                self._active_nozzle_material = loaded_material
            self._status = "CAPABILITY LOADED: revision {} | nozzle {} mm / {}".format(
                self._capability_revision,
                self._capability_nozzle_diameter or self._active_nozzle_diameter,
                self._capability_nozzle_material or "Unspecified",
            )

        except Exception as error:
            self._clear_capability_editor()
            self._status = "CAPABILITY LOAD FAILED: {}".format(error)
            Logger.logException("e", "Eventide capability load failed")

        self.stateChanged.emit()
        return self._status

    @pyqtSlot(str, str, result=str)
    def saveCurrentCapability(self, requested_path: str, payload_json: str) -> str:
        try:
            root = self._save_library_path_from_ui(requested_path)
            self._require_initialized_library(root)
            self.refreshSelection()

            if not self._capability_loaded or not self._capability_record_id:
                raise ValueError("load the current capability before saving")

            payload = json.loads(str(payload_json or "{}"))
            if not isinstance(payload, dict):
                raise ValueError("capability payload must be an object")

            # Find the exact loaded record by ID so changing nozzle material in
            # the editor can safely migrate it to a new canonical ID.
            capability_path = os.path.join(
                root,
                "capabilities",
                self._capability_record_id + ".json",
            )

            if not os.path.isfile(capability_path):
                # Legacy file name may not match record ID. Search by current
                # selection and loaded nozzle material.
                capability_path, record = self._find_current_capability(
                    root,
                    preferred_nozzle_material=(
                        self._capability_nozzle_material or None
                    ),
                )
            else:
                record = self._read_json(capability_path)

            if record.get("schema") != "eventide.shared_profiles.capability":
                raise ValueError("current capability record has the wrong schema")

            current_revision = int(record.get("revision", 0) or 0)
            expected_revision = int(payload.get("expected_revision", 0) or 0)

            if expected_revision != current_revision:
                self._status = (
                    "CAPABILITY CONFLICT: shared record is revision {}, "
                    "but this editor loaded revision {}. Reload before saving."
                ).format(current_revision, expected_revision)
                self.stateChanged.emit()
                return self._status

            limits = record.setdefault("limits", {})
            tuning = record.setdefault("tuning", {})
            hotend = record.setdefault("hotend", {})
            calibration = record.setdefault("calibration", {})

            limits["max_volumetric_flow_mm3_s"] = self._optional_float(
                payload.get("max_volumetric_flow_mm3_s"),
                "Maximum volumetric flow",
                strictly_positive=True,
            )
            limits["max_linear_speed_mm_s"] = self._optional_float(
                payload.get("max_linear_speed_mm_s"),
                "Maximum linear speed",
                strictly_positive=True,
            )

            pressure_advance = self._optional_float(
                payload.get("pressure_advance"),
                "Pressure advance",
                minimum=0.0,
            )
            emit_klipper_pa = bool(
                payload.get("emit_klipper_pressure_advance", False)
            )
            if emit_klipper_pa and pressure_advance is None:
                raise ValueError(
                    "Pressure advance must be set when Klipper PA emission is enabled"
                )
            tuning["pressure_advance"] = pressure_advance
            tuning["emit_klipper_pressure_advance"] = emit_klipper_pa
            tuning["flow_percent"] = self._optional_float(
                payload.get("flow_percent"),
                "Material flow",
                strictly_positive=True,
            )
            tuning["temperature_offset_c"] = self._optional_float(
                payload.get("temperature_offset_c"),
                "Temperature offset",
            )
            tuning["retraction_distance_mm"] = self._optional_float(
                payload.get("retraction_distance_mm"),
                "Retraction distance",
                minimum=0.0,
            )
            tuning["retraction_speed_mm_s"] = self._optional_float(
                payload.get("retraction_speed_mm_s"),
                "Retraction speed",
                minimum=0.0,
            )

            position, active_nozzle = self._active_toolhead()
            if active_nozzle is None:
                active_nozzle = self._optional_float(
                    payload.get("nozzle_diameter_mm"),
                    "Nozzle diameter",
                    strictly_positive=True,
                )

            hotend["nozzle_diameter_mm"] = active_nozzle
            nozzle_material = str(
                payload.get("nozzle_material", "") or ""
            ).strip() or "Unspecified"
            hotend["nozzle_material"] = nozzle_material
            record["extruder"] = position

            calibration["notes"] = str(payload.get("notes", "") or "")
            mark_calibrated = bool(payload.get("mark_calibrated", False))
            if mark_calibrated:
                calibration["status"] = "calibrated"
                calibration["last_calibrated_utc"] = self._utc_now()
            elif str(calibration.get("status", "uncalibrated") or "uncalibrated") == "calibrated":
                calibration["status"] = "needs_recalibration"
            else:
                calibration.setdefault("status", "uncalibrated")

            printer_record_id, filament_record_id = self._base_record_ids()
            canonical_id = self._canonical_capability_id(
                printer_record_id,
                filament_record_id,
                position,
                active_nozzle,
                nozzle_material,
            )
            canonical_path = os.path.join(
                root,
                "capabilities",
                canonical_id + ".json",
            )

            if (
                canonical_path != capability_path
                and os.path.isfile(canonical_path)
            ):
                raise FileExistsError(
                    "a capability already exists for this exact "
                    "printer/material/nozzle configuration"
                )

            record["id"] = canonical_id
            record["revision"] = current_revision + 1
            record["updated_utc"] = self._utc_now()
            record["updated_by"] = self._writer_info()

            self._atomic_write_json(canonical_path, record)

            if canonical_path != capability_path:
                try:
                    os.remove(capability_path)
                except OSError:
                    pass

            self._touch_manifest(root)
            self._set_capability_editor_from_record(record)
            self._bind_nozzle_material(
                self._active_printer_id,
                position,
                active_nozzle,
                nozzle_material,
            )
            self._active_nozzle_material = nozzle_material

            self._status = "CAPABILITY SAVED: revision {} | toolhead {} mm / {}".format(
                self._capability_revision,
                self._capability_nozzle_diameter,
                self._capability_nozzle_material,
            )

        except Exception as error:
            self._status = "CAPABILITY SAVE FAILED: {}".format(error)
            Logger.logException("e", "Eventide capability save failed")

        self.stateChanged.emit()
        return self._status

    def _get_active_extruder_stack(self) -> Any:
        """Return Cura's selected/default extruder without inventing an enabled state.

        Cura explicitly allows disabled extruders. If the UI is currently editing an
        extruder, return exactly that stack even when it is disabled. If the global
        settings tab is active, fall back to Cura's own default extruder position.
        If Cura has no valid default/selected extruder, return None rather than
        silently substituting extruder 0.
        """
        extruder_manager = ExtruderManager.getInstance()
        if extruder_manager is not None:
            active_stack = extruder_manager.getActiveExtruderStack()
            if active_stack is not None:
                return active_stack

        global_stack = self._application.getGlobalContainerStack()
        if global_stack is None:
            return None

        try:
            default_position = str(self._application.getMachineManager().defaultExtruderPosition)
        except (AttributeError, TypeError, ValueError):
            return None

        for extruder in global_stack.extruderList:
            if str(extruder.getMetaDataEntry("position", "")) == default_position:
                return extruder
        return None

    def _resolve_material_identity(
        self,
        material: Any,
        material_name: str,
    ) -> Tuple[str, Dict[str, Any]]:
        """Resolve a stable Cura material identity and collect diagnostics."""
        diagnostic: Dict[str, Any] = {
            "class": type(material).__name__ if material is not None else "None",
            "name": material_name,
            "getId": "",
            "id_attr": "",
            "metadata": {},
            "registry_matches": [],
            "resolved_from": "",
        }

        if material is None:
            return "", diagnostic

        try:
            diagnostic["getId"] = str(material.getId() or "").strip()
        except Exception as error:
            diagnostic["getId_error"] = repr(error)

        try:
            diagnostic["id_attr"] = str(getattr(material, "id", "") or "").strip()
        except Exception as error:
            diagnostic["id_attr_error"] = repr(error)

        metadata: Dict[str, Any] = {}
        try:
            raw_metadata = material.getMetaData()
            if isinstance(raw_metadata, dict):
                metadata = dict(raw_metadata)
        except Exception as error:
            diagnostic["metadata_error"] = repr(error)

        interesting_keys = (
            "id",
            "GUID",
            "guid",
            "base_file",
            "type",
            "name",
            "brand",
            "material",
            "color_name",
            "definition",
            "variant",
            "variant_name",
        )

        diagnostic["metadata"] = {
            key: metadata.get(key)
            for key in interesting_keys
            if key in metadata
        }

        # Prefer Cura's root logical material identity, then direct container ID.
        direct_candidates = [
            ("metadata.GUID", metadata.get("GUID")),
            ("metadata.guid", metadata.get("guid")),
            ("metadata.base_file", metadata.get("base_file")),
            ("getId()", diagnostic["getId"]),
            ("id", diagnostic["id_attr"]),
            ("metadata.id", metadata.get("id")),
        ]

        for source, value in direct_candidates:
            value = str(value or "").strip()
            if value and value not in ("empty_material", "empty"):
                diagnostic["resolved_from"] = source
                return value, diagnostic

        # Last-resort registry lookup by visible material name. Multiple derived
        # containers are okay only if they collapse to one GUID/base_file.
        try:
            registry = CuraContainerRegistry.getInstance()
            matches = registry.findInstanceContainersMetadata(
                type="material",
                name=material_name,
            )

            logical_roots: Dict[str, Dict[str, Any]] = {}

            for match in matches:
                if not isinstance(match, dict):
                    continue

                compact = {
                    key: match.get(key)
                    for key in interesting_keys
                    if key in match
                }
                diagnostic["registry_matches"].append(compact)

                guid = str(match.get("GUID") or match.get("guid") or "").strip()
                base_file = str(match.get("base_file") or "").strip()
                container_id = str(match.get("id") or "").strip()
                logical_id = guid or base_file or container_id

                if logical_id:
                    logical_roots[logical_id] = compact

            diagnostic["registry_logical_roots"] = list(logical_roots.keys())

            if len(logical_roots) == 1:
                logical_id = next(iter(logical_roots.keys()))
                diagnostic["resolved_from"] = "registry unique logical material"
                return logical_id, diagnostic

        except Exception as error:
            diagnostic["registry_error"] = repr(error)

        return "", diagnostic

    @pyqtSlot(result=str)
    def inspectActiveMaterial(self) -> str:
        """Expose the actual active material identity Cura gives the plugin."""
        try:
            active_extruder = self._get_active_extruder_stack()
            if active_extruder is None:
                result = "MATERIAL DEBUG: no active extruder stack"
            else:
                material = active_extruder.material
                if material is None:
                    result = "MATERIAL DEBUG: active extruder has no material"
                else:
                    material_name = str(material.getName() or "")
                    resolved, diagnostic = self._resolve_material_identity(
                        material,
                        material_name,
                    )
                    metadata = diagnostic.get("metadata", {})
                    roots = diagnostic.get("registry_logical_roots", [])

                    result = (
                        "MATERIAL DEBUG | class={cls} | name={name!r} | "
                        "getId={gid!r} | id={ida!r} | "
                        "GUID={guid!r} | base_file={base!r} | "
                        "metadata.id={mid!r} | registry_matches={mc} | "
                        "roots={roots!r} | resolved={resolved!r} via {source}"
                    ).format(
                        cls=diagnostic.get("class", ""),
                        name=material_name,
                        gid=diagnostic.get("getId", ""),
                        ida=diagnostic.get("id_attr", ""),
                        guid=metadata.get("GUID", metadata.get("guid", "")),
                        base=metadata.get("base_file", ""),
                        mid=metadata.get("id", ""),
                        mc=len(diagnostic.get("registry_matches", [])),
                        roots=roots,
                        resolved=resolved,
                        source=diagnostic.get("resolved_from", ""),
                    )

            self._status = result
            self.stateChanged.emit()
            return result

        except Exception as error:
            result = "MATERIAL DEBUG FAILED: {!r}".format(error)
            self._status = result
            Logger.logException("e", "Eventide material diagnostic failed")
            self.stateChanged.emit()
            return result

    @pyqtSlot()
    def refreshSelection(self) -> None:
        printer_name = "No active printer"
        printer_id = ""
        material_name = "No active material"
        material_id = ""
        position = 0
        nozzle: Optional[float] = None

        try:
            global_stack = self._application.getGlobalContainerStack()

            if global_stack is not None:
                printer_name = str(global_stack.getName() or "No active printer")
                printer_id = str(global_stack.getId() or "").strip()

            active_extruder = self._get_active_extruder_stack()

            if active_extruder is not None:
                position, nozzle = self._active_toolhead()
                material = active_extruder.material

                if material is not None:
                    material_name = str(material.getName() or "No active material")
                    material_id, _ = self._resolve_material_identity(
                        material,
                        material_name,
                    )

        except Exception:
            Logger.logException(
                "e",
                "Eventide Shared Profiles selection refresh failed",
            )

        nozzle_display = self._display_number(nozzle)
        bound_material = (
            self._bound_nozzle_material(printer_id, position, nozzle)
            if printer_id
            else ""
        )

        changed = (
            printer_name != self._active_printer_name
            or printer_id != self._active_printer_id
            or material_name != self._active_material_name
            or material_id != self._active_material_id
            or position != self._active_extruder_position
            or nozzle_display != self._active_nozzle_diameter
            or bound_material != self._active_nozzle_material
        )

        self._active_printer_name = printer_name
        self._active_printer_id = printer_id
        self._active_material_name = material_name
        self._active_material_id = material_id
        self._active_extruder_position = position
        self._active_nozzle_diameter = nozzle_display
        self._active_nozzle_material = bound_material

        if changed:
            self._clear_capability_editor()
            self._refresh_library_state_internal()
            self.stateChanged.emit()

    def _refresh_library_state_internal(self, require_manifest: bool = False) -> None:
        self._printer_count = 0
        self._filament_count = 0
        self._capability_count = 0
        self._quality_count = 0
        self._current_registration = "Not registered"
        if not self._shared_library_path:
            self._current_registration = "No library path"
            return
        root = os.path.normpath(self._shared_library_path)
        if not os.path.isdir(root):
            self._current_registration = "Library path unavailable"
            return
        if require_manifest:
            self._require_initialized_library(root)
        elif not os.path.isfile(self._manifest_path(root)):
            self._current_registration = "Library not initialized"
            return

        self._printer_count = self._count_json_files(os.path.join(root, "printers"))
        self._filament_count = self._count_json_files(os.path.join(root, "filaments"))
        self._capability_count = self._count_json_files(os.path.join(root, "capabilities"))
        self._quality_count = 0
        quality_dir = os.path.join(root, "quality")
        try:
            for filename in os.listdir(quality_dir):
                if not filename.lower().endswith(".json"):
                    continue
                try:
                    record = self._read_json(os.path.join(quality_dir, filename))
                    if not self._quality_is_deleted(record):
                        self._quality_count += 1
                except Exception:
                    # Validation reports malformed/newer records separately.
                    continue
        except OSError:
            pass

        if not self._active_printer_id or not self._active_material_id:
            self._current_registration = "Current Cura selection incomplete"
            return
        printer_record_id, filament_record_id = self._base_record_ids()
        printer_exists = os.path.isfile(
            os.path.join(root, "printers", printer_record_id + ".json")
        )
        filament_exists = os.path.isfile(
            os.path.join(root, "filaments", filament_record_id + ".json")
        )
        capability_exists = len(self._capability_candidates(root)) > 0
        if printer_exists and filament_exists and capability_exists:
            self._current_registration = "Registered"
        elif printer_exists or filament_exists or capability_exists:
            self._current_registration = "Partially registered"
        else:
            self._current_registration = "Not registered"

    @pyqtProperty(str, notify=stateChanged)
    def sharedLibraryPath(self) -> str:
        return self._shared_library_path

    @pyqtProperty(str, notify=stateChanged)
    def status(self) -> str:
        return self._status

    @pyqtProperty(str, notify=stateChanged)
    def activePrinterName(self) -> str:
        return self._active_printer_name

    @pyqtProperty(str, notify=stateChanged)
    def activePrinterId(self) -> str:
        return self._active_printer_id

    @pyqtProperty(str, notify=stateChanged)
    def activeMaterialName(self) -> str:
        return self._active_material_name

    @pyqtProperty(str, notify=stateChanged)
    def activeMaterialId(self) -> str:
        return self._active_material_id

    @pyqtProperty(int, notify=stateChanged)
    def printerCount(self) -> int:
        return self._printer_count

    @pyqtProperty(int, notify=stateChanged)
    def filamentCount(self) -> int:
        return self._filament_count

    @pyqtProperty(int, notify=stateChanged)
    def capabilityCount(self) -> int:
        return self._capability_count

    @pyqtProperty(int, notify=stateChanged)
    def qualityCount(self) -> int:
        return self._quality_count

    @pyqtProperty(str, notify=stateChanged)
    def currentRegistration(self) -> str:
        return self._current_registration


    @pyqtProperty(bool, notify=stateChanged)
    def capabilityLoaded(self) -> bool:
        return self._capability_loaded

    @pyqtProperty(str, notify=stateChanged)
    def capabilityRecordId(self) -> str:
        return self._capability_record_id

    @pyqtProperty(int, notify=stateChanged)
    def capabilityRevision(self) -> int:
        return self._capability_revision

    @pyqtProperty(str, notify=stateChanged)
    def capabilityMaxVolumetricFlow(self) -> str:
        return self._capability_max_volumetric_flow

    @pyqtProperty(str, notify=stateChanged)
    def capabilityMaxLinearSpeed(self) -> str:
        return self._capability_max_linear_speed

    @pyqtProperty(str, notify=stateChanged)
    def capabilityPressureAdvance(self) -> str:
        return self._capability_pressure_advance

    @pyqtProperty(bool, notify=stateChanged)
    def capabilityEmitKlipperPA(self) -> bool:
        return self._capability_emit_klipper_pa

    @pyqtProperty(str, notify=stateChanged)
    def capabilityFlowPercent(self) -> str:
        return self._capability_flow_percent

    @pyqtProperty(str, notify=stateChanged)
    def capabilityTemperatureOffset(self) -> str:
        return self._capability_temperature_offset

    @pyqtProperty(str, notify=stateChanged)
    def capabilityRetractionDistance(self) -> str:
        return self._capability_retraction_distance

    @pyqtProperty(str, notify=stateChanged)
    def capabilityRetractionSpeed(self) -> str:
        return self._capability_retraction_speed

    @pyqtProperty(str, notify=stateChanged)
    def capabilityNozzleDiameter(self) -> str:
        return self._capability_nozzle_diameter

    @pyqtProperty(str, notify=stateChanged)
    def capabilityNozzleMaterial(self) -> str:
        return self._capability_nozzle_material

    @pyqtProperty(str, notify=stateChanged)
    def capabilityNotes(self) -> str:
        return self._capability_notes

    @pyqtProperty(str, notify=stateChanged)
    def capabilityLastCalibrated(self) -> str:
        return self._capability_last_calibrated


    @pyqtProperty(str, notify=stateChanged)
    def capabilityCalibrationStatus(self) -> str:
        return self._capability_calibration_status

    @pyqtProperty(int, notify=stateChanged)
    def activeExtruderPosition(self) -> int:
        return self._active_extruder_position

    @pyqtProperty(str, notify=stateChanged)
    def activeNozzleDiameter(self) -> str:
        return self._active_nozzle_diameter

    @pyqtProperty(str, notify=stateChanged)
    def activeNozzleMaterial(self) -> str:
        return self._active_nozzle_material

    @pyqtProperty(bool, notify=stateChanged)
    def sliceHookInstalled(self) -> bool:
        return self._slice_hook_installed

    @pyqtProperty(str, notify=stateChanged)
    def lastSliceResolution(self) -> str:
        return self._last_slice_resolution


    @pyqtProperty(bool, notify=stateChanged)
    def sliceHookActive(self) -> bool:
        return self._slice_hook_installed

    @pyqtProperty(str, notify=stateChanged)
    def lastLibraryEvent(self) -> str:
        return self._last_library_event

    @pyqtProperty(str, notify=stateChanged)
    def libraryValidationSummary(self) -> str:
        return self._library_validation_summary

    @pyqtProperty(str, notify=stateChanged)
    def lastSyncSummary(self) -> str:
        return self._last_sync_summary

    @pyqtProperty(str, notify=stateChanged)
    def pluginVersion(self) -> str:
        return self.PLUGIN_VERSION

    @pyqtProperty(int, notify=stateChanged)
    def qualityConflictCount(self) -> int:
        return len(self._quality_conflicts)

    @pyqtProperty(str, notify=stateChanged)
    def qualityConflictKind(self) -> str:
        conflict = self._current_quality_conflict()
        return str(conflict.get("kind", "edit") or "edit") if conflict else ""

    @pyqtProperty(str, notify=stateChanged)
    def qualityConflictName(self) -> str:
        conflict = self._current_quality_conflict()
        return str(conflict.get("name", "") or "") if conflict else ""

    @pyqtProperty(str, notify=stateChanged)
    def qualityConflictPosition(self) -> str:
        if not self._quality_conflicts:
            return ""
        self._current_quality_conflict()
        return "{} of {}".format(self._quality_conflict_index + 1, len(self._quality_conflict_order))

    @pyqtProperty(str, notify=stateChanged)
    def qualityConflictDetails(self) -> str:
        conflict = self._current_quality_conflict()
        if not conflict:
            return ""
        kind = str(conflict.get("kind", "edit") or "edit")
        revision = int(conflict.get("remote_revision", 0) or 0)
        host = str(conflict.get("remote_hostname", "") or "another computer")
        if kind == "deletion":
            return "The shared profile was deleted at revision {} by {}, but this PC has an independent edit. Accept the deletion, preserve this edit as a new profile, or deliberately restore it as the shared profile.".format(revision, host)
        if kind == "local_delete_remote_edit":
            return "This PC deleted the profile, but revision {} from {} contains a newer shared edit. Review the conflict before either deleting that newer edit or restoring it on this PC.".format(revision, host)
        return "This PC and the shared library both changed this profile. Shared revision {} was last written by {}. Choose which version to keep, or preserve this PC's edit as a new profile.".format(revision, host)

    @pyqtProperty(str, notify=stateChanged)
    def qualityConflictSuggestedCopyName(self) -> str:
        conflict = self._current_quality_conflict()
        if not conflict:
            return ""
        suffix = "Preserved" if str(conflict.get("kind", "edit") or "edit") == "deletion" else "Conflict copy"
        return "{} ({} - {})".format(
            str(conflict.get("name", "") or "Shared Profile"),
            suffix,
            socket.gethostname(),
        )

    @pyqtProperty(str, notify=stateChanged)
    def lastQualitySyncSummary(self) -> str:
        return self._last_quality_sync_summary
