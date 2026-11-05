# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.12.0] - 2026-11-05

### Added

- jeap-dev-toolkit: a real OpenCode plugin (`plugins/jeap-dev-toolkit/.opencode-plugin/index.ts`),
  published to the public npm registry as `@jeap/jeap-opencode-plugin`. It registers the
  `jeap-mcp-service` MCP server and all bundled skills directly in OpenCode's runtime catalog via
  `ctx.mcp.transform`/`ctx.skill.transform` — no manual config merge or skill-folder copying.
  Install with `opencode plugin add @jeap/jeap-opencode-plugin`. Requires OpenCode v2.0.27+;
  OpenCode v1 is not supported.
- `.github/workflows/opencode-plugin-release.yml`: builds, smoke-tests (against both the oldest
  supported and newest `@opencode/plugin` version), and publishes the OpenCode plugin via npm
  Trusted Publishing (OIDC) on merge to `main`.

## [0.11.1] - 2026-11-04

### Security

- `plain-fixture` (eval fixture): override the versions of transitive dependencies that Spring Boot 4.1.2 (the latest
  release) still resolves with known vulnerabilities: `tools.jackson.core:jackson-databind` 3.1.8 (was 3.1.7,
  CVE-2026-110879) and the `kafka-clients` compression libraries `zstd-jni` 1.5.7-24 (was 1.5.6-10), `lz4-java` 1.12.1
  (was 1.11.2) and `snappy-java` 1.1.10.12 (was 1.1.10.7), aligned with `jeap-spring-boot-parent` 41.29.1.

## [0.11.0] - 2026-10-29

### Security

- Updated the Maven parents of the `jeap-expert` eval fixtures, which Snyk reported with many known vulnerabilities:
  `jeap-fixture` uses `jeap-spring-boot-parent` 41.29.0 (was 26.4.0) and `plain-fixture` uses Spring Boot 4.1.2 (was 3.4.5).
  The fixtures are only test projects for the evals and are not part of any plugin.

### Added

- Renovate configuration (`renovate.json`, based on the shared jEAP `default-oss` preset) to keep the fixture
  dependencies up to date.

## [0.10.0] - 2026-10-12

### Added

- `migrate-confluence-docs`: a standalone skill to migrate selected Confluence pages into repository
  Markdown, preserving Draw.io/PlantUML sources, generating SVGs, maintaining the arc42 glossary,
  and producing a migration report with links to review.

## [0.9.0] - 2026-08-05

### Added

- jeap-dev-toolkit: a `jeap-expert` skill that answers jEAP questions as a jEAP specialist — every
  jEAP-specific claim has to be looked up via the `jeap_*` tools of the jEAP MCP server and cited,
  and questions are framed by what the platform already provides (its libraries, starters, reusable
  microservices, tooling and conventions) before any generic Spring/Java answer. It also defines what
  makes a project **jEAP-based** so the lens is applied where it belongs, and covers **setting a
  project up as jEAP-based** (greenfield via `jeap-initializer`, an existing project via the parent
  plus the starters and libraries it actually needs).
- Documentation for using the skill per CLI (root and plugin README), and maintainer notes on
  verifying plugin components without a full agent session.
- Maintainer tooling: trigger evals for the `jeap-expert` skill (`evals/jeap-expert/` — Claude Code
  trigger/grounding measurement and a Copilot with/without-skill A/B harness, with reference results
  from 2026-08-05).

### Changed

- jeap-dev-toolkit (Codex manifest): the interface descriptions and the first starter prompt now
  cover jEAP question answering, not only the MCP server and the migration.

## [0.8.3] - 2026-06-08

### Added

- migrate-spring-boot-4 skill: a preflight check that confirms the target is a jEAP Spring Boot 3 Maven
  project before running the (long) baseline build or the migration.

### Changed

- migrate-spring-boot-4 skill: the pre-migration clean-tree check now ignores the skill's own
  `.jeap-migration/` scratch via a scoped `git status` instead of writing to `.git/info/exclude`, so it
  works under agent sandboxes that protect `.git/` (e.g. Codex `workspace-write`); the scratch is now
  removed before hand-off so it can't be committed by accident.
- migrate-spring-boot-4 skill: hardened the final step so agents reliably write the
  `spring-boot-4-migration.md` report file. Writing the report is now part of the definition of "done",
  the finish step is broken into ordered sub-steps with the report written before the scratch is deleted
  and the chat summary, an explicit on-disk check gates hand-off.
- jeap-dev-toolkit (Codex manifest): replaced the single vague `defaultPrompt` starter with three
  focused, plugin-named suggestions — jEAP API / example-code lookup, the Spring Boot 4 migration, and
  jEAP CLI install — so the install-time starter prompts reflect what the plugin actually does.

### Fixed

- migrate-spring-boot-4 skill: the `jeap-run.sh` build helper no longer errors when polled before a build
  is started, refuses to start a second build while one is still running (which could corrupt the shared
  log and exit files), and reports a build that was killed instead of polling "still running" forever.

## [0.8.2] - 2026-06-02

### Changed

- migrate-spring-boot-4 skill: write the migration summary to `spring-boot-4-migration.md` at the
  project root so it remains reviewable after the agent session ends.
- migrate-spring-boot-4 skill: the agent no longer commits anything — the migration is left uncommitted
  in the working tree for the user to inspect, verify, and commit themselves.

## [0.8.1] - 2026-06-01

### Changed

- migrate-spring-boot-4 skill: dropped the OpenRewrite host-fallback workaround as this has now been fixed in
  the jEAP CLI migrate spring-boot-4 command.

## [0.8.0] - 2026-06-01

### Added

- jeap marketplace definition
- jeap-dev-toolkit plugin
- documentation for the marketplace and the plugin

