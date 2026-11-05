// Shared package.json content check for .github/workflows/opencode-plugin-release.yml's
// "Verify package contents" steps — the `package` job runs this against the checked-out source
// tree, the `release` job against the extracted, already-published-candidate tarball. Previously
// duplicated verbatim as two inline `node -e` one-liners; kept in one place so a future rule
// change (or fix) only has to happen once.
//
// Usage: node ci/verify-package-contents.mjs <path-to-package.json>

import { readFileSync } from "node:fs"

const path = process.argv[2]
if (!path) {
  console.error("Usage: node ci/verify-package-contents.mjs <path-to-package.json>")
  process.exit(1)
}

const pkg = JSON.parse(readFileSync(path, "utf8"))

if (!pkg.name || !pkg.version) {
  throw new Error("Missing package name or version")
}
if (pkg.private === true) {
  throw new Error("Plugin package must not be private")
}
if (Object.keys(pkg.dependencies ?? {}).length > 0) {
  throw new Error("The OpenCode plugin must not have runtime dependencies")
}

console.log(`${pkg.name}@${pkg.version}`)
