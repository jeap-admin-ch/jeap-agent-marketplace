// CI-only smoke test for the packed @jeap/jeap-opencode-plugin tarball — not published (this file
// lives outside package.json's "files" allowlist). Run with Bun (not Node): the package ships
// .opencode-plugin/index.ts unbuilt, and Node refuses to type-strip *.ts files that live under
// node_modules (ERR_UNSUPPORTED_NODE_MODULES_TYPE_STRIPPING) — which is exactly how a real
// install of this package looks. Bun is the runtime this is actually built for: OpenCode itself
// runs plugins with its bundled Bun (see docs/opencode-setup.md), so this is what catches a
// broken `exports`/`files` entry or a skill that fails to load once this is a real dependency
// instead of a repo checkout with relative paths.
//
// Usage (see .github/workflows/opencode-plugin-release.yml "Smoke test the packed tarball"):
//   npm install --no-audit --no-fund ./jeap-jeap-opencode-plugin-*.tgz @opencode/plugin@<range>
//   bun run opencode-plugin-smoke-test.mjs

import plugin from "@jeap/jeap-opencode-plugin"

const EXPECTED_SKILLS = ["install-jeap-cli", "jeap-expert", "migrate-confluence-docs", "migrate-spring-boot-4"]

function fail(message) {
  console.error(`SMOKE TEST FAILED: ${message}`)
  process.exit(1)
}

if (plugin?.id !== "jeap-dev-toolkit" || typeof plugin.setup !== "function") {
  fail(`unexpected plugin export: ${JSON.stringify(plugin)}`)
}

const mcpSet = []
const skillAdds = []

const mcpEditor = {
  get: () => undefined,
  set: (name, config) => mcpSet.push([name, config]),
  update: () => fail("unexpected mcp editor.update() call"),
  remove: () => fail("unexpected mcp editor.remove() call"),
  list: () => [],
}
const skillEditor = {
  get: () => undefined,
  add: (skill) => skillAdds.push(skill),
  update: () => fail("unexpected skill editor.update() call"),
  remove: () => fail("unexpected skill editor.remove() call"),
  list: () => [],
}

await plugin.setup({
  mcp: { transform: async (fn) => fn(mcpEditor), reload: async () => {} },
  skill: { transform: async (fn) => fn(skillEditor), reload: async () => {} },
})

if (mcpSet.length !== 1 || mcpSet[0][0] !== "jeap-mcp-service") {
  fail(`expected exactly one jeap-mcp-service registration, got ${JSON.stringify(mcpSet)}`)
}
if (mcpSet[0][1]?.type !== "remote" || !mcpSet[0][1]?.url) {
  fail(`jeap-mcp-service config looks wrong: ${JSON.stringify(mcpSet[0][1])}`)
}

const gotSkills = skillAdds.map((s) => s.id).sort()
const wantSkills = [...EXPECTED_SKILLS].sort()
if (JSON.stringify(gotSkills) !== JSON.stringify(wantSkills)) {
  fail(`expected skills ${JSON.stringify(wantSkills)}, got ${JSON.stringify(gotSkills)}`)
}
for (const skill of skillAdds) {
  if (!skill.name || !skill.description) fail(`skill ${skill.id} is missing name/description`)
  if (!skill.content || skill.content.length < 100) fail(`skill ${skill.id} has suspiciously short content`)
}

console.log(`SMOKE TEST OK: jeap-mcp-service + ${gotSkills.length} skills (${gotSkills.join(", ")})`)
