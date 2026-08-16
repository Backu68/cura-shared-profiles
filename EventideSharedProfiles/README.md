# Eventide Shared Profiles 0.8.0 Beta

Eventide Shared Profiles is an early beta plugin for UltiMaker Cura 5.13.x that keeps selected Cura configuration objects in a shared filesystem library (including SMB/NAS paths) while leaving Cura itself local and responsive.

## Current beta scope

Working now:

- portable custom-material publish/import with preserved Cura GUID
- machine-instance publish/recreation when the target Cura has the same base definition
- **two-way shared custom quality-profile synchronization**
  - global + per-extruder `quality_changes` groups
  - Eventide-scoped to the shared printer record
  - stable cross-PC Eventide identity
  - revision/content-hash conflict protection
- **live shared-library polling** (SMB-friendly; no native filesystem watcher required)
- automatic synchronization when shared records change
- printer + material + extruder + nozzle capability records
- transient slice-time capability injection without persistent Cura `userChanges`
- max linear-speed and max volumetric-flow enforcement
- temperature offset, retraction override, material-flow multiplier
- optional Klipper `SET_PRESSURE_ADVANCE`
- single-extruder final G-code hard-limit guardrail
- optimistic record revision conflict protection and atomic JSON writes
- native folder **Browse…** button for the shared library path
- library validation and diagnostics export under Advanced

## Normal workflow

1. Install the plugin and restart Cura.
2. Open **Extensions → Eventide Shared Profiles**.
3. **Browse…** to the shared library and click **Connect**.
4. On a source Cura install, choose the printer/material and click **Share current setup**.
5. Custom Cura quality profiles for that shared printer are then published and updated automatically while Cura is running.
6. Other connected Cura installs automatically detect shared changes and install/update shared quality profiles when safe.

## Quality-profile safety

Cura represents one visible custom profile as a group containing a global `quality_changes` container and one container per extruder. Eventide stores and restores that group together.

Eventide never overwrites a quality profile when both the local copy and the shared copy changed since the last synchronization. It reports a conflict instead. Unsaved/independent local edits therefore are not silently destroyed.

Quality records are scoped to an Eventide printer record for synchronization. Cura itself exposes custom profiles by **quality definition**, not by unique machine instance. Printers with unique quality definitions therefore behave machine-specific; generic/custom printers that share Cura's `fdmprinter` quality definition may still show the same installed custom profile in Cura's native profile list. Eventide does not currently override Cura's native filtering behavior.

## Still not finished

- conflict-resolution UI for quality-profile edit conflicts
- synchronized deletion/tombstones for quality profiles
- syncing third-party custom machine `.def.json` files that are absent on the target Cura
- final G-code hard-limit/PA stamping for multi-extruder slices
- polished public installer / Cura Marketplace packaging

## Beta testing

See `TESTING.md`. Please use **Advanced → Export diagnostics** when filing a bug. The diagnostics report includes local machine/path information, so review it before sharing publicly.
