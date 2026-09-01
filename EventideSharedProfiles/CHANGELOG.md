# Changelog

## 0.9.0-alpha.4

- Restored Pressure Advance as a printer/material/nozzle capability field.
- Added an explicit Klipper PA emission toggle to the Capability UI.
- Emits `SET_PRESSURE_ADVANCE` through CuraEngine's transient copied `machine_start_gcode` value before slicing; live Cura stacks and finished G-code are not rewritten.
- Limits automatic Klipper PA emission to exactly one enabled extruder until an explicit Cura-to-Klipper extruder-name mapping exists.
- Validates that PA is present when Klipper PA emission is enabled.

## 0.9.0-alpha.3

- Changed the empty shared-library path placeholder to `\\path\\to\\share` so a remembered real path is visually distinct from the example state.

## 0.9.0-alpha.2

- Replaced the custom Python daemon-thread library monitor with Uranium's native `Job` / `JobQueue` mechanism.
- Increased routine NAS/SMB change-detection cadence from 2.5 seconds to 10 seconds; local Cura edits request an earlier debounced scan without allowing overlapping Jobs.
- Removed final G-code parsing, feed-rate rewriting, extrusion-delta tracking, and Klipper pressure-advance command injection.
- Printer/material capability limits continue to apply only through transient CuraEngine settings before slicing.
- Removed beta-only PA emission controls from the Capability UI; old record keys remain readable and are dropped when a capability is next saved.
- Fixed alpha.1 stale `_config_path` references introduced by moving local state into Cura Preferences.
- Material import now uses Python temporary files instead of a hand-managed sync-temp directory.
- Diagnostics export now opens an explicit Save dialog rather than silently choosing an external local path.

## 0.9.0-alpha.1

- Began pre-1.0 architecture hardening without changing the 0.8.7 shared-library format.
- Split storage, Cura preference persistence, G-code guardrail logic, and background library scanning into focused modules.
- Moved local Eventide state to Cura Preferences with one-time migration from 0.8.x JSON configuration.
- Routine NAS/SMB fingerprint scans now run off Cura's UI thread; local quality publication is event/debounce driven.
- Removed the implicit extruder-0 fallback and now follows Cura's selected/default extruder.
- Final distance-E G-code guardrails are explicitly limited to compatible G-code flavors instead of assuming Marlin semantics.
- Unknown non-primitive setting values are rejected/logged instead of silently stringified into shared records.
- Added standard-library core regression tests and Windows/macOS/Linux GitHub Actions CI.
- Began narrowing/logging broad exception fallbacks.

## 0.8.7-beta

- Changed synchronized quality deletion from destructive tombstones to reversible soft deletion.
- Quality records now retain their complete profile payload and use `is_deleted: true` as the deletion switch.
- Deleting a profile increments its revision and records deletion metadata without discarding `global_values`, extruder values, name, Eventide ID, or content hash.
- Manually changing `is_deleted` from `true` back to `false` in the shared JSON causes Eventide to reinstall the profile on the next synchronization pass.
- Deleted records are synchronized before active records so a newly-created profile can safely reuse the same visible name under a new Eventide ID.
- Active-profile detachment, stale-client protection, deletion/edit conflicts, and publisher-version guards remain in place.
- Legacy 0.8.6 `quality_tombstone` records remain readable for backward compatibility, but 0.8.7 no longer creates them.

## 0.8.6-beta

- Added synchronized quality-profile deletion with persistent tombstones.
- Tombstones use a separate schema so pre-0.8.6 clients cannot casually republish a stale live record over a deletion.
- Offline/stale clients accept a tombstone when their local copy is still the synchronized baseline instead of resurrecting it.
- Added safe active-profile detachment before Eventide removes remotely deleted Cura `quality_changes` containers.
- Added deletion conflicts with **Accept Deletion**, **Keep as New Profile**, and **Restore Profile** resolution paths.
- Added protection for the inverse race where this PC deletes a profile while another PC publishes a newer edit.
- Reusing a deleted profile name creates a new Eventide quality identity; tombstones are retained indefinitely.
- Added `publisher_plugin_version` to every newly written shared record/tombstone and the library manifest.
- Added compatibility guardrails: data written by a newer Eventide version is left untouched and reports **PLUGIN UPDATE REQUIRED**.
- Pre-0.8.6 unstamped records remain readable for backward compatibility.

## 0.8.5-beta

- Fixed **Keep This PC** resolution propagation across already-running Cura clients.
- Keep This PC now writes an authoritative human-resolution marker with the winning and known losing content hashes.
- Other clients automatically apply the chosen winner when their local copy is the resolved losing/baseline version instead of silently clearing bookkeeping or republishing the loser.
- A genuinely new local edit that is not part of the resolved conflict remains protected and still raises a conflict.

## 0.8.4-beta

- Fixed stale Synchronization summary after resolving a quality-profile conflict.
- Successful conflict resolution now immediately reports the current unresolved conflict count instead of retaining the previous sync result.

## 0.8.3-beta

- Added an automatic modal quality-conflict alert that can appear even when the main Eventide window is closed.
- The modal exposes the same three explicit resolutions: **Keep This PC**, **Use Shared Version**, and **Create New Profile**.
- A conflict popup is shown once per distinct local/shared conflict state rather than repeating every polling cycle.
- Closing the modal leaves the conflict protected and available in the Profiles tab.

## 0.8.2-beta

- Added visible quality-profile conflict handling in the normal Profiles tab.
- Added explicit **Keep This PC** and **Use Shared Version** conflict resolution.
- Added **Create New Profile** to preserve the local conflicting edit under an editable new profile name, then restore the shared original.
- Added one-by-one navigation when multiple quality conflicts exist.
- Conflict resolutions remain non-destructive to unresolved edits on other PCs.
- Fixed the plugin version display so QML reads the Python plugin version instead of a hard-coded label.

## 0.8.1-beta

- Fixed two-way live quality-profile updates on imported machine instances.
- Imported Cura machines now resolve their persisted Eventide printer record identity instead of deriving a new shared ID from the destination PC's local Cura machine ID.
- Capability lookup uses the same cross-PC shared printer identity.
- Share Current Setup now stamps/binds the source machine to the same Eventide printer record identity for symmetric behavior across PCs.

## 0.8.0-beta

- Added two-way synchronization of Cura custom quality profiles.
- Preserves Cura's global + per-extruder `quality_changes` grouping.
- Added stable Eventide quality-profile IDs so profiles can round-trip across PCs.
- Added quality-profile revision/content-hash conflict protection.
- Added live SMB-friendly shared-library polling and automatic sync on library changes.
- Library watcher now fingerprints the shared record directories rather than relying only on `.eventide/library.json`.
- Added native **Browse…** folder selection for the library path.
- Reworked the UI to three normal tabs: Library, Profiles, Capability.
- Moved validation, diagnostics and engine status to an Advanced area.
- Removed normal-workflow IDs, material inspection, resolver test controls, and verbose engine/G-code status text.

## 0.7.1-beta

- Added portable custom-material publish/import with preserved GUID.
- Added machine-instance publish/recreation when the target has the same Cura base definition.
- Added explicit Sync Library to Cura action.

## 0.7.0-beta

- Added material-flow capability override.
- Added persistent nozzle-material bindings.
- Added library polling, validation, diagnostics, and beta hardening.

## 0.6.2

- Automatic post-startup slice hook.
- Transient capability resolver verified without opening Eventide first.
