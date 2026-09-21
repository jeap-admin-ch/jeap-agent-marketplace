# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

