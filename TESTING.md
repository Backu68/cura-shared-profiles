# Eventide Shared Profiles 0.8.0 Beta Test Matrix

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

Expected: PC2's copy updates automatically when PC2 has not changed it locally.

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
