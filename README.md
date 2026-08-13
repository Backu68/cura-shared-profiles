# Eventide Shared Profiles for Cura

Shared printer, filament, quality, and printer/filament capability profiles for UltiMaker Cura.

## Current milestone: v0.1 proof of hook

This first build intentionally does only three things:

1. Loads as a normal Cura extension plugin.
2. Adds **Extensions → Eventide Shared Profiles**.
3. Displays the currently active printer and material (including their Cura container IDs).

No network writes, synchronization, or capability overrides are implemented yet. Those come after the plugin/data hooks are proven on stock Cura.

## Target Cura version

- UltiMaker Cura 5.13.x
- Plugin API 8

## Manual development install

Copy the `EventideSharedProfiles` folder into Cura's user plugin directory, then restart Cura.

On Windows, use **Help → Show Configuration Folder** in Cura and place the folder under `plugins` inside the shown versioned configuration directory.

Expected layout:

```text
plugins/
└── EventideSharedProfiles/
    ├── __init__.py
    ├── plugin.json
    ├── EventideSharedProfiles.py
    └── qml/
        └── SharedProfiles.qml
```

After restarting Cura, open **Extensions → Eventide Shared Profiles**.
