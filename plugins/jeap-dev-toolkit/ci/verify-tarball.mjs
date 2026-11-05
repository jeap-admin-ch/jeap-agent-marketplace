// Shared tarball check for .github/workflows/opencode-plugin-release.yml's
// "Verify package contents" steps — the `package` job runs this right after `npm pack`, the
// `release` job against the tarball artifact `package` built (downloaded, not re-packed).
// Previously this only validated package.json fields read from the source tree/extracted
// directory; it now inspects the actual tarball (`tar -tzf`), which is what catches a packaging
// rule — like package.json's "files": ["skills", "!skills/*/tests"] — silently not doing what it
// says, instead of trusting it by construction.
//
// Usage: node ci/verify-tarball.mjs <path-to-tarball.tgz>

import { execFileSync } from "node:child_process"

const tarball = process.argv[2]
if (!tarball) {
  console.error("Usage: node ci/verify-tarball.mjs <path-to-tarball.tgz>")
  process.exit(1)
}

const REQUIRED_ENTRIES = ["package/package.json", "package/README.md", "package/.opencode-plugin/index.ts"]
const FORBIDDEN_PATTERNS = [
  { name: "skill tests directories", test: (entry) => /^package\/skills\/[^/]+\/tests\//.test(entry) },
  { name: "Python bytecode caches", test: (entry) => entry.includes("__pycache__") || entry.endsWith(".pyc") },
]

const entries = execFileSync("tar", ["-tzf", tarball], { encoding: "utf8" })
  .split("\n")
  .filter(Boolean)

for (const required of REQUIRED_ENTRIES) {
  if (!entries.includes(required)) {
    throw new Error(`${tarball} is missing required entry: ${required}`)
  }
}

for (const { name, test } of FORBIDDEN_PATTERNS) {
  const matches = entries.filter(test)
  if (matches.length > 0) {
    throw new Error(`${tarball} contains ${name}, which must not be published:\n${matches.join("\n")}`)
  }
}

const pkg = JSON.parse(execFileSync("tar", ["-xzO", "-f", tarball, "package/package.json"], { encoding: "utf8" }))

if (!pkg.name || !pkg.version) {
  throw new Error("Missing package name or version")
}
if (pkg.private === true) {
  throw new Error("Plugin package must not be private")
}
if (Object.keys(pkg.dependencies ?? {}).length > 0) {
  throw new Error("The OpenCode plugin must not have runtime dependencies")
}

console.log(`${pkg.name}@${pkg.version} (${entries.length} files)`)
