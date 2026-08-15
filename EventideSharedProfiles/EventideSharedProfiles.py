import hashlib
import json
import os
import re
import socket
import sys
import math
import platform
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, cast

from PyQt6.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot

from UM.Extension import Extension
from UM.Logger import Logger
from cura.CuraApplication import CuraApplication
from cura.CuraVersion import CuraVersion
from cura.Settings.CuraContainerRegistry import CuraContainerRegistry


class EventideSharedProfiles(QObject, Extension):
    """Shared profile library extension for Cura."""

    stateChanged = pyqtSignal()

    CONFIG_FILENAME = "eventide_shared_profiles.json"
    PLUGIN_VERSION = "0.7.1"
    LIBRARY_FORMAT = 1
    RECORD_FORMAT = 1
    LIBRARY_POLL_INTERVAL_MS = 3000

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
        self._capability_flow_percent = ""
        self._capability_temperature_offset = ""
        self._capability_retraction_distance = ""
        self._capability_retraction_speed = ""
        self._capability_nozzle_diameter = ""
        self._capability_nozzle_material = ""
        self._capability_notes = ""
        self._capability_last_calibrated = ""
        self._capability_calibration_status = "uncalibrated"
        self._capability_emit_klipper_pa = False

        # Active Cura toolhead identity.
        self._active_extruder_position = 0
        self._active_nozzle_diameter = ""
        self._active_nozzle_material = ""

        # Transient slice-time integration state. Eventide never writes these
        # capability values into Cura's persistent userChanges container.
        self._slice_hook_installed = False
        self._slice_capability_snapshots: Dict[int, Dict[str, Any]] = {}
        self._last_slice_resolution = "Slice-time hook not checked yet."
        self._last_gcode_guardrail_summary = "No Eventide G-code guardrail run yet."
        self._toolhead_bindings: Dict[str, str] = {}
        self._library_manifest_signature: Optional[Tuple[int, int]] = None
        self._last_library_event = "Library watcher not started yet."
        self._library_validation_summary = "Library not validated yet."
        self._last_sync_summary = "Library has not been synchronized to this Cura install yet."
        self._machine_bindings: Dict[str, str] = {}

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
            raw_bindings = data.get("toolhead_bindings", {})
            if isinstance(raw_bindings, dict):
                self._toolhead_bindings = {
                    str(key): str(value).strip()
                    for key, value in raw_bindings.items()
                    if str(key).strip() and str(value).strip()
                }
            raw_machine_bindings = data.get("machine_bindings", {})
            if isinstance(raw_machine_bindings, dict):
                self._machine_bindings = {
                    str(key): str(value).strip()
                    for key, value in raw_machine_bindings.items()
                    if str(key).strip() and str(value).strip()
                }

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
                "format": 4,
                "shared_library_path": self._shared_library_path,
                "client_id": self._client_id,
                "toolhead_bindings": self._toolhead_bindings,
                "machine_bindings": self._machine_bindings,
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
            raise ValueError("unsupported library format {}".format(manifest.get("format")))
        return manifest

    def _save_library_path_from_ui(self, requested_path: str) -> str:
        root = self._library_root(requested_path)
        self._shared_library_path = str(requested_path or "").strip()
        self._save_config()
        return root

    def _identity_upsert(self, path: str, schema: str, record_id: str,
                         cura_id: str, cura_name: str) -> Tuple[Dict[str, Any], bool]:
        now = self._utc_now()
        writer = self._writer_info()
        if os.path.isfile(path):
            record = self._read_json(path)
            if record.get("schema") != schema:
                raise ValueError("record schema mismatch in {}".format(os.path.basename(path)))
            if record.get("id") != record_id:
                raise ValueError("record id mismatch in {}".format(os.path.basename(path)))
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
        """Return a JSON-safe representation without evaluating arbitrary objects."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [EventideSharedProfiles._json_safe_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): EventideSharedProfiles._json_safe_value(item)
                for key, item in value.items()
            }
        return str(value)

    def _instance_values(self, container: Any) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        if container is None:
            return values
        try:
            keys = container.getAllKeys()
        except Exception:
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
        except Exception:
            pass
        root = material
        if base_file:
            try:
                roots = registry.findInstanceContainers(id=base_file)
                if roots:
                    root = roots[0]
            except Exception:
                pass
        if not guid:
            try:
                guid = str(root.getMetaDataEntry("GUID", "") or "").strip()
            except Exception:
                pass

        root_id = str(root.getId() or "").strip()
        readonly = False
        try:
            readonly = bool(registry.isReadOnly(root_id))
        except Exception:
            pass

        metadata: Dict[str, Any] = {}
        try:
            raw_metadata = root.getMetaData()
            if isinstance(raw_metadata, dict):
                for key in ("GUID", "base_file", "name", "brand", "material", "color_name", "color_code", "diameter", "setting_version"):
                    if key in raw_metadata:
                        metadata[key] = self._json_safe_value(raw_metadata.get(key))
        except Exception:
            pass

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
            except Exception:
                pass
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

    def _base_record_ids(self) -> Tuple[str, str]:
        if not self._active_printer_id:
            raise ValueError("Cura does not have an active printer id")
        if not self._active_material_id:
            raise ValueError("Cura does not have an active material id")

        return (
            self._stable_id("printer", self._active_printer_id),
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
        self._capability_flow_percent = ""
        self._capability_temperature_offset = ""
        self._capability_retraction_distance = ""
        self._capability_retraction_speed = ""
        self._capability_nozzle_diameter = ""
        self._capability_nozzle_material = ""
        self._capability_notes = ""
        self._capability_last_calibrated = ""
        self._capability_calibration_status = "uncalibrated"
        self._capability_emit_klipper_pa = False

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
        self._capability_emit_klipper_pa = bool(
            tuning.get("emit_klipper_pressure_advance", False)
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
                "flow_percent": None,
                "emit_klipper_pressure_advance": False,
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

        printer_record_id = self._stable_id("printer", printer_id)
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
            self._last_slice_resolution = "Slice started; resolving Eventide capabilities per extruder."
            return original_values

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

    @staticmethod
    def _replace_or_add_feedrate(line: str, feedrate_mm_min: float) -> str:
        newline = "\n" if line.endswith("\n") else ""
        body = line[:-1] if newline else line
        formatted = "F{:.3f}".format(feedrate_mm_min).rstrip("0").rstrip(".")

        code, sep, comment = body.partition(";")
        if re.search(r"(?i)(?:^|\s)F[-+]?\d*\.?\d+", code):
            code = re.sub(
                r"(?i)(?<=\s)F[-+]?\d*\.?\d+",
                formatted,
                code,
                count=1,
            )
            if re.match(r"(?i)^F[-+]?\d*\.?\d+", code):
                code = re.sub(
                    r"(?i)^F[-+]?\d*\.?\d+",
                    formatted,
                    code,
                    count=1,
                )
        else:
            code = code.rstrip() + " " + formatted

        return code + ((";" + comment) if sep else "") + newline

    def _enforce_gcode_limits(
        self,
        gcode_list: list,
        snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        record = snapshot.get("record", {})
        limits = record.get("limits", {})
        max_linear = limits.get("max_linear_speed_mm_s")
        max_flow = limits.get("max_volumetric_flow_mm3_s")
        max_linear = float(max_linear) if max_linear not in (None, "") else None
        max_flow = float(max_flow) if max_flow not in (None, "") else None

        stats = {
            "clamped_moves": 0,
            "max_extrusion_speed_before_mm_s": 0.0,
            "max_extrusion_speed_after_mm_s": 0.0,
            "max_flow_before_mm3_s": 0.0,
            "max_flow_after_mm3_s": 0.0,
        }
        if max_linear is None and max_flow is None:
            return stats

        filament_diameter = float(snapshot.get("filament_diameter_mm", 1.75) or 1.75)
        filament_area = math.pi * (filament_diameter * 0.5) ** 2

        xyz_absolute = True
        e_absolute = True
        current = {"X": None, "Y": None, "Z": None}
        current_e: Optional[float] = None
        modal_feedrate: Optional[float] = None

        parameter_re = re.compile(r"(?i)([XYZEF])([-+]?\d*\.?\d+)")
        command_re = re.compile(r"^\s*([GMT])(\d+)", re.IGNORECASE)

        for chunk_index, chunk in enumerate(gcode_list):
            lines = chunk.splitlines(keepends=True)
            output_lines = []

            for original_line in lines:
                line = original_line
                code_part = line.split(";", 1)[0].strip()
                match = command_re.match(code_part)
                if not match:
                    output_lines.append(line)
                    continue

                letter = match.group(1).upper()
                number = int(match.group(2))

                if letter == "G" and number == 90:
                    xyz_absolute = True
                    output_lines.append(line)
                    continue
                if letter == "G" and number == 91:
                    xyz_absolute = False
                    output_lines.append(line)
                    continue
                if letter == "M" and number == 82:
                    e_absolute = True
                    output_lines.append(line)
                    continue
                if letter == "M" and number == 83:
                    e_absolute = False
                    output_lines.append(line)
                    continue

                params = {
                    key.upper(): float(value)
                    for key, value in parameter_re.findall(code_part)
                }

                if letter == "G" and number == 92:
                    for axis in ("X", "Y", "Z"):
                        if axis in params:
                            current[axis] = params[axis]
                    if "E" in params:
                        current_e = params["E"]
                    output_lines.append(line)
                    continue

                if letter != "G" or number not in (0, 1):
                    output_lines.append(line)
                    continue

                explicit_feedrate = params.get("F")
                if explicit_feedrate is not None:
                    modal_feedrate = explicit_feedrate

                target = dict(current)
                for axis in ("X", "Y", "Z"):
                    if axis not in params:
                        continue
                    if xyz_absolute:
                        target[axis] = params[axis]
                    elif current[axis] is not None:
                        target[axis] = current[axis] + params[axis]
                    else:
                        target[axis] = None

                move_components = []
                for axis in ("X", "Y", "Z"):
                    if current[axis] is not None and target[axis] is not None:
                        move_components.append(target[axis] - current[axis])
                move_length = (
                    math.sqrt(sum(component * component for component in move_components))
                    if move_components
                    else 0.0
                )

                extrusion_delta: Optional[float] = None
                target_e = current_e
                if "E" in params:
                    if e_absolute:
                        target_e = params["E"]
                        if current_e is not None:
                            extrusion_delta = target_e - current_e
                    else:
                        extrusion_delta = params["E"]
                        target_e = (current_e or 0.0) + params["E"]

                if (
                    extrusion_delta is not None
                    and extrusion_delta > 0.0
                    and move_length > 0.0
                    and modal_feedrate is not None
                    and modal_feedrate > 0.0
                ):
                    original_speed = modal_feedrate / 60.0
                    allowed_speed = original_speed

                    if max_linear is not None:
                        allowed_speed = min(allowed_speed, max_linear)

                    original_flow = (
                        filament_area
                        * extrusion_delta
                        * original_speed
                        / move_length
                    )
                    stats["max_extrusion_speed_before_mm_s"] = max(
                        stats["max_extrusion_speed_before_mm_s"],
                        original_speed,
                    )
                    stats["max_flow_before_mm3_s"] = max(
                        stats["max_flow_before_mm3_s"],
                        original_flow,
                    )

                    if max_flow is not None and filament_area * extrusion_delta > 0:
                        flow_limited_speed = (
                            max_flow * move_length / (filament_area * extrusion_delta)
                        )
                        allowed_speed = min(allowed_speed, flow_limited_speed)

                    if allowed_speed < original_speed - 0.000001:
                        line = self._replace_or_add_feedrate(
                            line,
                            allowed_speed * 60.0,
                        )
                        modal_feedrate = allowed_speed * 60.0
                        stats["clamped_moves"] += 1

                    after_speed = min(original_speed, allowed_speed)
                    after_flow = (
                        filament_area
                        * extrusion_delta
                        * after_speed
                        / move_length
                    )
                    stats["max_extrusion_speed_after_mm_s"] = max(
                        stats["max_extrusion_speed_after_mm_s"],
                        after_speed,
                    )
                    stats["max_flow_after_mm3_s"] = max(
                        stats["max_flow_after_mm3_s"],
                        after_flow,
                    )

                current = target
                if "E" in params:
                    current_e = target_e
                output_lines.append(line)

            gcode_list[chunk_index] = "".join(output_lines)

        return stats

    def _gcode_capability_header(
        self,
        snapshot: Dict[str, Any],
        stats: Dict[str, Any],
    ) -> str:
        record = snapshot.get("record", {})
        limits = record.get("limits", {})
        tuning = record.get("tuning", {})
        hotend = record.get("hotend", {})

        lines = [
            ";EVENTIDE_SHARED_PROFILES=1",
            ";EVENTIDE_SLICE_MODE=TRANSIENT",
            ";EVENTIDE_GCODE_GUARDRAIL=1",
            ";EVENTIDE_CAPABILITY_ID={}".format(record.get("id", "")),
            ";EVENTIDE_PRINTER={}".format(snapshot.get("printer_name", "")),
            ";EVENTIDE_MATERIAL={}".format(snapshot.get("material_name", "")),
            ";EVENTIDE_EXTRUDER={}".format(snapshot.get("extruder", 0)),
            ";EVENTIDE_NOZZLE_DIAMETER_MM={}".format(
                hotend.get("nozzle_diameter_mm", "")
            ),
            ";EVENTIDE_NOZZLE_MATERIAL={}".format(
                hotend.get("nozzle_material", "")
            ),
            ";EVENTIDE_MAX_VOLUMETRIC_FLOW_MM3_S={}".format(
                limits.get("max_volumetric_flow_mm3_s", "")
            ),
            ";EVENTIDE_MAX_LINEAR_SPEED_MM_S={}".format(
                limits.get("max_linear_speed_mm_s", "")
            ),
            ";EVENTIDE_PRESSURE_ADVANCE={}".format(
                tuning.get("pressure_advance", "")
            ),
            ";EVENTIDE_FLOW_PERCENT={}".format(
                tuning.get("flow_percent", "")
            ),
            ";EVENTIDE_CALIBRATION_STATUS={}".format(
                record.get("calibration", {}).get("status", "uncalibrated")
            ),
            ";EVENTIDE_TEMPERATURE_OFFSET_C={}".format(
                tuning.get("temperature_offset_c", "")
            ),
            ";EVENTIDE_RETRACTION_DISTANCE_MM={}".format(
                tuning.get("retraction_distance_mm", "")
            ),
            ";EVENTIDE_RETRACTION_SPEED_MM_S={}".format(
                tuning.get("retraction_speed_mm_s", "")
            ),
            ";EVENTIDE_GCODE_CLAMPED_MOVES={}".format(
                stats.get("clamped_moves", 0)
            ),
            ";EVENTIDE_GCODE_MAX_SPEED_BEFORE_MM_S={:.5f}".format(
                stats.get("max_extrusion_speed_before_mm_s", 0.0)
            ),
            ";EVENTIDE_GCODE_MAX_SPEED_AFTER_MM_S={:.5f}".format(
                stats.get("max_extrusion_speed_after_mm_s", 0.0)
            ),
            ";EVENTIDE_GCODE_MAX_FLOW_BEFORE_MM3_S={:.5f}".format(
                stats.get("max_flow_before_mm3_s", 0.0)
            ),
            ";EVENTIDE_GCODE_MAX_FLOW_AFTER_MM3_S={:.5f}".format(
                stats.get("max_flow_after_mm3_s", 0.0)
            ),
        ]

        pressure_advance = tuning.get("pressure_advance")
        if (
            bool(tuning.get("emit_klipper_pressure_advance", False))
            and pressure_advance not in (None, "")
        ):
            lines.append(
                "SET_PRESSURE_ADVANCE ADVANCE={:g}".format(
                    float(pressure_advance)
                )
            )

        return "\n".join(lines) + "\n"

    def _stamp_gcode(self, output_device: Any) -> None:
        """Guardrail and stamp G-code using only the capability resolved for this slice."""
        try:
            if not self._slice_capability_snapshots:
                return

            scene = self._application.getController().getScene()
            if not hasattr(scene, "gcode_dict"):
                return
            gcode_dict = getattr(scene, "gcode_dict")
            if not gcode_dict:
                return

            active_plate = self._application.getMultiBuildPlateModel().activeBuildPlate
            gcode_list = gcode_dict.get(active_plate)
            if not gcode_list:
                return

            # G-code-level hard limiting currently tracks one active tool. Do not
            # guess on a multi-extruder slice: per-extruder transient CuraEngine
            # settings are already applied, but the final guardrail/PA stamping is
            # skipped until tool-change-aware G-code enforcement is implemented.
            if len(self._slice_capability_snapshots) != 1:
                self._last_gcode_guardrail_summary = (
                    "MULTI-EXTRUDER: final Eventide G-code guardrail/PA stamp skipped; "
                    "transient CuraEngine settings only."
                )
                Logger.log("w", "Eventide %s", self._last_gcode_guardrail_summary)
                self.stateChanged.emit()
                return

            position = sorted(self._slice_capability_snapshots.keys())[0]
            snapshot = self._slice_capability_snapshots[position]
            record = snapshot.get("record", {})
            marker = ";EVENTIDE_CAPABILITY_ID={}".format(record.get("id", ""))
            if marker in gcode_list[0]:
                return

            stats = self._enforce_gcode_limits(gcode_list, snapshot)
            gcode_list[0] += self._gcode_capability_header(snapshot, stats)
            gcode_dict[active_plate] = gcode_list
            setattr(scene, "gcode_dict", gcode_dict)

            self._last_gcode_guardrail_summary = (
                "G-code guardrail: {} move(s) clamped; max speed {:.3f} -> {:.3f} mm/s; "
                "max flow {:.3f} -> {:.3f} mm³/s"
            ).format(
                stats.get("clamped_moves", 0),
                stats.get("max_extrusion_speed_before_mm_s", 0.0),
                stats.get("max_extrusion_speed_after_mm_s", 0.0),
                stats.get("max_flow_before_mm3_s", 0.0),
                stats.get("max_flow_after_mm3_s", 0.0),
            )
            Logger.log("i", "Eventide %s", self._last_gcode_guardrail_summary)

        except Exception:
            Logger.logException("e", "Eventide G-code guardrail/stamping failed")

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
            self._last_library_event = "Library watcher active ({} s).".format(
                int(self.LIBRARY_POLL_INTERVAL_MS / 1000)
            )

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

    def _ensure_runtime_hooks(self) -> None:
        """Connect Cura-state signals only after Cura has finished startup."""
        if self._runtime_hooks_connected:
            self._install_slice_settings_hook()
            return

        self._application.globalContainerStackChanged.connect(self.refreshSelection)
        self._application.getOutputDeviceManager().writeStarted.connect(
            self._stamp_gcode
        )
        self._install_slice_settings_hook()
        self._runtime_hooks_connected = True

    def _get_manifest_signature(self) -> Optional[Tuple[int, int]]:
        if not self._shared_library_path:
            return None
        path = self._manifest_path(os.path.normpath(self._shared_library_path))
        try:
            stat = os.stat(path)
            return int(stat.st_mtime_ns), int(stat.st_size)
        except OSError:
            return None

    def _poll_shared_library(self) -> None:
        signature = self._get_manifest_signature()
        if signature == self._library_manifest_signature:
            return
        self._library_manifest_signature = signature
        try:
            self._refresh_library_state_internal(require_manifest=False)
            self._last_library_event = "Shared library changed on disk; inventory refreshed at {}.".format(
                self._utc_now()
            )
            self.stateChanged.emit()
        except Exception as error:
            self._last_library_event = "Library watcher error: {}".format(error)
            Logger.logException("e", "Eventide shared library polling failed")
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

        sync_dir = os.path.join(os.path.dirname(self._config_path), "sync-temp")
        os.makedirs(sync_dir, exist_ok=True)
        filename = self._normalize_toolhead_text(record.get("id", "material")) + ".xml.fdm_material"
        temp_path = os.path.join(sync_dir, filename)
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                handle.write(serialized)
            result = manager.importMaterialContainer(temp_path)
            if not isinstance(result, dict) or result.get("status") != "success":
                message = result.get("message", "unknown import error") if isinstance(result, dict) else str(result)
                raise RuntimeError("Cura material import failed: {}".format(message))
        finally:
            try:
                if os.path.isfile(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

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

    @pyqtSlot(str, result=str)
    def syncLibraryToCura(self, requested_path: str) -> str:
        """Install missing published materials and machine instances into this Cura profile."""
        material_stats = {"installed": 0, "existing": 0, "unpublished": 0, "builtin-missing": 0, "failed": 0}
        machine_stats = {"installed": 0, "existing": 0, "unpublished": 0, "missing-definition": 0, "failed": 0}
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

            self._refresh_library_state_internal(require_manifest=True)
            self.refreshSelection()
            self._last_sync_summary = (
                "SYNC COMPLETE: materials {} installed / {} already local / {} not yet published; "
                "machines {} installed / {} already local / {} not yet published / {} missing base definition; "
                "{} failure(s)."
            ).format(
                material_stats.get("installed", 0), material_stats.get("existing", 0), material_stats.get("unpublished", 0),
                machine_stats.get("installed", 0), machine_stats.get("existing", 0), machine_stats.get("unpublished", 0),
                machine_stats.get("missing-definition", 0), len(failures),
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
                    if record.get("schema") != schema:
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

            for name, record in records_by_folder["capabilities"]:
                if record.get("printer_id") not in ids_by_folder["printers"]:
                    errors.append("{}: missing printer reference".format(name))
                if record.get("filament_id") not in ids_by_folder["filaments"]:
                    errors.append("{}: missing filament reference".format(name))
                hotend = record.get("hotend", {})
                if hotend.get("nozzle_diameter_mm") in (None, ""):
                    warnings.append("{}: nozzle diameter unset".format(name))
                tuning = record.get("tuning", {})
                if tuning.get("emit_klipper_pressure_advance") and tuning.get("pressure_advance") in (None, ""):
                    warnings.append("{}: Klipper PA emit enabled but PA is unset".format(name))

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
            output_dir = os.path.dirname(self._config_path)
            os.makedirs(output_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = os.path.join(
                output_dir, "eventide-diagnostics-{}.txt".format(stamp)
            )
            lines = [
                "Eventide Shared Profiles diagnostics",
                "generated_utc={}".format(self._utc_now()),
                "plugin_version={}".format(self.PLUGIN_VERSION),
                "cura_version={}".format(CuraVersion),
                "python={}".format(sys.version.replace("\n", " ")),
                "platform={}".format(platform.platform()),
                "config_path={}".format(self._config_path),
                "shared_library_path={}".format(self._shared_library_path),
                "client_id={}".format(self._client_id),
                "hostname={}".format(socket.gethostname()),
                "slice_hook_active={}".format(self._slice_hook_installed),
                "last_slice_resolution={}".format(self._last_slice_resolution),
                "last_guardrail={}".format(self._last_gcode_guardrail_summary),
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
                if "record_format" not in manifest:
                    manifest["record_format"] = self.RECORD_FORMAT
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

            tuning["pressure_advance"] = self._optional_float(
                payload.get("pressure_advance"),
                "Pressure advance",
                minimum=0.0,
            )
            tuning["flow_percent"] = self._optional_float(
                payload.get("flow_percent"),
                "Material flow",
                strictly_positive=True,
            )
            tuning["emit_klipper_pressure_advance"] = bool(
                payload.get("emit_klipper_pressure_advance", False)
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
        """Return Cura's actual active extruder stack.

        Cura's own material management code uses MachineManager.activeStack.
        Prefer that instead of trying to infer the active extruder by walking
        GlobalStack.extruderList.
        """
        machine_manager = self._application.getMachineManager()
        active_stack = getattr(machine_manager, "activeStack", None)
        if active_stack is not None:
            return active_stack

        global_stack = self._application.getGlobalContainerStack()
        if global_stack is None:
            return None

        extruders = list(global_stack.extruderList)
        if not extruders:
            return None

        # Fallback only. isEnabled is a Qt property in Cura 5.13, not a method.
        for extruder in extruders:
            try:
                if bool(extruder.isEnabled):
                    return extruder
            except Exception:
                pass

        return extruders[0]

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
        self._quality_count = self._count_json_files(os.path.join(root, "quality"))

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

    @pyqtProperty(bool, notify=stateChanged)
    def capabilityEmitKlipperPA(self) -> bool:
        return self._capability_emit_klipper_pa

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
    def lastGcodeGuardrailSummary(self) -> str:
        return self._last_gcode_guardrail_summary

    @pyqtProperty(str, notify=stateChanged)
    def lastLibraryEvent(self) -> str:
        return self._last_library_event

    @pyqtProperty(str, notify=stateChanged)
    def libraryValidationSummary(self) -> str:
        return self._library_validation_summary

    @pyqtProperty(str, notify=stateChanged)
    def lastSyncSummary(self) -> str:
        return self._last_sync_summary
