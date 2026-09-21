# OpenCode (manual setup)

OpenCode has no marketplace or plugin-install command, so the MCP server and skills are added by
hand from a local copy of this repository. Clone or otherwise obtain the repo first, then:

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

3. **Verify** — run `opencode` and use `/mcp` to confirm `jeap-mcp-service` is connected; the
   skills load automatically when their description matches your request.

To **update** later, refresh your local copy of this repository, re-copy the skills, and re-merge
the MCP snippet if it changed. See the [OpenCode skills docs](https://opencode.ai/docs/skills) and
[MCP docs](https://opencode.ai/docs/mcp-servers) for the full list of scanned locations.
