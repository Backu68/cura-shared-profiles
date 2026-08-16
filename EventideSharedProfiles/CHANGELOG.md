# Changelog

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
