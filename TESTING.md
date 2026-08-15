# Eventide Shared Profiles 0.7.0 Beta Test Matrix

These tests do not require a functioning printer unless explicitly noted.

## 1. Startup / hook

- Restart Cura.
- Do not open Eventide.
- Slice with a material that has a saved capability.
- Expected: capability affects the slice automatically.
- Open Eventide afterward; **Slice Hook** should show **ACTIVE**.

## 2. Managed → unmanaged → managed selection

- Slice a material with an Eventide capability.
- Switch to a material with no matching capability and reslice.
- Switch back and reslice.
- Expected: Eventide applies only to the managed material. No stale header, PA, temperatures, speeds, or retraction values may leak into the unmanaged slice.

## 3. Material-flow propagation (no printer required)

- Save a clearly different **Material flow (%)**, for example 80.
- Slice a known model.
- Expected: Eventide header includes `EVENTIDE_FLOW_PERCENT=80.0` (format may vary), and CuraEngine output extrusion values/filament use change accordingly.
- Restore the field to blank after the test.

## 4. Linear hard limit

- Set a deliberately low max linear speed and a very high volumetric limit.
- Slice/export G-code.
- Expected: `EVENTIDE_GCODE_MAX_SPEED_AFTER_MM_S` is at or below the requested ceiling.

## 5. Volumetric hard limit

- Set a very high linear limit and deliberately low volumetric limit.
- Slice/export G-code.
- Expected: `EVENTIDE_GCODE_MAX_FLOW_AFTER_MM3_S` is at or below the requested ceiling.

## 6. Temperature / retraction / PA

- Use diagnostic values only; do not print them.
- Verify the resulting G-code and Eventide header reflect the configured temperature offset, retraction distance/speed, and optional Klipper pressure advance.

## 7. Nozzle-material binding

- Create two capability records for the same printer/material/extruder/nozzle diameter but different nozzle materials.
- Bind one in **Current Selection → Active nozzle material**.
- Restart Cura and slice without opening Eventide.
- Expected: the bound nozzle-material capability resolves automatically.

## 8. Multi-client revision conflict

- PC1 loads capability revision N.
- PC2 loads capability revision N.
- PC1 saves, producing N+1.
- PC2 attempts to save its stale editor.
- Expected: PC2 receives `CAPABILITY CONFLICT` and does not overwrite N+1.

## 9. Live library watcher

- Keep Eventide open on PC1.
- Modify/save a library record on PC2.
- Expected: PC1 refreshes inventory/status within a few seconds without re-opening the window.
- The capability editor is intentionally not silently replaced; stale-save revision protection remains authoritative.

## 10. Library validation

- Run **Validate Library** on a healthy library: expected `VALIDATION OK` (warnings are allowed for intentionally unset fields).
- For a controlled test copy only, corrupt a JSON record or break a capability's printer/filament reference.
- Expected: validation reports the problem; slicing must fail open to untouched Cura settings rather than guessing.

## Bug reports

Use **Diagnostics → Export Diagnostics** and attach the report with the Cura log and, when relevant, the generated G-code. Review the diagnostics text before sharing because it contains host/selection IDs and the shared-library path.


## Cross-PC material and machine sync (0.7.1)
1. On PC A, select a custom material and printer and click **Register Current Selection**.
2. Validate the library; the material/printer records should no longer warn that their portable definitions are unpublished.
3. On PC B, where the custom material is absent, click **Sync Library to Cura**.
4. Confirm the material appears in Cura and its GUID matches the shared material record.
5. If the printer is absent but its base definition exists on PC B, confirm Eventide recreates the machine and restores its machine definition changes.
6. Run Sync again; it must report the material/machine as already local and must not create duplicates.
7. If a printer record references a base definition PC B does not have, sync must report `missing base definition` and must not guess or install a substitute.
