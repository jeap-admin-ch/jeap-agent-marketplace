# OpenCode

OpenCode has no marketplace command, but it does have a real plugin-install mechanism — unlike
[OpenCode "plugins"](https://opencode.ai/v2/docs/build/plugins/) in general (JS/TS modules with
lifecycle hooks), `jeap-dev-toolkit`'s plugin is written specifically to use the
[`ctx.mcp.transform`](https://opencode.ai/v2/docs/build/plugins/server/#mcp-servers) and
[`ctx.skill.transform`](https://opencode.ai/v2/docs/build/plugins/server/#skills) hooks to register
the jEAP MCP server and this plugin's skills directly in OpenCode's runtime catalog. No config file
to merge by hand and no skill folders to copy — OpenCode reconciles both on every launch, and a
`jeap-mcp-service` entry you set yourself is left alone.

It is published to the public npm registry as
[`@jeap/jeap-dev-toolkit`](https://www.npmjs.com/package/@jeap/jeap-dev-toolkit).

Requires **OpenCode v2.0.27+** (first release with `ctx.mcp.transform` / `ctx.skill.transform`).
OpenCode v1 is unsupported.

## Install OpenCode itself

If OpenCode isn't installed yet, or was installed via `npm`/`bun`, use the official install script
instead:

```bash
curl -fsSL https://opencode.ai/v2/install | bash
```

## Install the plugin

**Global (recommended):**

```bash
opencode plugin add @jeap/jeap-dev-toolkit
```

**Project-local** — add it to the project's `opencode.json(c)`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": ["@jeap/jeap-dev-toolkit"]
}
```

Pin a version the same way as any other OpenCode plugin: `opencode plugin add
@jeap/jeap-dev-toolkit@1.2.3`, or `{ "package": "@jeap/jeap-dev-toolkit@1.2.3" }` in config.

## Verify

Run `opencode`, then:

- `/mcp` → confirm `jeap-mcp-service` is connected.
- Ask something clearly jEAP-specific (e.g. *"migrate this project to Spring Boot 4"*) and confirm
  a bundled skill picks it up, or check your skill list for `jeap-expert`, `install-jeap-cli`,
  `migrate-spring-boot-4`, and `migrate-confluence-docs`.

## Update

```bash
opencode plugin update @jeap/jeap-dev-toolkit
opencode plugin check @jeap/jeap-dev-toolkit
```

## Remove

```bash
opencode plugin remove @jeap/jeap-dev-toolkit
```

(Or delete the `plugins` entry from your `opencode.json(c)`, for a project-local install.)

## Working from a local checkout instead (development only)

For testing an unpublished change — not needed for normal use — point OpenCode at the checkout
directly:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": ["/absolute/path/to/jeap-agent-marketplace/plugins/jeap-dev-toolkit"]
}
```

OpenCode loads the package through its `package.json` (`name` + `exports`), so the path can
point at either `plugins/jeap-dev-toolkit` or a symlink to it — no copying. A relative path
resolves from that `opencode.json`'s own directory.
