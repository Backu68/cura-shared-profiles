# Changelog

## 0.7.0 Beta

- Added material flow (%) capability field and transient CuraEngine propagation.
- Added persistent per-printer/extruder/nozzle nozzle-material binding.
- Added 3-second shared-library manifest polling and inventory refresh.
- Added shared-library JSON/schema/reference validation.
- Added local diagnostic report export for beta bug reports.
- Added explicit calibration status and opt-in "mark calibrated" saves.
- Normal capability saves no longer automatically update the calibration timestamp.
- Added safe multi-extruder behavior: final G-code guardrail and Klipper PA stamp are skipped instead of guessing a tool.
- Preserved v0.6 transient slice architecture, hard linear/volumetric guardrails, optimistic revision conflict checks, and atomic writes.
