# Changelog

All notable changes to `power-rail-validation` are recorded here. Release
claims remain limited to the documented synthetic/offline software scope.

## [0.5.0] - 2026-10-24

### Added

- Six-command deterministic CLI for contract validation, rail budgets,
  sequencing, fault injection, regulator review, and report rendering.
- Shared process exit-code contract for `PASS`, `FAIL`, `UNKNOWN`, `BLOCKED`,
  `NOT_RUN`, input/usage failures, and unexpected internal failures.
- Versioned public JSON Schema for the normalized report model.
- Wheel/sdist build, clean-install, package-data, and installed CLI smoke
  verification.
- Minimal Python CI workflow and D4 release evidence.

### Changed

- Package version advanced to `0.5.0` for the D4 productization release.
- README now contains installation, quickstart, CLI, and release-boundary
  guidance.

### Scope limits

- This release validates synthetic fixtures, versioned contracts, and local
  artifact integrity only.
- Live REP-01/REP-03 adapters remain `NOT_RUN/BLOCKED`.
- GAP-02, GAP-03, G1, and G2-G6 hardware gates remain open or blocked.
- No physical hardware, transient waveform, loop-stability, EMC, ISO, AEC, or
  field-compliance approval is claimed.

## [0.4.0] - 2026-10-19

- Closed the D3 synthetic regulator-review, offline file-API, reporting, and
  evidence scope while preserving live-integration and hardware claim limits.

## [0.3.0] - 2026-10-19

- Added deterministic fault-injection and initial common reporting support.
