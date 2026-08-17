# Eventide Shared Profiles 0.8.7 Beta Test Matrix

No printer hardware is required for these tests.

## A. Fresh install / connection

1. Install Cura 5.13.x on a clean workstation.
2. Install Eventide Shared Profiles and restart Cura.
3. Open Eventide and use **Browse…** to select the shared library.
4. Click **Connect**.

Expected: the library connects without typing a UNC path manually; existing shared machines/materials synchronize.

## B. Material and machine bootstrap

On PC1 choose a custom material and printer, then click **Share current setup**. On PC2 connect/sync.

Expected: missing custom material appears with the same material GUID; missing machine instance appears when its base Cura definition exists locally.

## C. Quality-profile first sync

1. On PC1 create/save a Cura custom quality profile for a shared printer.
2. Leave Cura running for several seconds.
3. On PC2 use the same shared library and printer.

Expected: the custom profile appears in Cura on PC2 without manually exporting/importing a Cura profile.

## D. Live quality update

1. Confirm the profile is synchronized on both PCs.
2. Edit/save that custom profile on PC1.
3. Wait for live sync on PC2.
4. Then edit/save the same profile on PC2 and wait for PC1.

Expected: changes propagate in **both directions** automatically when the receiving PC has not changed the profile locally. This specifically verifies imported-machine Eventide identity resolution.

## E. Conflict protection

1. Start with the same synchronized quality profile on PC1 and PC2.
2. Make a different edit to the profile on both PCs before either side has received the other's change.
3. Let live sync run.

Expected: Eventide reports a quality conflict and does not silently overwrite either local edited copy.

## F. Quality profile machine scoping

Create two shared printer instances that can both use Cura's generic `fdmprinter` quality definition. Create a custom quality profile under only one Eventide printer.

Expected: Eventide's shared record remains associated with the originating Eventide printer. Note that Cura's native custom-profile list is quality-definition based, so another local printer using the same `fdmprinter` quality definition may still display the installed profile. Record ownership and Cura visibility are intentionally treated as separate concepts.

## G. Capability regression

Repeat the known v0.6/v0.7 no-hardware slice tests:

- matching material capability activates automatically
- switching to an unmatched material returns to plain Cura
- max linear speed hard ceiling
- max volumetric flow hard ceiling
- temperature offset
- retraction distance/speed
- material flow multiplier
- optional Klipper pressure advance

## H. Diagnostics

Open **Library → Advanced**, run Validate and Export diagnostics.

Expected: validation includes printer, filament, capability and quality record references/hashes. Diagnostics file is created locally.


## v0.8.2 conflict-resolution test

1. Start Cura on two connected PCs with the same shared custom quality profile current.
2. Disconnect one PC from the NAS temporarily or make near-simultaneous different edits so both local and shared versions move from the same known revision.
3. Confirm neither side is silently overwritten and **Conflict requires attention** appears under Profiles.
4. Test **Keep This PC**: the local version becomes the next shared revision.
5. Recreate a conflict and test **Use Shared Version**: only that PC's local copy is replaced by the current NAS version.
6. Recreate a conflict and test **Create New Profile**: the local conflicting edit is preserved under the entered new name, while the original profile returns to the NAS version.
7. Confirm the newly created profile also propagates to the other connected Cura instance.

## v0.8.3 modal conflict test

1. Leave the main Eventide window closed on both PCs.
2. Create different edits to the same synchronized quality profile before either side receives the other's change.
3. Expected: Eventide opens a modal **Shared profile conflict detected** window once the conflict is detected.
4. Close the modal without choosing a resolution. Expected: neither profile is overwritten and the conflict remains under Profiles.
5. Recreate/continue the conflict and verify each resolution path: **Keep This PC**, **Use Shared Version**, and **Create New Profile**.
6. Expected: the modal does not repeat every 2.5-second polling cycle for the same conflict state.

## Conflict status regression (0.8.4)

After resolving a quality-profile conflict with **Keep This PC**, **Use Shared Version**, or **Create New Profile**, verify the Synchronization box no longer retains a stale `1 conflict(s)` message. It should report zero unresolved conflicts when none remain.


## Keep This PC authoritative propagation regression (0.8.5)

1. Start two connected Cura instances with the same synchronized custom quality profile.
2. Create a real edit conflict so the two PCs hold different values.
3. On one PC choose **Keep This PC**.
4. Expected on the resolving PC: the conflict clears immediately and its local value becomes the shared winner.
5. Expected on the other already-running PC: within live-sync polling, the original profile changes to the chosen winning value automatically and no conflict remains.
6. Make a fresh unrelated edit on the second PC after it has accepted the resolution. Expected: that later edit publishes normally and is not treated as part of the old resolution.


## Legacy quality tombstone regression (0.8.6 compatibility)

1. Use an archived/isolated 0.8.6 test library containing an `eventide.shared_profiles.quality_tombstone` record. Do not create a new deletion with 0.8.7 for this test.
2. Open the library with 0.8.7. Expected: the legacy tombstone is accepted as deleted and the matching local Eventide-managed profile is removed safely.
3. Expected: 0.8.7 does not rewrite another deletion into the legacy destructive tombstone schema.

## Deletion conflict regression (0.8.7)

1. Start with the same synchronized profile on PC1 and PC2.
2. Prevent one PC from seeing the other's change long enough to create a race: delete the profile on PC1 while independently editing it on PC2.
3. Expected: Eventide does not silently destroy the independent edit or silently resurrect the deleted original.
4. Test **Accept Deletion**: the edited local copy is removed and the original shared record remains intact with `is_deleted: true`.
5. Recreate the race and test **Keep as New Profile**: the edited copy is preserved under a new Eventide identity/name, while the original remains soft-deleted.
6. Recreate the race and test **Restore Profile**: the edited version becomes a new active revision of the original shared Eventide identity with `is_deleted: false`.
7. Test the inverse ordering where the deleting PC first discovers a newer shared edit. Expected: Eventide asks for an explicit choice before deleting that newer edit.

## Publisher-version compatibility regression (0.8.7)

1. Publish or modify a printer, filament, capability, active quality profile, and soft-deleted quality record with 0.8.7.
2. Expected: each newly written shared JSON record contains `publisher_plugin_version: 0.8.7-beta`; the manifest is stamped too.
3. Remove the field from a copy of an old test record. Expected: 0.8.7 still reads the unstamped record.
4. In an isolated test library, change a record or manifest stamp to a numerically newer Eventide version (for example `0.9.0-beta`).
5. Expected: 0.8.7 reports **PLUGIN UPDATE REQUIRED** and does not overwrite that newer data.

## Reversible soft-delete regression (0.8.7)

1. Synchronize a custom profile on PC1 and PC2, then save a copy of its shared `quality/<id>.json` for comparison.
2. Delete the profile in Cura on PC1. Expected: the same JSON file and Eventide ID remain; revision increments; `is_deleted` becomes `true`; all profile payload fields remain intact.
3. Expected on PC2: Eventide safely detaches the profile if active, removes its local Eventide-managed containers, and does not republish the stale baseline.
4. Open the shared JSON manually and change only `is_deleted` from `true` to `false`. Save the file.
5. Expected: on the next live-sync pass Eventide reinstalls the original profile on both PCs using the same Eventide ID and retained settings. No manual revision edit is required.
6. Delete the original again, then create a brand-new Cura profile with the same visible name. Expected: the original record remains soft-deleted and the new profile publishes under a different Eventide quality ID.
7. Verify deleted records are not included in the normal active quality-profile count.
8. If a legacy 0.8.6 `eventide.shared_profiles.quality_tombstone` exists, expected: 0.8.7 still honors it but never creates a new destructive tombstone.
