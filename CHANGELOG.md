# Changelog

All notable changes to `power-rail-validation` are recorded here. Release
claims remain limited to the documented synthetic/offline software scope.

## [Unreleased] — 0.6.0.dev0

### Added

- Fail-closed `project_intake` contract and validator for one pinned REP-01
  regulator/rail slice.
- Capability-based artifact requirements, byte-exact artifact verification,
  cross-bindings, provenance/origin checks, and explicit gap records.
- `intake INPUT --artifact-root ROOT` CLI command with the existing status and
  exit-code contract.
- A canonical real REP-01 `5V` scenario and separate D5 checkpoint evidence.
- Repository-wide `AGENTS.md` governance contract with immutable D4/D5
  evidence, source-policy, test, and release boundaries.
- D6 reference-candidate qualification gate and external-reference storage
  policy.
- `TI-TLVM13660EVM` / `TLVM13660RDLR` as the first technically qualified
  `STATIC + BUCK + PIN` candidate in `QUALIFIED_FOR_LOCK` state; portfolio
  approval remains pending.

### Changed

- Source-tree development version advanced to `0.6.0.dev0`. The frozen D4
  wheel/sdist remain version `0.5.0`; no D5 distribution was built or
  published.
- Corrected the current remote-CI baseline: D5 commit `66f2073` passes the
  Python 3.9 and 3.12 GitHub Actions matrix. New D6 commits still require a
  fresh CI run.

### Scope limits

- Intake `PASS` means execution inputs are complete and byte-bound only.
- The current REP-01 scenario remains `BLOCKED` because the project
  configuration, power profile, rail demands, exact regulator selection,
  exact-part datasheet, and authorized model are unavailable.
- Buck, pin, transient, loop-stability, hardware, and G1-G6 evaluations remain
  `NOT_RUN` or open; no electrical or physical approval is claimed.

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
