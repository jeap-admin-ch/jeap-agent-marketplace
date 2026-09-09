# Maintaining the jEAP Agent Marketplace

Internal documentation for the **jEAP team** — how the marketplace is structured, how each agent
is wired up, and how to add or release a plugin. End-user documentation lives in the
[root README](../README.md) and each plugin's own README.

## Repository layout

```
jeap-agent-marketplace/
├── README.md                              # user docs (marketplace level)
├── docs/
│   └── maintaining.md                     # this file
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
        ├── .claude-plugin/plugin.json       # Copilot / Claude plugin manifest
        ├── .codex-plugin/plugin.json        # Codex plugin manifest
        ├── .opencode-plugin/opencode.json   # OpenCode MCP snippet (merged manually)
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
| OpenCode | — (no marketplace) | — | `.opencode-plugin/opencode.json` (merged by hand) | `skills/` (copied by hand) |

Copilot and Claude Code share the `.claude-plugin/` manifests and `.mcp.json`. Codex uses its own
manifests under `.agents/` and `.codex-plugin/`. OpenCode has no marketplace or plugin manifest and
is configured manually.

## Adding a new plugin

1. Create `plugins/<plugin-name>/` with:
   - `.claude-plugin/plugin.json` (Copilot / Claude), `.codex-plugin/plugin.json` (Codex), and
     `.opencode-plugin/opencode.json` (OpenCode MCP snippet) manifests.
   - `.mcp.json` and/or `.codex-mcp.json` if the plugin ships an MCP server.
   - `skills/<skill-name>/SKILL.md` for each skill.
   - A `README.md` describing the plugin (what it's for, what it installs, how/when to use each
     thing), following the pattern of `jeap-dev-toolkit`.
2. Register it in **both** marketplace manifests, pointing each `source` at the plugin directory:
   - [`.claude-plugin/marketplace.json`](../.claude-plugin/marketplace.json) (Copilot / Claude)
   - [`.agents/plugins/marketplace.json`](../.agents/plugins/marketplace.json) (Codex)
3. Add a row to the **Available plugins** table in the [root README](../README.md#2-available-plugins),
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

## Updating / releasing a plugin

1. Bump `version` in **both** plugin manifests (`.claude-plugin/plugin.json` and
   `.codex-plugin/plugin.json`).
2. Keep the version in the Claude/Copilot marketplace entry
   (`.claude-plugin/marketplace.json`) in sync.
3. Commit and push to the marketplace repository on GitHub
   (`jeap-admin-ch/jeap-agent-marketplace`).

Consumers then update with:

| Agent | Update command |
| ----- | -------------- |
| GitHub Copilot CLI | `copilot plugin update <plugin-name>` (or `copilot plugin update --all`) |
| Claude Code | `/plugin marketplace update jeap`, then apply updates from the `/plugin` UI |
| OpenAI Codex CLI | `codex plugin marketplace upgrade jeap` (re-run `codex plugin add <plugin-name>@jeap` if the version doesn't refresh) |
| OpenCode | refresh the local clone, re-copy `skills/`, re-merge the MCP snippet if it changed |

## License

Apache-2.0
