// OpenCode plugin for jeap-dev-toolkit.
//
// Registers the jeap-mcp-service MCP server and this package's skills directly in OpenCode's
// runtime catalog via ctx.mcp.transform / ctx.skill.transform (see
// https://opencode.ai/v2/docs/build/plugins/server/#mcp-servers and #skills). No config file
// merging and no copying skill folders into a scanned directory — OpenCode reconciles both on
// every launch, and a user's own `jeap-mcp-service` entry (if they set one) is left alone.
//
// Requires OpenCode >= 2.0.27 (first release with ctx.mcp.transform / ctx.skill.transform).
//
// Modeled on the first-party example at
// https://github.com/anomalyco/opencode/blob/dev/packages/plugins/figma/index.ts

import { Plugin, Skill } from "@opencode/plugin"
import { readdir, readFile } from "node:fs/promises"
import { fileURLToPath } from "node:url"

const MCP_SERVER_NAME = "jeap-mcp-service"
const MCP_SERVER_CONFIG = {
  type: "remote" as const,
  url: "https://jeap-mcp.bit.admin.ch/jeap-mcp-service/mcp",
  enabled: true,
}

// plugins/jeap-dev-toolkit/skills/ — a sibling of this .opencode-plugin/ directory, shared with
// this repo's other distribution formats (.claude-plugin, .codex-plugin).
const SKILLS_DIR = new URL("../skills/", import.meta.url)

// Minimal frontmatter reader — intentionally not a YAML library. This repo's SKILL.md frontmatter
// is authored by us and is always a flat block of `key: value` scalars (see
// docs/maintaining.md#skill-authoring-constraints-portability); a full YAML parser (e.g.
// gray-matter, which pulls in js-yaml) is more dependency than that needs, and js-yaml's 3.x line
// has had repeated advisories (code injection, prototype pollution, several DoS issues) over the
// years. Unknown/unsupported syntax (lists, block scalars, nested maps) is simply left as the raw
// string value, which is safe here since every value we read is used as a plain string anyway.
function parseFrontmatter(raw: string): { data: Record<string, string | boolean>; content: string } {
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(raw)
  if (!match) return { data: {}, content: raw }
  const [, frontmatter, content] = match
  const data: Record<string, string | boolean> = {}
  for (const line of frontmatter.split(/\r?\n/)) {
    const lineMatch = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line)
    if (!lineMatch) continue
    const [, key, rawValue] = lineMatch
    data[key] = parseScalar(rawValue)
  }
  return { data, content }
}

function parseScalar(raw: string): string | boolean {
  const trimmed = raw.trim()
  if ((trimmed.startsWith('"') && trimmed.endsWith('"')) || (trimmed.startsWith("'") && trimmed.endsWith("'"))) {
    return trimmed.slice(1, -1)
  }
  if (trimmed === "true") return true
  if (trimmed === "false") return false
  return trimmed
}

async function loadSkills() {
  const entries = await readdir(SKILLS_DIR, { withFileTypes: true })
  const skills = await Promise.all(
    entries
      .filter((entry) => entry.isDirectory())
      .map(async (entry) => {
        const file = new URL(`${entry.name}/SKILL.md`, SKILLS_DIR)
        let raw: string
        try {
          raw = await readFile(file, "utf8")
        } catch {
          return undefined // not a skill folder (no SKILL.md)
        }
        const { data, content } = parseFrontmatter(raw)
        const name = typeof data.name === "string" ? data.name : entry.name
        return Skill.Info.make({
          id: Skill.ID.make(name),
          name: Skill.Name.make(name),
          description: typeof data.description === "string" ? data.description : "",
          autoinvoke: data["disable-model-invocation"] !== true,
          path: Skill.Info.fields.path.make(fileURLToPath(file)),
          content: content.trim(),
        })
      }),
  )
  return skills.filter((skill): skill is NonNullable<typeof skill> => skill !== undefined)
}

export default Plugin.define({
  id: "jeap-dev-toolkit",
  async setup(ctx) {
    await ctx.mcp.transform((editor) => {
      if (editor.get(MCP_SERVER_NAME)) return // don't clobber a user's own override
      editor.set(MCP_SERVER_NAME, MCP_SERVER_CONFIG)
    })

    const skills = await loadSkills()
    await ctx.skill.transform((editor) => {
      for (const skill of skills) {
        if (!editor.get(skill.id)) editor.add(skill)
      }
    })
  },
})
