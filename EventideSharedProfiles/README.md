# Eventide Shared Profiles 0.7.0 Beta

Eventide Shared Profiles is an early beta plugin for UltiMaker Cura 5.13.0.

## Beta scope

Currently working:

- shared printer/material identity records
- printer + material + extruder + nozzle capability records
- transient slice-time capability injection without persistent Cura `userChanges`
- max linear-speed and max volumetric-flow enforcement
- temperature offset
- retraction distance/speed override
- material-flow multiplier
- optional Klipper `SET_PRESSURE_ADVANCE`
- final G-code hard-limit guardrail for single-extruder slices
- persistent local nozzle-material binding (for example Brass vs Hardened Steel)
- optimistic revision conflict protection
- atomic JSON writes
- shared-library polling/inventory refresh
- library validation
- local diagnostics export

Not finished yet:

- recreating/synchronizing complete Cura printer definitions on another computer
- recreating/synchronizing complete Cura custom material definitions on another computer
- shared quality-profile capture/application
- edit lock/lease UI (revision conflicts already prevent stale overwrites)
- final G-code hard-limit/PA stamping for multi-extruder slices

## Safety behavior

If Eventide cannot uniquely resolve a capability or a capability is malformed, it fails open to Cura's untouched copied settings rather than guessing. Multi-extruder slices currently skip the final Eventide G-code guardrail/PA stamping rather than applying the wrong tool's capability.

## Tester workflow

1. Install into Cura's plugin directory and restart Cura.
2. Open **Extensions → Eventide Shared Profiles**.
3. Set a shared library path and initialize it.
4. Register the current printer/material selection.
5. Bind the active nozzle material if applicable.
6. Edit/save the capability.
7. Slice normally; opening Eventide is not required for capability activation after startup.
8. Use **Diagnostics → Validate Library** and **Export Diagnostics** when filing bugs.

Review exported diagnostics before sharing; it includes local host, selection IDs, and the shared-library path.

## Wider beta testing

See `TESTING.md` for the repeatable no-printer-required regression matrix.
