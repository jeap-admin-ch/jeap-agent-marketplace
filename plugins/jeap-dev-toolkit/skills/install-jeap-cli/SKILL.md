---
name: install-jeap-cli
description: Install or update the jEAP CLI (the `jeap` command) used for jEAP platform automation such as Spring Boot and Java migrations. Use this when the `jeap` command is missing, when the user asks to install or update the jEAP CLI, or as a prerequisite before running any `jeap` command.
license: Apache-2.0
---

# Install / update the jEAP CLI

Ensure the `jeap` command-line tool is installed and available before running jEAP automation such as `jeap migrate spring-boot-4`.

## When to use

- The `jeap` command is not found on the PATH.
- The user explicitly asks to install or update the jEAP CLI.
- Another skill needs the jEAP CLI as a prerequisite.

## Install scopes

The official installer (`install.sh`) supports two scopes:

- **User (default, recommended)** — installs the launcher to `~/.local/bin/jeap`. No `sudo`. The right choice for a developer's personal machine.
- **Global** — installs the launcher to `/usr/local/bin/jeap`. Requires `sudo`, which prompts for a password interactively — an agent cannot answer that, so the user runs this one themselves.

**Always ask the user which scope to use (step 3), presenting user (local) as the pre-selected default.** Never start installing before the user has answered.

## Prerequisites

The jEAP CLI runs its tooling inside Docker. Docker is needed to **run** the CLI (some `jeap` commands run in a container), not to install the launcher — but installing the CLI is pointless without Docker, so confirm it up front. Before installing, confirm:

- A working local Docker installation (`docker info` succeeds) — required to *use* `jeap`; if it's missing, do not install.
- `curl` available (the installer uses it).
- `script` available (util-linux on Linux, built in on macOS) — used to give the interactive installer a pseudo-TTY.
- A supported OS: Linux (Ubuntu / most distributions) or macOS.
- **Global install only:** `sudo`, with the user able to enter their password themselves.

If a prerequisite is missing, tell the user exactly what is missing and stop — do not attempt workarounds.

## Steps

Steps 1–3 are shared; **step 3 selects which branch you follow.** Both branches end with **Verify the installation**.

### 1. Check whether the CLI is already installed

```bash
command -v jeap >/dev/null 2>&1 && jeap version || echo "jeap not installed"
```

The `|| echo …` tail keeps the command's exit status at 0 when `jeap` is missing — important so running this check in parallel with other tool calls does not cancel them.

If this prints a version, the CLI is installed. The launcher self-updates regularly, so an existing install is normally already current. What to do next depends on why this skill was invoked:

- **Invoked as a prerequisite, or the user did not explicitly ask to update/reinstall** → report the version and stop; no action is needed.
- **The user explicitly asked to update or reinstall** → continue. Re-running the installer refreshes the launcher script. Reuse the existing scope (a user install lives at `~/.local/bin/jeap`, a global one at `/usr/local/bin/jeap` — `command -v jeap` shows which).

If it prints `jeap not installed`, continue.

### 2. Confirm prerequisites

Check the prerequisites listed above before going further — most importantly Docker:

```bash
docker info >/dev/null 2>&1 && echo "docker ok" || echo "docker missing"
```

If Docker (or any other prerequisite) is missing, tell the user exactly what is missing and stop — installing the CLI makes no sense without Docker, since `jeap` commands rely on it to run.

### 3. Ask which scope to install (this selects the branch)

Present both scopes and ask the user to choose, with **user (local) pre-selected as the recommended default**. Wait for the answer before continuing.

- User (local) chosen → follow **A. User install** below.
- Global chosen → follow **B. Global install** below.

### A. User install (default)

**A1. Ensure `~/.local/bin` is on PATH *before* installing.**

Doing this first means the install is complete the moment the launcher is written — no follow-up PATH wiring, whoever ends up running the installer.

```bash
case ":$PATH:" in *":$HOME/.local/bin:"*) echo "on PATH";; *) echo "missing";; esac
```

If it prints `on PATH`, go to A2. If it prints `missing`:

- Pick the right rc file for the login shell (`~/.bashrc` for bash, `~/.zshrc` for zsh, else `~/.profile`; check `echo "$SHELL"` and which files exist).
- Ask the user before editing shell config, showing the exact line and file.
- Append: `export PATH="$HOME/.local/bin:$PATH"`
- The change only affects shells opened *after* the edit. The agent cannot apply it to the current terminal — a subprocess cannot modify its parent's environment, and sourcing the rc file only affects that one subshell. For verification in this session, call the launcher by full path (`~/.local/bin/jeap version`).

**A2. Get the user's consent to run the installer.**

This is a *separate* ask from the scope choice in step 3: here you ask permission to execute external code. The installer is piped from a remote URL into bash (`curl … | bash`), which permission classifiers — and good sense — treat as risky. Get explicit consent. If the user declines, abort and point them to the jEAP CLI docs for a manual install.

**A3. Run the installer.**

The installer is interactive and reads its answer from `/dev/tty`, so piping via stdin alone does not reach it. Wrap it with `script` to provide a pseudo-TTY and feed `2` (user install). `script`'s flags differ between Linux and macOS, so branch on the OS:

```bash
INSTALL_CMD='curl -sSL https://raw.githubusercontent.com/jeap-admin-ch/jeap-cli/main/install.sh | bash'
if [ "$(uname)" = "Darwin" ]; then
  script -q /dev/null bash -c "$INSTALL_CMD" <<< "2"   # macOS / BSD script: command is positional
else
  script -qec "$INSTALL_CMD" /dev/null <<< "2"          # Linux / util-linux script: -c command, -e exit code
fi
```

This installs the launcher to `~/.local/bin/jeap`. (User is the installer's own default — option `2` — so feeding `2` just makes the choice explicit.)

**If the agent is blocked from running this** (executing remote code may be denied), ask the user to run the same OS-appropriate command above in their own terminal, followed by `~/.local/bin/jeap version`. Ask them to reply (e.g. "done", or paste the `jeap version` output) once finished — without that follow-up you will wait silently. Then go to **Verify the installation**.

### B. Global install (only if the user explicitly chose it)

Do **not** run this yourself, and do **not** relay it through the agent's chat: the installer reads from `/dev/tty` and `sudo` needs an interactive password prompt — neither works without a real controlling terminal (it fails immediately with `/dev/tty: No such device or address`).

Ask the user to run these directly in their own interactive terminal session:

```bash
curl -sSL https://raw.githubusercontent.com/jeap-admin-ch/jeap-cli/main/install.sh | bash
# choose 1 (Global) when prompted, then enter your sudo password
jeap version
```

Ask the user to reply "done" (or paste the `jeap version` output) once it finishes — without that follow-up you will wait silently. Then go to **Verify the installation**.

### Verify the installation

If you ran the installer yourself (user install):

```bash
command -v jeap >/dev/null 2>&1 && jeap version || echo "jeap not installed"
```

For a fresh user install, use `~/.local/bin/jeap version` instead if `jeap` is not yet on this shell's PATH — the rc-file change from A1 only takes effect in new shells.

If the user ran the installer themselves (the A3 fallback or the global install), confirm the version they reported, then run `jeap version` yourself to double-check the launcher is visible from the agent's shell. Optionally run `jeap help` to show the available commands.

## Updating

The jEAP CLI self-updates regularly, so manual updates are usually unnecessary (see step 1). To force a refresh of the launcher script, re-run the installer for the existing scope — **A. User install** or **B. Global install** — again only with the user's consent, and keeping the same scope as the original install.

## Notes

- This skill intentionally does **not** pre-approve shell tools — every command is shown to the user for confirmation first.
- Reference: <https://github.com/jeap-admin-ch/jeap-cli/blob/main/README.md>
