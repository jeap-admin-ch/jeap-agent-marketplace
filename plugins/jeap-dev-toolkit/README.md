# jEAP Dev Toolkit Plugin

An agent plugin — for **GitHub Copilot CLI**, **Claude Code**, and **OpenAI Codex CLI** (and
usable with **OpenCode** via manual setup) — that bundles everything a jEAP (Java Enterprise
Application Platform) developer needs into one versioned, easy-to-update package.

> **Installing, updating, and removing** this plugin is the same for every plugin in the
> marketplace and is documented once in the
> [marketplace README](../../README.md#1-add-the-marketplace). This page describes **what the
> plugin provides and how to use it.**

## What it's for

Working on a jEAP-based project from an AI agent has two recurring frictions: the agent's built-in
knowledge of the fast-moving jEAP platform is stale or absent, and platform chores (installing
the CLI, running a major Spring Boot migration) are multi-step and easy to get wrong. This plugin
addresses both — it connects the agent to an authoritative, **version-aware** view of jEAP, tells it
to actually *use* that view, and ships **skills** that carry the chores end to end.

**Benefits:**

- The agent answers jEAP questions from the **real, current** jEAP source and examples instead of
  guessing or relying on outdated memory — the `jeap-expert` skill makes that a rule rather than
  a hope.
- Common platform tasks become a single request — the skill knows the correct sequence, the
  pitfalls, and how to verify success.
- It's **one versioned package**: install once, update in place, identical behaviour across all
  supported agents.

## What it installs

| Thing | Type | Short description |
| ----- | ---- | ----------------- |
| `jeap-mcp-service` | MCP server | A version-aware view of the jEAP platform: semantic search over jEAP source/examples, symbol lookup, and the platform overview. |
| [`jeap-expert`](#skill-jeap-expert) | skill | Answers jEAP questions from the indexed jEAP documentation and source instead of model memory, and from a jEAP point of view. |
| [`install-jeap-cli`](#skill-install-jeap-cli) | skill | Installs or updates the jEAP CLI (the `jeap` command). |
| [`migrate-spring-boot-4`](#skill-migrate-spring-boot-4) | skill | Upgrades a jEAP Spring Boot 3 Maven project to Spring Boot 4 and drives it back to green. |
| [`migrate-confluence-docs`](#skill-migrate-confluence-docs) | skill | Migrates selected Confluence architecture pages to Markdown, preserving diagram sources and maintaining the glossary. No jEAP tooling dependency. |

### MCP server: `jeap-mcp-service`

Auto-registered on install, this MCP server gives the agent `jeap_*` tools — semantic search over
indexed jEAP source and examples (`jeap_find_code_examples`), symbol definition/reference lookup
(`jeap_find_definition`, `jeap_find_references`), the jEAP umbrella overview (`jeap_overview`), and
more. Agents may reach for them on their own when you ask about jEAP packages, classes, starters,
messaging/inbox/outbox or configuration — but whether they do is a judgement call the model makes,
which is exactly what the [`jeap-expert`](#skill-jeap-expert) skill takes out of its hands. The
`migrate-spring-boot-4` skill uses the same tools to resolve moved or renamed jEAP APIs.

In **Copilot CLI** the server's own usage instructions never reach the model by default: Copilot
includes MCP server instructions in the prompt only for servers on its internal allowlist and marks
all others as *deferred* (verified with Copilot CLI 1.0.78 debug logs; `--allow-tool` does not change
this). The `jeap-expert` skill exists to carry those instructions anyway. Optionally, start
Copilot with `--allow-all-mcp-server-instructions` to inject the server instructions too — and for
scripted `copilot -p` runs, pre-approve the read-only jEAP tools with `--allow-tool jeap-mcp-service`,
since non-interactive runs cannot ask and silently deny them otherwise.

It is a public, hosted service — no token or login is required. Confirm it is connected with the
in-session `/mcp` command (Copilot, Claude Code, Codex, OpenCode) — look for `jeap-mcp-service`.
In Copilot, plugin-bundled MCP servers appear only under `/mcp` inside a session, **not** in the
`copilot mcp list` shell command.

<a id="skill-jeap-expert"></a>
### Skill: `jeap-expert`

Makes the agent answer jEAP questions **as a jEAP specialist**: grounded in the indexed jEAP
documentation and source via the `jeap_*` tools rather than in model memory, and framed by what the
platform already provides.

**When it runs:** whenever you mention jEAP, a `jeap-*` repository, or a jEAP concept (messaging,
transactional outbox, sequential inbox, message type registry, audit, error handling, archrepo,
deploymentlog, governance, …) — and whenever a task in a jEAP-based project might already be solved by a
jEAP building block. Invoke it explicitly if it does not fire on its own — in Claude Code:
`/jeap-dev-toolkit:jeap-expert`; in Codex CLI: `$jeap-expert` in the prompt; in Copilot CLI and
OpenCode: name it in the prompt (*"use the jeap-expert skill: …"*).

**What it changes.** Without it, an agent answers a jEAP question the way it answers any Spring
question: from memory, in generic Spring terms, and only reaching for the `jeap_*` tools if it happens
to think of them. The skill makes three things binding:

- **Look it up, always.** Every jEAP-specific claim — a class, a starter, a property, a default, a
  version — has to come from a tool call in that session, and sources are cited so you can check them.
  Unverified general Spring knowledge must be labelled as such.
- **Ask the platform question first.** Before proposing a library or a hand-rolled solution, check the
  four families of jEAP building blocks (libraries, Spring Boot starters, reusable microservices,
  tooling & registries) and the conventions around them, and answer for the jEAP version *this*
  project actually uses.
- **Say so when jEAP has nothing.** "There is no jEAP building block for this" is only allowed after
  searching, and comes with the generic answer plus a pointer to report the gap to the jEAP team.

It also knows **what a jEAP-based project is** so the jEAP lens is applied where it belongs and not forced
on projects that do not use the platform. And it treats **"set this project up with jEAP"** as a task
of its own: greenfield goes through `jeap-initializer`, an existing project gets the parent (with the
current version looked up rather than remembered) plus the starters and libraries that service
actually needs, including what jEAP would replace in what is already there.

<a id="skill-install-jeap-cli"></a>
### Skill: `install-jeap-cli`

Installs or updates the jEAP CLI — the `jeap` command used for jEAP automation such as the Spring
Boot migration.

**When it runs:** when the `jeap` command is missing, when you explicitly ask to install/update it,
or automatically as a prerequisite of another skill (e.g. `migrate-spring-boot-4`).

**What to expect:** it asks which install scope to use — **user** (`~/.local/bin/jeap`, no `sudo`,
the recommended default) or **global** (`/usr/local/bin/jeap`, needs `sudo` which you run
yourself) — and verifies the install afterward. It will not proceed if a prerequisite is missing.

<a id="skill-migrate-spring-boot-4"></a>
### Skill: `migrate-spring-boot-4`

Upgrades a jEAP Spring Boot 3 Maven project to Spring Boot 4 using `jeap migrate spring-boot-4`,
then iterates — fixing the compilation and test failures the automated step can't handle — until
the project builds and **all tests pass**.

**When to use it:** ask to “migrate/upgrade this jEAP project to Spring Boot 4.” It requires the
jEAP CLI (it will use `install-jeap-cli` if needed) and a clean, fully-tested Spring Boot 3
baseline to start from.

**What to expect during the migration:**

Building and testing a jEAP-based project — especially the integration tests, which boot Spring contexts
and Testcontainers — can take **10–20 minutes or more**, and the migration runs the build several
times. To keep these verbose, debug-level logs from crowding the agent's working memory (which
would degrade its reasoning and slow the migration), the skill redirects all build output to
`.jeap-migration/run.log` and reads back only a short summary. As a result:

- **Long quiet stretches are normal.** Builds run detached, and the agent only posts a short
  `still running (~6m elapsed)` / `maven_exit=<code>` line every ~20 s. This does **not** mean it
  has hung; it is waiting for Maven. Please don't stop it mid-build.
- **To watch live progress,** open a second terminal in the project and run
  `tail -f .jeap-migration/run.log`. You'll see the full Maven and test output in real time while
  the agent's own view stays uncluttered.
- **On GitHub Copilot CLI, approve the build command once "for the session".** Builds go through a
  small fixed helper script (`.jeap-migration/jeap-run.sh`). The first time the agent runs it,
  Copilot asks for approval — choose **"Yes, and approve … for the rest of the running session"**.
  That remembers only this one command, so every later build and poll runs unattended while every
  other shell command still asks — you never have to grant blanket shell access, and you won't be
  prompted on every poll. The helper itself only ever runs Maven (`mvn`/`./mvnw`) and refuses any
  other command, so approving it for the session can't be turned into arbitrary shell access.
  (Claude Code and Codex don't need this; they don't prompt per build.)
- **`.jeap-migration/` is scratch** — the skill creates it for these logs and the helper, keeps the
  clean-tree check scoped so this untracked scratch does not count as user work, and deletes it before
  handoff. Do not add it to `.gitignore`; that would add noise to the migration diff.

The skill resolves moved/renamed jEAP APIs via the bundled `jeap-mcp-service`, and consults the
official Spring/Hibernate migration guides (links curated in the skill) for framework changes. If a
general documentation-retrieval MCP such as [Context7](https://context7.com) is configured in your
agent, the skill will use it too — but it is optional, not required.

### Skill: `migrate-confluence-docs`

Use this to move system architecture documentation from Confluence Data Center into a chosen
repository folder. It works independently of the jEAP expert skill, CLI and MCP server and has
no team-specific spaces, terminology or publishing rules.

For example: *“Migrate Confluence pages 12345 and 12346 from https://wiki.example.org/confluence
into docs/architecture. Page 12346 is our chapter 12 glossary.”* In Claude Code, invoke
`/jeap-dev-toolkit:migrate-confluence-docs`; in other agents, describe the task or select the skill.
It is included from plugin version 0.10.0. If it is absent from the active skill catalog, check the installed
version/location and refresh the session, or load the checked-out plugin explicitly.

The skill produces finished documentation following the target repository's structure, language,
frontmatter and publishing conventions. It retains Draw.io sources as `.drawio` with matching `.svg`
images and PlantUML sources as `.puml`, and maintains the existing arc42 chapter 12 glossary. A single
`migration-report.md` outside the published documentation contains the summary, source/target mapping,
validation results and links needing review. Raw XML, JSON manifests, preview images and conversion
diagnostics remain in a temporary working directory.
Local helpers seed a structural coverage checklist, preserve Jira keys/queries without fetching them,
assemble every source/target link into a deduplicated table, and block the documented cleanup workflow
while review or diagram checks remain unresolved. These checks do not replace editorial or publisher validation.
For renderer prerequisites, an installation smoke test and a local image build recipe, see
[renderer setup](skills/migrate-confluence-docs/references/renderer-setup.md).

Only explicitly selected pages and their attachments are read. Descendants are included only when
the user explicitly requests that page tree; included pages and ordinary links do not expand scope.
The helper automates that discovery with `--include-descendants`, bounded by `--max-pages` (100) and
`--max-depth` (20), and records the hierarchy and any incomplete branches in its working manifest.
Unsupported content and missing diagram renderers are reported for follow-up. Information and
requirements are retained, even when pages are reorganised or merged into the target's chapters.

For repositories published by the Doc Service, the skill reads the documentation set's configuration,
fits pages into its chapters, respects supported metadata and generated-page names, and checks which
diagram files may enter the upload bundle. This is conditional guidance, not a dependency on another
jEAP skill or a fixed repository. See
[target repository conventions](skills/migrate-confluence-docs/references/target-repository.md).
An existing or explicitly supplied arc42 structure determines the destinations; one migration can span
several chapters. If requested, the agent additionally retrieves Doc-Service rules via MCP and compares
them with the target's version and configuration. Without MCP, local reconciliation remains possible and
the unavailable rule check is reported. Only general platform questions are sent to MCP.

Anonymous access is tried first. If required, provide your Confluence PAT locally through
`CONFLUENCE_PAT_FILE` (a raw-token file outside the repository) or `CONFLUENCE_TOKEN`.
Never paste the token into the agent conversation. See the
[skill](skills/migrate-confluence-docs/SKILL.md) and
[operational notes](skills/migrate-confluence-docs/references/operations.md) for details.

## Requirements

| Requirement                                           | Needed for |
|-------------------------------------------------------| ---------- |
| **Docker** (running)                                  | the jEAP CLI — some `jeap` commands may run in a container |
| `curl`, and `script` (util-linux / built-in on macOS) | the jEAP CLI installer |
| `sudo` (your password)                                | **global** jEAP CLI install only |
| **JDK 25**                                            | the Spring Boot 4 build — jEAP's SB4 baseline is Java 25; the migrated code will not compile on an older JDK |
| Linux or macOS                                        | the jEAP CLI |
| Python 3.10+ (standard library only), network access to the selected Confluence instance | Confluence export; developer PAT only if required |
| Draw.io Desktop with bubblewrap and Xvfb on Linux, or Docker with an existing local image containing `xvfb-run` and `drawio` | Offline Draw.io SVG generation |

## Contents

```
jeap-dev-toolkit/
├── .claude-plugin/plugin.json        # Copilot / Claude plugin manifest
├── .codex-plugin/plugin.json         # Codex plugin manifest
├── .opencode-plugin/opencode.json    # OpenCode MCP snippet (merged manually)
├── .mcp.json                         # jEAP MCP server (Copilot / Claude)
├── .codex-mcp.json                   # jEAP MCP server (Codex)
└── skills/
    ├── jeap-expert/SKILL.md            # Answer jEAP questions from the indexed jEAP docs and source
    ├── install-jeap-cli/SKILL.md       # Install / update the jEAP CLI
    ├── migrate-spring-boot-4/SKILL.md  # Spring Boot 3 → 4 migration
    └── migrate-confluence-docs/      # Confluence → Markdown, sources, SVGs and glossary
        ├── SKILL.md
        ├── scripts/                 # Export, offline rendering, validation and review helpers
        ├── references/              # Target publishing conventions and operations
        └── tests/                   # Offline behavioural tests
```

For how these manifests are wired per agent, and how to extend the marketplace, see
[`docs/maintaining.md`](../../docs/maintaining.md).

## License

Apache-2.0
