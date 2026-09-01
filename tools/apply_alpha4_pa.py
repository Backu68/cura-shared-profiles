from pathlib import Path

PY_PATH = Path("EventideSharedProfiles/EventideSharedProfiles.py")
QML_PATH = Path("EventideSharedProfiles/qml/EventideSharedProfilesWindow.qml")
PLUGIN_PATH = Path("EventideSharedProfiles/plugin.json")
CHANGELOG_PATH = Path("EventideSharedProfiles/CHANGELOG.md")


def replace_exact(text: str, old: str, new: str, expected: int = 1, label: str = "replacement") -> str:
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{label}: expected {expected} occurrence(s), found {count}")
    return text.replace(old, new)


py = PY_PATH.read_text(encoding="utf-8")
qml = QML_PATH.read_text(encoding="utf-8")
plugin = PLUGIN_PATH.read_text(encoding="utf-8")
changelog = CHANGELOG_PATH.read_text(encoding="utf-8")

py = replace_exact(
    py,
    '    PLUGIN_VERSION = "0.9.0-alpha.3"\n    PUBLISHER_PLUGIN_VERSION = "0.9.0-alpha.3"',
    '    PLUGIN_VERSION = "0.9.0-alpha.4"\n    PUBLISHER_PLUGIN_VERSION = "0.9.0-alpha.4"',
    label="python version",
)

py = replace_exact(
    py,
    '        self._capability_max_linear_speed = ""\n        self._capability_flow_percent = ""',
    '        self._capability_max_linear_speed = ""\n        self._capability_pressure_advance = ""\n        self._capability_emit_klipper_pa = False\n        self._capability_flow_percent = ""',
    expected=2,
    label="capability editor state",
)

py = replace_exact(
    py,
    '''        self._capability_max_linear_speed = self._display_number(\n            limits.get("max_linear_speed_mm_s")\n        )\n        self._capability_flow_percent = self._display_number(\n            tuning.get("flow_percent")\n        )''',
    '''        self._capability_max_linear_speed = self._display_number(\n            limits.get("max_linear_speed_mm_s")\n        )\n        self._capability_pressure_advance = self._display_number(\n            tuning.get("pressure_advance")\n        )\n        self._capability_emit_klipper_pa = bool(\n            tuning.get("emit_klipper_pressure_advance", False)\n        )\n        self._capability_flow_percent = self._display_number(\n            tuning.get("flow_percent")\n        )''',
    label="load PA editor state",
)

py = replace_exact(
    py,
    '''            "tuning": {\n                "flow_percent": None,\n                "temperature_offset_c": None,''',
    '''            "tuning": {\n                "pressure_advance": None,\n                "emit_klipper_pressure_advance": False,\n                "flow_percent": None,\n                "temperature_offset_c": None,''',
    expected=2,
    label="new capability PA fields",
)

py = replace_exact(
    py,
    '''            # 0.9.0 no longer injects firmware commands into finished G-code.\n            # Drop obsolete beta-only PA emission fields when this record is saved.\n            tuning.pop("pressure_advance", None)\n            tuning.pop("emit_klipper_pressure_advance", None)\n            tuning["flow_percent"] = self._optional_float(''',
    '''            pressure_advance = self._optional_float(\n                payload.get("pressure_advance"),\n                "Pressure advance",\n                minimum=0.0,\n            )\n            emit_klipper_pa = bool(\n                payload.get("emit_klipper_pressure_advance", False)\n            )\n            if emit_klipper_pa and pressure_advance is None:\n                raise ValueError(\n                    "Pressure advance must be set when Klipper PA emission is enabled"\n                )\n            tuning["pressure_advance"] = pressure_advance\n            tuning["emit_klipper_pressure_advance"] = emit_klipper_pa\n            tuning["flow_percent"] = self._optional_float(''',
    label="save PA fields",
)

helper_marker = '''        return values, changed\n\n    def _transform_slice_settings(\n'''
helper_code = '''        return values, changed\n\n    def _apply_klipper_pa_to_global_slice_values(\n        self,\n        settings: Dict[str, Any],\n    ) -> Tuple[Dict[str, Any], str]:\n        \"\"\"Add capability PA to Cura's transient copied machine start G-code.\n\n        This runs inside the existing StartSliceJob copied-settings hook. It does\n        not mutate Cura's live stack and does not rewrite finished G-code. Alpha.4\n        deliberately supports exactly one enabled extruder until Eventide has an\n        explicit Cura-to-Klipper extruder-name mapping.\n        \"\"\"\n        values = dict(settings)\n        if not self._shared_library_path:\n            return values, \"\"\n\n        root = os.path.normpath(self._shared_library_path)\n        if not os.path.isfile(self._manifest_path(root)):\n            return values, \"\"\n\n        global_stack = self._application.getGlobalContainerStack()\n        if global_stack is None:\n            return values, \"Klipper PA not emitted: no active printer stack\"\n\n        enabled_extruders = [\n            extruder\n            for extruder in list(getattr(global_stack, \"extruderList\", []) or [])\n            if bool(getattr(extruder, \"isEnabled\", True))\n        ]\n        if len(enabled_extruders) != 1:\n            return values, \"Klipper PA not emitted: requires exactly one enabled extruder\"\n\n        extruder_stack = enabled_extruders[0]\n        effective_settings: Dict[str, Any] = {}\n        try:\n            keys = extruder_stack.getAllKeys()\n        except (AttributeError, TypeError):\n            keys = []\n        for key in keys:\n            try:\n                effective_settings[str(key)] = extruder_stack.getProperty(key, \"value\")\n            except Exception:\n                Logger.logException(\n                    \"w\",\n                    \"Eventide could not read extruder setting %s while resolving Klipper PA\",\n                    key,\n                )\n\n        try:\n            record, context = self._find_capability_for_slice_stack(\n                root, extruder_stack, effective_settings\n            )\n        except FileNotFoundError:\n            return values, \"\"\n        except Exception as error:\n            Logger.logException(\"e\", \"Eventide Klipper PA slice resolution failed\")\n            return values, \"Klipper PA not emitted: {}\".format(error)\n\n        tuning = record.get(\"tuning\", {})\n        if not bool(tuning.get(\"emit_klipper_pressure_advance\", False)):\n            return values, \"\"\n\n        pressure_advance = tuning.get(\"pressure_advance\")\n        if pressure_advance in (None, \"\"):\n            return values, \"Klipper PA not emitted: enabled but value is unset\"\n        try:\n            pressure_advance = float(pressure_advance)\n        except (TypeError, ValueError):\n            return values, \"Klipper PA not emitted: invalid value\"\n        if pressure_advance < 0:\n            return values, \"Klipper PA not emitted: value must be at least 0\"\n\n        if \"machine_start_gcode\" not in values:\n            return values, \"Klipper PA not emitted: machine_start_gcode unavailable\"\n\n        start_gcode = str(values.get(\"machine_start_gcode\", \"\") or \"\")\n        command = \"SET_PRESSURE_ADVANCE ADVANCE={:g} ; Eventide Shared Profiles\".format(\n            pressure_advance\n        )\n        separator = \"\" if not start_gcode or start_gcode.endswith((\"\\n\", \"\\r\")) else \"\\n\"\n        values[\"machine_start_gcode\"] = start_gcode + separator + command\n        return values, \"Klipper PA={:g} ({})\".format(\n            pressure_advance, context.get(\"material_name\", \"material\")\n        )\n\n    def _transform_slice_settings(\n'''
py = replace_exact(py, helper_marker, helper_code, label="PA global helper")

py = replace_exact(
    py,
    '''        if getattr(stack, "material", None) is None:\n            self._slice_capability_snapshots = {}\n            self._last_slice_resolution = "Slice started; resolving Eventide capabilities per extruder."\n            return original_values''',
    '''        if getattr(stack, "material", None) is None:\n            self._slice_capability_snapshots = {}\n            transformed, pa_note = self._apply_klipper_pa_to_global_slice_values(\n                original_values\n            )\n            self._last_slice_resolution = (\n                "Slice started; resolving Eventide capabilities per extruder."\n                + ((" " + pa_note) if pa_note else "")\n            )\n            return transformed''',
    label="global PA hook",
)

py = replace_exact(
    py,
    '''    @pyqtProperty(str, notify=stateChanged)\n    def capabilityFlowPercent(self) -> str:\n        return self._capability_flow_percent\n''',
    '''    @pyqtProperty(str, notify=stateChanged)\n    def capabilityPressureAdvance(self) -> str:\n        return self._capability_pressure_advance\n\n    @pyqtProperty(bool, notify=stateChanged)\n    def capabilityEmitKlipperPA(self) -> bool:\n        return self._capability_emit_klipper_pa\n\n    @pyqtProperty(str, notify=stateChanged)\n    def capabilityFlowPercent(self) -> str:\n        return self._capability_flow_percent\n''',
    label="PA QML properties",
)

py = replace_exact(
    py,
    '''                hotend = record.get("hotend", {})\n                if hotend.get("nozzle_diameter_mm") in (None, ""):\n                    warnings.append("{}: nozzle diameter unset".format(name))\n''',
    '''                hotend = record.get("hotend", {})\n                if hotend.get("nozzle_diameter_mm") in (None, ""):\n                    warnings.append("{}: nozzle diameter unset".format(name))\n                tuning = record.get("tuning", {})\n                if (\n                    tuning.get("emit_klipper_pressure_advance")\n                    and tuning.get("pressure_advance") in (None, "")\n                ):\n                    warnings.append(\n                        "{}: Klipper PA emit enabled but PA is unset".format(name)\n                    )\n''',
    label="PA validation",
)

qml = replace_exact(
    qml,
    '''        materialFlow.text = eventideBridge.capabilityFlowPercent\n        tempOffset.text = eventideBridge.capabilityTemperatureOffset''',
    '''        materialFlow.text = eventideBridge.capabilityFlowPercent\n        pressureAdvance.text = eventideBridge.capabilityPressureAdvance\n        klipperPa.checked = eventideBridge.capabilityEmitKlipperPA\n        tempOffset.text = eventideBridge.capabilityTemperatureOffset''',
    label="QML populate PA",
)

qml = replace_exact(
    qml,
    '''            "flow_percent": materialFlow.text,\n            "temperature_offset_c": tempOffset.text,''',
    '''            "flow_percent": materialFlow.text,\n            "pressure_advance": pressureAdvance.text,\n            "emit_klipper_pressure_advance": klipperPa.checked,\n            "temperature_offset_c": tempOffset.text,''',
    label="QML save PA",
)

qml = replace_exact(
    qml,
    '''                                    Label { text: "Material flow (%)"; color: UM.Theme.getColor("text") }\n                                    TextField { id: materialFlow; Layout.fillWidth: true; placeholderText: "inherit Cura"; selectByMouse: true }\n\n''',
    '''                                    Label { text: "Material flow (%)"; color: UM.Theme.getColor("text") }\n                                    TextField { id: materialFlow; Layout.fillWidth: true; placeholderText: "inherit Cura"; selectByMouse: true }\n\n                                    Label { text: "Pressure advance"; color: UM.Theme.getColor("text") }\n                                    TextField { id: pressureAdvance; Layout.fillWidth: true; placeholderText: "unset"; selectByMouse: true }\n\n                                    Label { text: "Klipper pressure advance"; color: UM.Theme.getColor("text") }\n                                    CheckBox { id: klipperPa; text: "Emit SET_PRESSURE_ADVANCE" }\n\n''',
    label="QML PA controls",
)

plugin = replace_exact(
    plugin,
    '"version": "0.9.0-alpha.3"',
    '"version": "0.9.0-alpha.4"',
    label="plugin version",
)

changelog = replace_exact(
    changelog,
    "# Changelog\n\n## 0.9.0-alpha.3",
    """# Changelog\n\n## 0.9.0-alpha.4\n\n- Restored Pressure Advance as a printer/material/nozzle capability field.\n- Added an explicit Klipper PA emission toggle to the Capability UI.\n- Emits `SET_PRESSURE_ADVANCE` through CuraEngine's transient copied `machine_start_gcode` value before slicing; live Cura stacks and finished G-code are not rewritten.\n- Limits automatic Klipper PA emission to exactly one enabled extruder until an explicit Cura-to-Klipper extruder-name mapping exists.\n- Validates that PA is present when Klipper PA emission is enabled.\n\n## 0.9.0-alpha.3""",
    label="changelog alpha4",
)

PY_PATH.write_text(py, encoding="utf-8")
QML_PATH.write_text(qml, encoding="utf-8")
PLUGIN_PATH.write_text(plugin, encoding="utf-8")
CHANGELOG_PATH.write_text(changelog, encoding="utf-8")
print("Applied Eventide Shared Profiles v0.9.0-alpha.4 Pressure Advance changes.")
