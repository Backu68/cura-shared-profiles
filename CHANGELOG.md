# Changelog

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
