# OpenCode (manual setup)

OpenCode has no marketplace or plugin-install command — [OpenCode "plugins"](https://opencode.ai/docs/plugins/)
are a different thing (JS/TS hook modules for events like `tool.execute.before`); they don't bundle
MCP servers or skills. So the jEAP MCP server and skills are added by hand from a local copy of
this repository, either with the script below or fully manually.

## Install OpenCode itself

If OpenCode isn't installed yet, or was installed via `npm`/`bun`, use the official install
script instead:

```bash
curl -fsSL https://opencode.ai/v2/install | bash
```

## Get the jEAP marketplace repo

Clone or otherwise obtain this repo:

```bash
git clone https://github.com/jeap-admin-ch/jeap-agent-marketplace.git
```

## Option A: run the setup script

[`plugins/jeap-dev-toolkit/.opencode-plugin/setup.py`](../plugins/jeap-dev-toolkit/.opencode-plugin/setup.py)
does both steps below for you: it merges the `mcp.jeap-mcp-service` entry into your OpenCode
config and copies the selected skill folders into the directory OpenCode scans for skills. It
needs only Python 3.8+ (standard library only) and makes no network calls.

```bash
cd jeap-agent-marketplace
python3 plugins/jeap-dev-toolkit/.opencode-plugin/setup.py --dry-run   # preview, writes nothing
python3 plugins/jeap-dev-toolkit/.opencode-plugin/setup.py             # apply, global scope (default)
```

Useful flags:

| Flag | Effect |
| ---- | ------ |
| `--dry-run` | Print what would change; writes nothing. Always safe to run first. |
| `--scope global` (default) | Target `~/.config/opencode/opencode.json` and `~/.config/opencode/skills/`. |
| `--scope project --project-dir <path>` | Target `<path>/opencode.json` and `<path>/.opencode/skills/` instead. |
| `--skills <a,b,...>` | Install only the named skills (default: `all`). |
| `--list-skills` | Print available skill names and exit; installs nothing. |

Re-running the script is always safe:

- the MCP merge only ever adds/updates the single `mcp.jeap-mcp-service` key — every other key in
  your `opencode.json`, including other MCP servers, is left untouched — and it writes a
  `opencode.json.bak` backup before changing an existing file;
- the skill copy only overwrites the folders it previously copied, so running it again (e.g. after
  pulling a newer checkout) is how you **update**.

## Option B: do it manually

1. **MCP server** — merge the `mcp` block from
   [`plugins/jeap-dev-toolkit/.opencode-plugin/opencode.json`](../plugins/jeap-dev-toolkit/.opencode-plugin/opencode.json)
   into your OpenCode configuration — `~/.config/opencode/opencode.json` (global) or
   `./opencode.json` (per project). OpenCode merges config files, but a single file can have only
   one top-level `mcp` object, so add the `jeap-mcp-service` key into your existing `mcp` block
   rather than pasting a second one.

2. **Skills** — copy the skill folders from `plugins/jeap-dev-toolkit/skills/` into a directory
   OpenCode scans, keeping one folder per skill with its `SKILL.md`. Common locations:
   `~/.config/opencode/skills/` (global) or `./.opencode/skills/` (per project). No registration
   is needed — placement is enough, and the bundled `name`/`description` frontmatter is already
   what OpenCode expects.

## Verify

Run `opencode` and use `/mcp` to confirm `jeap-mcp-service` is connected; the skills load
automatically when their description matches your request.

## Update later

Refresh your local copy of this repository, then either re-run the setup script (Option A) or
re-copy the skills and re-merge the MCP snippet by hand (Option B) if it changed. See the
[OpenCode skills docs](https://opencode.ai/docs/skills), [MCP docs](https://opencode.ai/docs/mcp-servers)
and [plugins docs](https://opencode.ai/docs/plugins/) for the full list of scanned locations and
what OpenCode plugins are actually for.
