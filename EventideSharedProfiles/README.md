# Eventide Shared Profiles 0.9.0 Alpha 2 (refactor branch)

Eventide Shared Profiles 0.9.0-alpha.2 is a development refactor of the 0.8.7 public beta for UltiMaker Cura 5.13.x that keeps selected Cura configuration objects in a shared filesystem library (including SMB/NAS paths) while leaving Cura itself local and responsive.

> **Development build:** 0.8.7-beta remains the public-beta baseline. This alpha focuses on architecture and reliability. Core helpers are CI-tested on Windows, macOS, and Linux, but full Cura integration testing is still Windows-first.


## Current beta scope

Working now:

- portable custom-material publish/import with preserved Cura GUID
- machine-instance publish/recreation when the target Cura has the same base definition
- **two-way shared custom quality-profile synchronization**
  - global + per-extruder `quality_changes` groups
  - Eventide-scoped to the shared printer record
  - stable cross-PC Eventide identity
  - revision/content-hash conflict protection
- **background shared-library polling through Uranium `Job`** (10 s idle cadence; local edits request an earlier debounced pass)
- automatic modal alert when a new quality-profile conflict is detected, even if the main Eventide window is closed
- visible quality-profile conflict queue with explicit **Keep This PC**, **Use Shared Version**, and **Create New Profile** resolution
- automatic synchronization when shared records change
- synchronized quality-profile soft deletion using reversible `is_deleted` records
- safe removal of a remotely deleted profile even when that custom profile is currently active
- publisher-version stamping and stale-plugin overwrite protection
- printer + material + extruder + nozzle capability records
- transient slice-time capability injection without persistent Cura `userChanges`
- max linear-speed and max volumetric-flow enforcement
- temperature offset, retraction override, material-flow multiplier
- no post-slice G-code rewriting; capability limits are resolved into CuraEngine settings before slicing
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

Eventide never overwrites a quality profile when both the local copy and the shared copy changed since the last synchronization. It reports the conflict in the normal **Profiles** tab and offers three explicit resolutions:

- **Keep This PC** — publish this PC's edit as an authoritative shared resolution; clients still holding the resolved losing/baseline version automatically accept the chosen winner.
- **Use Shared Version** — replace this PC's conflicting copy with the current NAS version.
- **Create New Profile** — preserve this PC's edit under an editable new profile name, then restore the original profile to the current shared version.

A later independent edit that is not part of the resolved conflict remains protected; Eventide does not use a Keep This PC decision to bulldoze unrelated new work.

Quality records are scoped to an Eventide printer record for synchronization. Cura itself exposes custom profiles by **quality definition**, not by unique machine instance. Printers with unique quality definitions therefore behave machine-specific; generic/custom printers that share Cura's `fdmprinter` quality definition may still show the same installed custom profile in Cura's native profile list. Eventide does not currently override Cura's native filtering behavior.

### Deletion synchronization

Deleting a previously synchronized custom quality profile no longer replaces or erases its NAS record. Eventide keeps the complete quality payload and increments the record revision while setting `is_deleted: true`. Other connected clients remove the corresponding Eventide-managed Cura containers, and stale clients cannot silently overwrite the deletion. If recovery is needed, changing only `is_deleted` back to `false` in the shared JSON causes Eventide to reinstall that same profile identity on the next sync. A newly-created Cura profile may still reuse the same visible name and receives a new Eventide quality ID.

If deletion races with an independent edit, Eventide does not guess. The conflict offers **Accept Deletion**, **Keep as New Profile**, and **Restore Profile**. If the deleting PC discovers that the shared copy was edited first, it must explicitly choose whether to delete that newer edit or restore it locally.

Every newly written shared JSON record and the manifest include `publisher_plugin_version`. If a client encounters data published by a newer Eventide plugin, it refuses to overwrite that data and reports **PLUGIN UPDATE REQUIRED**. Older unstamped records remain readable.

## Still not finished

- syncing third-party custom machine `.def.json` files that are absent on the target Cura
- polished public installer / Cura Marketplace packaging

## Beta testing

See `TESTING.md`. Please use **Advanced → Export diagnostics** when filing a bug. The diagnostics report includes local machine/path information, so review it before sharing publicly.
