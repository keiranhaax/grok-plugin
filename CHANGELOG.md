# Changelog

All notable changes to Grok Advisor are documented here.

## Unreleased

## 0.2.1 - 2026-07-13

### Added

- Added `minimum_cli_version` and deterministic, ordered `readiness_issues` to
  `grok_status`, including separate CLI-version and authentication diagnostics.
- Added safe `grok_not_ready` details containing only readiness issues and CLI
  version bounds.
- Added `failure_stage`, `automatic_retry_performed`, and
  `manual_retry_allowed` diagnostics for rejected structured output.

### Changed

- Renamed the user-facing product from Grok Orchestrator to Grok Advisor while
  retaining the stable `grok-orchestrator` package, skill, and MCP identifiers.
- Replaced the cyclic architecture flowchart with a request/response sequence
  that distinguishes Codex-side advisor policy from the MCP runtime boundary and
  documents the no-model status path plus parallel panel behavior.
- Compacted the sequence diagram with two-line labels, activation bars, and a
  transparent plugin-boundary group, moving status and panel caveats into prose.
- Strengthened all schema-bound role instructions to require JSON without
  Markdown fences, preambles, or trailing commentary.
- Documented version-path, authentication, structured-output, and preflight
  troubleshooting without recommending deletion of inactive CLI downloads.

### Fixed

- Removed raw semicolons from the architecture notes so GitHub's Mermaid parser
  renders the sequence diagram instead of treating note prose as new statements.
- Corrected release-smoke documentation: the script verifies isolated package
  install, cache, reinstall, and stdio discovery without using credentials or
  Grok allowance.
- Preserved `ready_unverified` after structured-output rejection and made the
  no-automatic-retry policy explicit.

## 0.2.0 - 2026-07-13

### Added

- Structured `review_plan_with_grok` gate with fail-closed `PLAN_APPROVED` and
  `PLAN_REVISE` semantics.
- Opt-in `review_with_grok_panel` with two or three fresh, concurrent,
  independent Grok reviews.
- JSON Schema output contracts and local semantic validation for plan,
  research, workspace, and panel modes.
- Structured research claims, source catalogs, uncertainties, and inferences.
- Structured workspace findings with severity, file, line, evidence, impact,
  remediation, and recommended tests.
- Truthful `ready_unverified`, `route_accepted`, `used_and_confirmed`, and
  `unavailable` route states.
- CLI capability, exact-model, profile-integrity, authentication-isolation, and
  integration-isolation checks in `grok_status`.
- Workflow recipes for plan gates, research verification, workspace review,
  and high-stakes panels.

### Security

- Launches Grok with a minimal environment and removes provider credentials,
  endpoint overrides, fetch proxies, and external authentication commands.
- Uses a temporary isolated `HOME` and `GROK_HOME` containing only a private,
  mode-`0600` copy of the existing grok.com login.
- Adds explicit no-subagent/no-plan controls, role turn ceilings, comprehensive
  tool removal, permission denies, bounded inline self-checks for high-risk
  roles, and feature-level write/MCP/memory disabling. Grok CLI 0.2.99 rejects
  its `--check` flag when paired with explicit `--no-subagents`, so the plugin
  preserves the stronger no-subagent boundary and reports that compatibility
  state in status.
- Adds bounded stdout/stderr capture and complete process-group termination on
  timeout or output overflow.
- Handles MCP SIGTERM/SIGINT by cancelling active Grok process groups and
  removing prompt files plus temporary credential/runtime data.
- Verifies pinned Grok role profiles by path, file type, permissions, required
  frontmatter, and SHA-256 digest.
- Requires every inspected Grok configuration layer to remain inside the fresh
  temporary Grok home so local model or provider mappings cannot enter the
  isolated child route.

### Changed

- Requires Grok CLI 0.2.99 or newer.
- Keeps single-pass review as the default; panels must be explicit and are
  capped at three calls.
- Expands the MCP surface from four to six read-only tools.
- Updates package documentation and metadata for version 0.2.0.

## 0.1.0 - 2026-07-12

- Initial Grok-only Codex plugin with tool-free consultation, bounded web
  research, read-only workspace review, and no-model status checks.
