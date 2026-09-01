# Eventide Shared Profiles v0.9.0-alpha.3 — Exact Archive

This branch is an immutable archive of the **exact v0.9.0-alpha.3 ZIP produced in the ChatGPT 3D Printer project thread**.

It is intentionally separate from `main` and from active 0.9 development. Do not treat this branch as a development branch and do not merge it into `main`.

## Canonical artifact

Filename: `EventideSharedProfiles-v0.9.0-alpha.3.zip`

Size: `57803` bytes

SHA-256:

`e87b8c98e7c89fa0457ba89dadfe5b6094816fe7bb3dc05cac0df300c5bf6a03`

The exact ZIP is stored as Base64 chunks in `parts/`, split only because the GitHub connector could not directly transfer the binary artifact. The chunks themselves were verified against the local canonical ZIP by Git blob SHA before this archive commit was created.

## Reconstruct the exact ZIP

Linux/macOS/Git Bash:

```bash
cat parts/*.b64 | base64 --decode > EventideSharedProfiles-v0.9.0-alpha.3.zip
sha256sum EventideSharedProfiles-v0.9.0-alpha.3.zip
unzip EventideSharedProfiles-v0.9.0-alpha.3.zip
```

PowerShell:

```powershell
$b64 = (Get-ChildItem parts\*.b64 | Sort-Object Name | ForEach-Object { Get-Content $_ -Raw }) -join ''
[IO.File]::WriteAllBytes('EventideSharedProfiles-v0.9.0-alpha.3.zip', [Convert]::FromBase64String($b64))
Get-FileHash .\EventideSharedProfiles-v0.9.0-alpha.3.zip -Algorithm SHA256
Expand-Archive .\EventideSharedProfiles-v0.9.0-alpha.3.zip -DestinationPath .\alpha3
```

The SHA-256 **must** be:

`e87b8c98e7c89fa0457ba89dadfe5b6094816fe7bb3dc05cac0df300c5bf6a03`

## Exact extracted source Git blob hashes

These hashes are from the files inside the canonical alpha.3 ZIP. They can be used to verify that no source file has changed after extraction.

| File | Git blob SHA-1 |
| --- | --- |
| `EventideSharedProfiles/CHANGELOG.md` | `81b9199e365a9f23c5f6e579615ebb28f15b1418` |
| `EventideSharedProfiles/EventideLibraryMonitor.py` | `da1d2207dde040dcb5e6fc5b8e99fa4ca430ddcd` |
| `EventideSharedProfiles/EventidePreferences.py` | `e9b1cfc2f67917b939983b0e8acb5c9716710d01` |
| `EventideSharedProfiles/EventideSharedProfiles.py` | `74c86d65b51f81a9f2ef1961149eb62da327b291` |
| `EventideSharedProfiles/EventideStorage.py` | `3bcc9e16f7661614d321f095201640f6d6c0306d` |
| `EventideSharedProfiles/README.md` | `64963a9e21f92178746086aaeb7c9f86cd6372a5` |
| `EventideSharedProfiles/TESTING.md` | `6eb14c3689472547a63616d9a776a2956d923066` |
| `EventideSharedProfiles/__init__.py` | `54e13f2f6f7e574e13472ea18e114f4b42e0e3c2` |
| `EventideSharedProfiles/plugin.json` | `3a84591ac4626d79acee81c7277c512aec01ff4d` |
| `EventideSharedProfiles/qml/EventideQualityConflictDialog.qml` | `bfe407355b958d800664348712e55037f21f6281` |
| `EventideSharedProfiles/qml/EventideSharedProfilesWindow.qml` | `eb0b9fa1e3a0a5c1875740b4ed4518e7e77cd219` |

## Pressure Advance state in alpha.3

Pressure Advance was **intentionally removed from the active alpha.3 capability model/UI**, inherited from the v0.9.0-alpha.2 refactor. It was not accidentally omitted from QML.

The alpha.2 changelog states that final G-code parsing/rewrite and Klipper pressure-advance command injection were removed, while capability limits continue to apply through transient CuraEngine settings before slicing.

In `EventideSharedProfiles.py`, `saveCurrentCapability()` explicitly removes legacy keys on save:

```python
tuning.pop("pressure_advance", None)
tuning.pop("emit_klipper_pressure_advance", None)
```

The active alpha.3 capability creation, editor-loading, and slice-time application paths do not create or consume Pressure Advance. There is no `SET_PRESSURE_ADVANCE` emission path in this checkpoint.

The later calibration requirement **Pressure Advance = 0.055 for Trident + ELEGOO PLA Pro Peach Pink** is intentionally *not* written into this archive, because doing so would alter alpha.3.
