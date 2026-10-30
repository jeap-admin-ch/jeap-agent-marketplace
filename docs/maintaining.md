# Maintaining the jEAP Agent Marketplace

Internal documentation for the **jEAP team** — how the marketplace is structured, how each agent
is wired up, and how to add or release a plugin. End-user documentation lives in the
[root README](https://github.com/jeap-admin-ch/jeap-agent-marketplace#readme) and each plugin's own README.

## Repository layout

```
jeap-agent-marketplace/
├── README.md                              # user docs (marketplace level)
├── docs/
│   └── maintaining.md                     # this file
├── .github/
│   └── workflows/
│       └── opencode-plugin-release.yml    # builds + publishes @jeap/jeap-dev-toolkit on merge to main
├── evals/
│   └── jeap-expert/                       # trigger evals for the jeap-expert skill (see its README)
├── .claude-plugin/
│   └── marketplace.json                   # Copilot / Claude marketplace manifest
├── .agents/
│   └── plugins/
│       └── marketplace.json               # Codex marketplace manifest
└── plugins/
    └── jeap-dev-toolkit/                   # a plugin
        ├── README.md                       # user docs (plugin level)
        ├── package.json                    # OpenCode plugin package (name + exports; files: .opencode-plugin, skills)
        ├── .claude-plugin/plugin.json       # Copilot / Claude plugin manifest
        ├── .codex-plugin/plugin.json        # Codex plugin manifest
        ├── .opencode-plugin/index.ts         # OpenCode plugin: registers jeap-mcp-service + skills at runtime
        ├── .mcp.json                        # jEAP MCP server (Copilot / Claude)
        ├── .codex-mcp.json                  # jEAP MCP server (Codex)
        └── skills/                          # bundled skills (each: SKILL.md + optional scripts/, references/)
```

## How each agent is wired up

All agents share the same `skills/` folders and the same MCP server URL; they differ only in which
manifest files they read.

| Agent | Marketplace manifest | Plugin manifest | MCP config | Skills |
| ----- | -------------------- | --------------- | ---------- | ------ |
| GitHub Copilot CLI | `.claude-plugin/marketplace.json` | `.claude-plugin/plugin.json` | `.mcp.json` | `skills/` |
| Claude Code | `.claude-plugin/marketplace.json` | `.claude-plugin/plugin.json` | `.mcp.json` | `skills/` |
| OpenAI Codex CLI | `.agents/plugins/marketplace.json` | `.codex-plugin/plugin.json` | `.codex-mcp.json` (via `mcpServers` field in the plugin manifest) | `skills/` (via `skills` field) |
| OpenCode | — (no marketplace) | `package.json` (name + `exports`) | `.opencode-plugin/index.ts` via `ctx.mcp.transform` | `.opencode-plugin/index.ts` via `ctx.skill.transform`, reading `skills/*/SKILL.md` |

Copilot and Claude Code share the `.claude-plugin/` manifests and `.mcp.json`. Codex uses its own
manifests under `.agents/` and `.codex-plugin/`. OpenCode has no marketplace, but it is a real
[OpenCode plugin](https://opencode.ai/v2/docs/build/plugins/server/): `.opencode-plugin/index.ts`'s
`setup(ctx)` calls `ctx.mcp.transform` to register `jeap-mcp-service` and `ctx.skill.transform` to
register each skill from `skills/*/SKILL.md`, both reconciled on every OpenCode launch — no config
merge or file copy needed. It is published to the public npm registry as
[`@jeap/jeap-dev-toolkit`](https://www.npmjs.com/package/@jeap/jeap-dev-toolkit) via
[`.github/workflows/opencode-plugin-release.yml`](../.github/workflows/opencode-plugin-release.yml)
using npm Trusted Publishing (OIDC) — see [`docs/opencode-setup.md`](opencode-setup.md) for the
install instructions. Requires OpenCode **v2.0.27+**
(`ctx.mcp.transform`/`ctx.skill.transform`); OpenCode v1 is unsupported.

## Adding a new plugin

1. Create `plugins/<plugin-name>/` with:
   - `.claude-plugin/plugin.json` (Copilot / Claude) and `.codex-plugin/plugin.json` (Codex)
     manifests.
   - A `package.json` (`name: "@jeap/<plugin-name>"`, `exports: { ".": "./.opencode-plugin/index.ts" }`,
     `files: [".opencode-plugin", "skills"]`, `publishConfig: { "access": "public" }`) and
     `.opencode-plugin/index.ts` for OpenCode. Copy `jeap-dev-toolkit`'s version of both and
     adjust the MCP server name/URL and the package name — the skill-loading part is generic
     (reads every `skills/*/SKILL.md` in the plugin's own folder), so it needs no changes.
     To publish it to npm too, extend `.github/workflows/opencode-plugin-release.yml` with this
     plugin's own `PLUGIN_NAME`/`PLUGIN_ROOT` jobs (or copy the workflow under a new name) and
     give it its own `<plugin-name>-v*`-style tag prefix. The one-time npm/GitHub setup this needs
     is maintainer-internal, not documented in this repo.
   - `.mcp.json` and/or `.codex-mcp.json` if the plugin ships an MCP server.
   - `skills/<skill-name>/SKILL.md` for each skill.
   - A `README.md` describing the plugin (what it's for, what it installs, how/when to use each
     thing), following the pattern of `jeap-dev-toolkit`.
2. Register it in **both** marketplace manifests, pointing each `source` at the plugin directory:
   - [`.claude-plugin/marketplace.json`](../.claude-plugin/marketplace.json) (Copilot / Claude)
   - [`.agents/plugins/marketplace.json`](../.agents/plugins/marketplace.json) (Codex)
3. Add a row to the **Available plugins** table in the
   [root README](https://github.com/jeap-admin-ch/jeap-agent-marketplace#2-available-plugins),
   and a per-plugin summary section.

### Skill authoring constraints (portability)

Skills must work across all four agents, so prefer agent-agnostic mechanisms (plain shell, file
reads, MCP tools) and avoid hard dependencies on any one agent's features. Each `SKILL.md` needs
`name`/`description` frontmatter; for OpenCode compatibility the `name` must be lowercase,
hyphen-separated, and **match the skill's folder name** (regex `^[a-z0-9]+(-[a-z0-9]+)*$`).

**Shipping a helper script.** A non-trivial helper (e.g. `migrate-spring-boot-4`'s `jeap-run.sh`)
is bundled as a real file under the skill's `scripts/` directory rather than embedded in `SKILL.md`
as a code block the model retypes — so it can be reviewed, linted, and tested, and so a
security-relevant guardrail can't be silently dropped during reconstruction. There is no portable
placeholder for the skill's install path across all four agents (only Claude Code exports
`${CLAUDE_PLUGIN_ROOT}`; Codex's `PLUGIN_ROOT` is hooks-only; Copilot and OpenCode have none), so
`SKILL.md` instructs the agent to **copy the bundled script into the project's working directory
and run it from there** rather than executing it in place. That keeps the run command identical
across agents, keeps the script's scratch files in a writable location, and means no agent ever
has to execute a file out of its (possibly read-only) plugin cache. Invoke bundled scripts with an
explicit interpreter (`sh script.sh`), since the executable bit is not guaranteed to survive
install/clone on every agent.

**The jEAP-expert tool table tracks a remote server.** The tool table in
`skills/jeap-expert/SKILL.md` describes the tool surface of `jeap-mcp-service` — a **remote**
server, whose tools can change without a plugin release. When the server's tool surface or
behaviour changes (a new tool, a removed filter, changed required parameters), update the skill and
release the plugin, or its guidance silently goes stale.

**Verifying plugin components without a full session.** `claude plugin details <plugin>` (after
installing the plugin, optionally into a throwaway `CLAUDE_CONFIG_DIR`) prints the component
inventory and the projected token cost of every skill. For Copilot,
`copilot --plugin-dir ./plugins/jeap-dev-toolkit -p "…"` loads a plugin straight from disk without
installing it. For Codex, `CODEX_HOME=<tmp> codex plugin marketplace add . && codex plugin add
<plugin>@jeap` installs into a throwaway home, and `codex debug prompt-input` renders what the model
actually sees (skills included).

**Measuring skill triggering.** [`evals/jeap-expert/`](../evals/jeap-expert/README.md) holds a
reusable trigger-eval set (realistic should/shouldn't-trigger queries against jEAP and plain-Spring
fixtures) with runners for Claude Code and a Copilot with/without-skill A/B, plus reference results.
Re-run it after changing the skill's description or body.

**Testing Confluence migration helpers.** Run
`python3 -m unittest discover -s plugins/jeap-dev-toolkit/skills/migrate-confluence-docs/tests -v`.
The tests use local fixtures and a mocked renderer: they require neither Confluence credentials
nor Draw.io. Regression coverage includes bounded subtree discovery, failed-page links, Markdown without
raw HTML, admonitions, expected diagram inventories, source/SVG hash checks and embedded resource rejection.
They also cover PlantUML code-source retention, glossary row headers, diagram link inspection (including
compressed Draw.io tabs), list numbering, table pipes and the native renderer's restricted mount arguments.
Container regressions cover endpoint selection, rootless UID mapping and cleanup after timeouts, including
stopping on cleanup failure. Glossary regressions preserve literal markup and avoid duplicates on reimport.
Review-tool regressions cover complete deduplicated link rows, Jira identities without lookups, structural
coverage, retained review answers, cleanup blocking and read-only SVG/pair validation from arbitrary paths.
A release smoke test against explicitly authorised pages and a real offline Draw.io
installation should additionally check rendered diagram fidelity; the fixture tests do not prove it.
For a real container smoke test, set `CONFLUENCE_DRAWIO_TEST_IMAGE` to an already installed team-approved
image and run the same test command. `test_drawio_smoke.py` exercises the production renderer with two
local fixture tabs, checks their SVG text and source preservation, and verifies reuse. It is explicitly
skipped without that variable and never pulls an image. Run it on both ordinary Docker as a non-root user
and rootless Docker before claiming compatibility with both modes. This test needs no Confluence access.
The same installation can be checked before a migration with
`render_drawio.py --smoke-test --container-image <local-image>`. The optional image build recipe is in the skill's
`references/renderer-setup.md`; it is not an approved or prebuilt renderer distribution.

**Diagnosing a missing migration skill.** Version 0.10.0 includes
`skills/migrate-confluence-docs/SKILL.md`. Codex explicitly declares `./skills/`; Copilot/Claude use the
standard plugin `skills/` directory. There is no per-skill registration list to extend. For Copilot, check
`copilot plugin list --json` for the actual version and installation source, then update the installed
plugin or load the intended local plugin directory in a fresh session. A tool such as `functions.skill`
uses the host's active catalog, not every `SKILL.md` present in adjacent checkouts. See the
[Copilot plugin reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference).

To verify this checkout directly without installing it, run:

```bash
copilot --plugin-dir /path/to/jeap-agent-marketplace/plugins/jeap-dev-toolkit skill list --json
```

The result must contain `migrate-confluence-docs` with `source: plugin`, the intended checkout path and
`enabled: true`. Use the same `--plugin-dir` when starting a local test session.

## Updating / releasing a plugin

1. Bump `version` in **all three** of `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`,
   and `plugins/jeap-dev-toolkit/package.json` (the OpenCode npm package) — keep all three in
   lockstep, one version per plugin release.
2. Keep the version in the Claude/Copilot marketplace entry
   (`.claude-plugin/marketplace.json`) in sync.
3. Add a `CHANGELOG.md` entry.
4. Commit and push to the marketplace repository on GitHub
   (`jeap-admin-ch/jeap-agent-marketplace`); merging the version bump to `main` both makes the
   new version available to Copilot/Claude/Codex consumers and, via
   [`.github/workflows/opencode-plugin-release.yml`](../.github/workflows/opencode-plugin-release.yml),
   **publishes** `@jeap/jeap-dev-toolkit` to npm. A merge that does not bump
   `plugins/jeap-dev-toolkit/package.json`'s version is a no-op for npm (the release job sees the
   tag already exists and skips).

Consumers then update with:

| Agent | Update command |
| ----- | -------------- |
| GitHub Copilot CLI | `copilot plugin update <plugin-name>` (or `copilot plugin update --all`) |
| Claude Code | `/plugin marketplace update jeap`, then apply updates from the `/plugin` UI |
| OpenAI Codex CLI | `codex plugin marketplace upgrade jeap` (re-run `codex plugin add <plugin-name>@jeap` if the version doesn't refresh) |
| OpenCode | `opencode plugin update @jeap/jeap-dev-toolkit` |

## License

Apache-2.0
