---
name: migrate-spring-boot-4
description: Upgrade a jEAP Spring Boot 3 Maven project to Spring Boot 4 using the jEAP CLI's `jeap migrate spring-boot-4` command, then iteratively fix the compilation and test failures it surfaces until the migrated project builds and all tests pass. Use this when the user asks to migrate or upgrade a jEAP project to Spring Boot 4.
license: Apache-2.0
---

# Migrate a jEAP project from Spring Boot 3 to Spring Boot 4

Automate the Spring Boot 3 → 4 upgrade of a jEAP Maven project with the jEAP CLI (`jeap migrate spring-boot-4`), then drive
the project back to a green build and test run. Note up front that jEAP's Spring Boot 4 baseline runs on **Java 25** — the
migrated code will not build on an older JDK (see step 5).

## What `jeap migrate spring-boot-4` does

Run from the project root, the command performs three steps:

1. Updates the jEAP parent POM to the latest version.
2. Updates explicitly-declared jEAP dependency versions to their latest releases. Dependencies whose versions are managed by the parent POM are left unchanged.
3. Runs jEAP's OpenRewrite recipe `UpgradeSpringBoot_4_0_NoOtherMigrations` to migrate application code, configuration, and dependencies. This recipe deliberately applies **only** the Spring Boot 4 upgrade — not the full chain of earlier OpenRewrite migrations that the stock `UpgradeSpringBoot_4_0` recipe would also re-run.

> Reference: <https://github.com/jeap-admin-ch/jeap-cli/blob/main/docs/migrate-spring-boot-4.md>

## Look up unfamiliar APIs — don't guess

Spring Boot 4 (GA November 2025) moves, renames, and removes a large number of APIs and changes several defaults. The failures you are fixing are exactly the cases the automated recipes could *not* handle — so if your model's knowledge predates the release, treat your recollection of Spring Boot 4 APIs as unreliable and verify against an authoritative source before applying a fix. Route each question to where the answer actually lives:

- **jEAP** packages, classes, starters, listeners, messaging / inbox / outbox, config → the **jEAP MCP server** bundled with this plugin. Use its `jeap_*` tools — `jeap_find_definition`, `jeap_find_references`, `jeap_find_code_examples`, `jeap_overview` — to find the correct Spring Boot 4 replacement. It is version-aware and authoritative for jEAP; prefer it over web search and over your own memory.
- **Spring / Hibernate / Jackson** APIs, defaults, and migration steps → if a general documentation-retrieval MCP is available (for example **Context7**), use it to pull the current, version-correct docs or Javadoc; otherwise fetch the canonical pages below with your web tools. Either way, confirm the new package, artifact, and signature *before* editing — these are exactly the facts that move between major versions.

**Canonical migration guides** (verified 2026-05; if one 404s, search for the current page title):

| Project | Page |
|---|---|
| Spring Boot 4.0 — Migration Guide | <https://github.com/spring-projects/spring-boot/wiki/Spring-Boot-4.0-Migration-Guide> |
| Spring Boot 4.0 — Release Notes (deprecations, upgrade-from-3.5) | <https://github.com/spring-projects/spring-boot/wiki/Spring-Boot-4.0-Release-Notes> |
| Spring Framework 7.0 — Release Notes / upgrade | <https://github.com/spring-projects/spring-framework/wiki/Spring-Framework-7.0-Release-Notes> |
| Spring Security 7.0 — Migrating to 7.0 | <https://docs.spring.io/spring-security/reference/migration/> |
| Spring for Apache Kafka 4.0 — What's New | <https://docs.spring.io/spring-kafka/reference/whats-new.html> |
| Hibernate ORM 7.x — Migration Guides | <https://hibernate.org/orm/documentation/migrate/> |

The **Spring Boot migration guide is the hub** — its "Upgrading … Features" sections link onward to each project. Two specifics worth knowing:

- **Spring Web / WebMVC / WebFlux** have no standalone guide — they ship inside Spring Framework, so use the Spring Framework 7.0 page plus the migration guide's *Upgrading Web Features* section.
- **Spring Data** (2025.1, aligned with Boot 4): the breaking changes you are most likely to hit — build-time query derivation, JPQL generation, query validation moved to build time — are in the migration guide's *Upgrading Data Features* section; use a docs MCP or the Spring Data reference for module specifics.

**To see what the automated step already did** (so you don't redo it, and can spot partial transforms): the jEAP CLI recipe is documented at the Reference link above; its source is <https://github.com/jeap-admin-ch/jeap-rewrite-recipes>, built on OpenRewrite's Spring recipes (<https://docs.openrewrite.org/recipes/java/spring>).

## Run builds without flooding your context

Maven build and test runs — above all integration tests, which start Spring contexts, log SQL, and so on — can produce tens of thousands of lines. If that output lands in your context as a tool result, it crowds out everything you actually need to reason with, and because the migration makes you re-run the build after every fix, the damage compounds fast. So **never let raw build or test output into your context.** Redirect every build to a log file, read back only a distilled failure report, and open individual test reports on demand for the failures you are actually fixing. This is plain shell, so it behaves identically in every agent.

All snippets below write to `.jeap-migration/` — a plain directory **inside the project**. That location is deliberate: it is the one place guaranteed writable whenever the skill can run at all (Maven already writes `target/` there), whereas `/tmp` and `.git/` are both off-limits or escalation-gated under some agent sandboxes — Codex `workspace-write`, for instance, runs freely inside the workspace but escalates for writes *outside* it (so `/tmp`) and commonly write-protects `.git/`. So the scratch stays in the workspace; we just keep git from tripping over it **without writing anything privileged**:

- The clean-tree gate in step 2 runs a *scoped* `git status` that excludes the scratch — `git status --porcelain -- . ':(exclude).jeap-migration'` — so it judges the **user's** work, not our logs. No `.gitignore` edit (that would pollute the review diff) and no `.git/info/exclude` write (a sandbox may forbid it, and the path is fiddly to get right across linked worktrees).
- The post-migration `git diff` is already clean of the scratch: `git diff` never shows untracked files.

The logs and helper are scratch — **delete `.jeap-migration/` before you hand off** (step 9), so it can't be swept into the user's commit. Until then it simply sits untracked.

**Tell the user before a long, quiet build — so they don't think you've hung.** Because every build and test run goes to the log instead of your chat, a full `clean verify` can leave the user watching a silent screen for several minutes and conclude the agent has frozen — then stop it mid-migration. Pre-empt that. Immediately *before* launching any long run (the baseline build, the full `clean verify`, a module `verify` that includes integration tests), post one line to the user: what you're running, that it will be quiet here for several minutes *by design*, and how to watch it live. For example:

> Running the full build and test suite now (unit + integration tests) — this usually takes ~10–20 min and stays quiet here, which is expected, not a hang. To follow along live, run `tail -f .jeap-migration/run.log` in another terminal.

`tail -f .jeap-migration/run.log` is the live view (zero context cost: the verbose stream goes to the file). Each `wait` you run prints one short line — `still running (~Nm elapsed)` or `maven_exit=<code>` — so the chat keeps a light proof-of-life with none of the log reaching your context.

**1. Install the helper once, then run every Maven build through it.** A full `clean verify` can run longer than a single tool call is allowed to last, and a long foreground `./mvnw` call that gets killed at the timeout leaves you with no exit code and a half-written log. Worse, some agent CLIs reap the whole process group when a tool call returns, which kills a plain `nohup … &` build outright — an empty log and nothing to `tail`. The helper sidesteps both: it truly detaches the build into a new session (via `setsid`, so a process-group teardown can't reach it) and records the exit code in a sentinel file, so a later `wait` call picks up the result. And because every build and every poll is then the *same* command — this one script — you approve it **once per session** and all subsequent runs go unattended, with no blanket shell grant (see the Copilot note below).

The helper ships with this skill as **`scripts/jeap-run.sh`**. Copy it into the project (the working directory the builds run in) and make it executable — copy the bundled file, do **not** reconstruct it from memory, so its guardrail stays intact:

```bash
mkdir -p .jeap-migration
# Claude Code — the plugin root is exported for you:
cp "$CLAUDE_PLUGIN_ROOT/skills/migrate-spring-boot-4/scripts/jeap-run.sh" .jeap-migration/
# Other agents — copy scripts/jeap-run.sh from this skill's own directory (the folder that holds
# this SKILL.md): Copilot and Codex surface that path to you; under OpenCode it is the
# skills/migrate-spring-boot-4/ folder you copied in.
chmod +x .jeap-migration/jeap-run.sh
```

**Why it is safe to approve `jeap-run.sh` for the whole session.** The helper only ever launches an allowlisted build tool — `mvn` or `./mvnw` — and refuses anything else (the check runs in both its `run` and its internal `__exec`, so it can't be bypassed). Approving it once therefore does **not** hand the agent arbitrary shell execution: the worst a fumbled or injected command can do *through the helper* is run Maven, not `rm -rf` or `curl … | sh`. This is defense-in-depth, not a sandbox — a Maven build still runs plugins, the lifecycle, and the project's own tests, which is arbitrary code execution by design — so only ever run the migration against a project you trust. If you ever need the helper to run something else, edit the bundled `scripts/jeap-run.sh` and its allowlist, not the project copy.

**2. Launch a build and poll for its result.** Pass the full Maven command to `run` (use `mvn` if the project has no `./mvnw` wrapper), then re-invoke `wait` until it prints `maven_exit=<code>`:

```bash
.jeap-migration/jeap-run.sh run ./mvnw clean verify
.jeap-migration/jeap-run.sh wait      # re-run this EXACT line until it prints maven_exit=<code>
```

`maven_exit=0` means the build is green — trust it and move on; do not re-read the log to "confirm". `-q` is intentionally not passed, so the log holds the complete output for the distill step below — but the log is the *only* place that output goes, so nothing floods your context.

Run one build at a time: `run` **refuses to start a second build while one is still running** (it exits non-zero with `refused: a build is already running`) precisely so a stray double-launch can't interleave `run.log` or race the exit sentinel — finish or stop the in-flight build before starting another. Two messages from `wait` are not `maven_exit` and need no panic: `no build has been started yet` (you polled before launching anything — launch first) and `build process is gone but no exit code was recorded` (the detached build was killed, e.g. across a session restart — just re-launch it). Anything else (`still running …` / `maven_exit=<code>`) is the normal poll.

**Poll by re-issuing the identical `wait` — do not add your own `sleep` or otherwise vary the command between polls.** Each `wait` already paces itself (it blocks ~20 s, returning the instant the build finishes), so prepending `sleep 30 … 60 … 90` does nothing but delay your detection of a finished build — the build runs independently of the poll, so a finished build sits unnoticed until your next call. And on Copilot a *changed* command line forfeits the one-time session approval and prompts again, so keep it byte-for-byte identical every time (a fixed `cd <project> && .jeap-migration/jeap-run.sh wait` is fine — only the varying part hurts). If your CLI cuts a `wait` off early at its own timeout, that's harmless: just re-issue the same `wait`.

> **GitHub Copilot note.** Builds go through one fixed script (not ad-hoc command lines) precisely so Copilot's session approval can remember it — a compound or varying command line can't be. Approve `.jeap-migration/jeap-run.sh` *for the session* the first time you're asked (the middle option, not plain "Yes") — safe to do because the script only ever runs Maven, not arbitrary commands (see step 1's guardrail note). See **Cut down on permission prompts** for this and the other per-CLI modes.

**3. On a non-zero exit, distill the failures from the log — never read it whole.**

```bash
# Which modules failed (small, always safe to read):
sed -nE '/Reactor Summary/,/BUILD (SUCCESS|FAILURE)/p' .jeap-migration/run.log

# Compilation errors, if any (file:[line,col] and the failing goal):
grep -nE '\.java:\[[0-9]+,[0-9]+\]|COMPILATION ERROR|Failed to execute goal' .jeap-migration/run.log | head -n 60

# Failed/errored tests, one line of reason each (the Surefire/Failsafe summary):
awk '/^\[INFO\] Results:/{p=1} p' .jeap-migration/run.log | grep -E '^\[ERROR\]|Tests run:' | head -n 80
```

Together these stay compact even when dozens of tests fail: you get the failing modules, any compile errors, and a one-line-per-test roster — enough to decide what to fix next, without the surrounding log noise.

**4. For a specific failing test, read only its report — not the log.** Surefire (unit) and Failsafe (integration) write a per-class report containing the full stack trace:

```bash
find . -path '*/target/surefire-reports/*.txt' -o -path '*/target/failsafe-reports/*.txt' | grep -i <ClassName>
```

Open that `.txt` (or the matching `.xml` for structured detail) and focus on the deepest `Caused by:` — the real cause, not the surface-level `IllegalStateException: Failed to load ApplicationContext`. If the distilled report is ever not enough, `grep -n` a targeted pattern out of `.jeap-migration/run.log` rather than reading the whole file.

While fixing failures, re-run only the affected tests rather than the whole suite — see **Re-run only the tests you're fixing** below — but always keep the same redirect.

## Re-run only the tests you're fixing

Running the full `verify` after every single fix is the biggest time sink in the migration: integration tests start Spring contexts and Testcontainers, so a full run can take many minutes. During iteration, re-run **only the test(s) you just fixed**, then widen the scope in steps. This cuts each fix-cycle from minutes to seconds. The full suite still has to pass as a whole — but only as the final gate (step 9), not after every fix. (A module-level `verify` with integration tests can itself take minutes — always run it through the helper from **Run builds without flooding your context** so it survives your tool timeout.)

Two things make a narrow run *correct* and not just fast; get either wrong and a test passes against the wrong code or silently runs nothing:

- **Unit vs integration selector.** `-Dtest=` selects **Surefire** (unit) tests; **Failsafe** (integration, usually `*IT`) tests need `-Dit.test=` and the `verify` phase. `-Dtest=FooIT` runs *zero* tests and still looks like a pass — don't do that.
- **Keep dependencies fresh.** `-pl <module>` resolves that module's dependencies on *other* modules from your local `~/.m2` repo — i.e. the last **installed** artifact. If your fix touched an upstream/shared module, the leaf test runs against a **stale** copy of it. Refresh changed upstream modules first (step A). And avoid the tempting `-pl <module> -am verify`: with `-am`, every upstream module runs its *own full* test suite — the opposite of narrowing.

**A. If your fix touched modules other than the one you're testing, refresh them once** (compile + install fresh artifacts, no tests):

```bash
.jeap-migration/jeap-run.sh run ./mvnw -pl <changedA>,<changedB> -am install -DskipTests
.jeap-migration/jeap-run.sh wait      # re-run until it prints maven_exit=<code>
```

**B. Re-run narrow** (deps are now fresh in `~/.m2`, so no upstream tests run) — through the helper, distilling as always. `run` launches detached and returns at once; `wait` until it prints `maven_exit=<code>`:

```bash
# one or more unit tests (Surefire) — class, method, set, or pattern:
.jeap-migration/jeap-run.sh run ./mvnw -pl <module> test -Dtest='FooTest#shouldX'
#   selectors: -Dtest='FooTest' | 'FooTest#m1+m2' | 'FooTest,BarTest' | '*MapperTest'

# one or more integration tests (Failsafe) — note -Dit.test, not -Dtest:
.jeap-migration/jeap-run.sh run ./mvnw -pl <module> verify -Dit.test='FooIT'

# every test in one module:
.jeap-migration/jeap-run.sh run ./mvnw -pl <module> verify

# …then poll until done:
.jeap-migration/jeap-run.sh wait
```

Add `-DfailIfNoTests=false` if a selector spans more than one module, so modules with no matching test don't fail with "No tests were executed". (To find a test's module, walk up from the test file to its nearest `pom.xml`.)

**C. Escalate scope as fixes land:** failing test → the rest of that module (`-pl <module> verify`) → the full reactor. A migration fix can pass in isolation yet break other tests — SB4 changes are often global (a moved autoconfig, a shared bean, a parent-BOM bump) — so widening the scope is what catches those before the final run.

**D. Final build gate (non-negotiable):** the build is done only after a clean, full `./mvnw clean verify` over the whole reactor is green — and a green full build is necessary but not sufficient for the *migration* to be complete; you must also write the report file (step 9). Narrow runs are for iteration only — they never replace the final full run.

## Cut down on permission prompts (recommended, per CLI)

This migration issues many commands over a long session — builds, greps, file edits, the `jeap` CLI, and `jeap-mcp-service` lookups — so approving each one by hand is painful. Before you start, switch your CLI to a mode that auto-runs routine work while still guarding genuinely risky actions. Use the line for your CLI:

- **Claude Code — `auto` mode.** Cycle to it with **Shift+Tab** until the status line reads `⏵⏵ auto on`, or start with `claude --permission-mode auto` (or set `"permissions": { "defaultMode": "auto" }` in `~/.claude/settings.json`). A built-in classifier auto-approves routine commands and still asks about risky ones — unlike *accept-edits* mode, which auto-approves file edits but still prompts on every Maven/shell command. Note `auto` needs a recent model on the **first-party Anthropic API** (not Bedrock/Vertex/Foundry), and on Team/Enterprise an admin must enable it first; if it's unavailable to you, fall back to accept-edits plus a single prefix allow-rule for the helper — `"permissions": { "allow": ["Bash(.jeap-migration/jeap-run.sh:*)"] }`. One rule covers every build (they all go through the helper), and it is safe to allow this broadly precisely because the helper only ever runs Maven (see its guardrail in **Run builds without flooding your context**), not arbitrary shell.

- **Codex CLI — `Auto` mode.** This is Codex's default preset and is exactly what you want: it runs commands inside the project workspace without asking, and only escalates for actions outside the workspace or for network access. Confirm/select it in-session with **`/permissions`** (older versions: `/approvals`) → **Auto**, or start with `codex --sandbox workspace-write --ask-for-approval on-request`.

- **GitHub Copilot CLI — approve recurring tools for the whole session.** Copilot prompts per command and has no safe auto-classifier, so the trick is to approve the things this skill repeats — **for the session, not just once.** The first time it runs `.jeap-migration/jeap-run.sh`, pick the **middle** option, **"Yes, and approve … for the rest of the running session"** — *not* plain "Yes", which re-asks on every single poll. That approval is scoped to **that one command**, so all later builds and `wait`s run unattended while every other shell command still prompts (no blanket shell access). Do the same the first time the agent calls a **`jeap-mcp-service`** tool (approve it for the session), and for any other recurring tool it needs (file edits, the `jeap` CLI). You can also pre-approve at launch: `copilot --allow-tool 'jeap-mcp-service' --allow-tool 'shell(...)'`.
  - **Optional — `yolo` mode (your call, *not* recommended).** Copilot can skip **all** approval prompts via the in-session **`/yolo`** command, or at launch with **`--yolo`** (equivalent to `--allow-all`; or `--allow-all-tools` for tools only). It is the lowest-friction option, but it lets the agent run **any** tool without asking — GitHub recommends it only in an isolated environment and warns against making it a permanent alias. Enabling it is entirely **your responsibility**, and from a security standpoint it is **not recommended**; prefer the per-tool session approvals above.

## Workflow

1. **Preflight — confirm the CLI *and* that this is a jEAP Spring Boot 3 Maven project.** Do these cheap checks first, before any expensive build or the migration itself, so a wrong target fails fast with a clear message instead of after a ~10–20 min baseline run or a confusing mid-migration error:

   - **jEAP CLI present.** If the `jeap` command is missing, use the `install-jeap-cli` skill (which asks the user before installing). Verify with `jeap version`.
   - **Maven project.** There is a `pom.xml` at the project root. If not, this skill doesn't apply — stop and tell the user.
   - **jEAP project.** The root POM inherits a jEAP parent (its `<parent>` is a jEAP artifact, typically under `ch.admin.bit.jeap…`). A quick `grep -i jeap pom.xml` should hit; if nothing references jEAP, stop — `jeap migrate spring-boot-4` only works on jEAP projects.
   - **Currently on Spring Boot 3 (not already 4, not 2.x).** This is a 3 → 4 upgrade. The jEAP parent pins the Spring Boot baseline, so if you can determine it cheaply — the jEAP parent version, or `./mvnw help:evaluate -Dexpression=spring-boot.version -q -DforceStdout` if the version is resolvable — confirm it reads as 3.x. If it already resolves to 4.x the migration is a no-op (say so and stop); if it's 2.x or earlier, that's out of scope for this skill. If you *can't* determine it cheaply, don't block on it — the baseline build and the migrate command will surface a wrong version soon enough.

   If a hard check fails — no `pom.xml`, or no jEAP parent — stop and report it rather than proceeding to the baseline build.

2. **Establish a clean, fully-tested Spring Boot 3 baseline.** Before migrating, confirm the project currently builds **and all tests pass** on Spring Boot 3. Run a full `clean verify` (use `mvn` if there is no wrapper) with the patterns from **Run builds without flooding your context** — i.e. create the helper and run it through `.jeap-migration/jeap-run.sh run … ; wait`, so the run survives your tool timeout. Trust `maven_exit`: `0` is green (don't re-run to confirm); on non-zero, distill the failing modules/tests from the log (step 3 of that section) rather than reading it whole.

   **The baseline must be green (compile + all tests pass) before you migrate.** This is a hard prerequisite, not a recommendation: any test failure that survives the migration could be a pre-existing business-logic bug rather than a technical migration issue, and without a clean baseline you cannot tell the two apart. If the baseline build fails, stop and report which tests/modules fail — the user has to fix the baseline before migration can proceed.

   Make sure the working tree is clean before migrating. Check with a **scoped** `git status` that ignores our own scratch — no `.gitignore` edit and no `.git/` write, so it behaves identically under every agent sandbox:

   ```bash
   git status --porcelain -- . ':(exclude).jeap-migration'
   ```

   If that shows uncommitted changes, ask the **user** to commit or stash them first — you don't do this yourself (see the no-commit rule in **Guardrails**). Scoping the check to exclude `.jeap-migration/` is what keeps the gate about the **user's** own work rather than our logs. A clean starting tree matters for two reasons. First, the post-migration `git diff` is then exactly the migration's changes, so the user can review it (and `git diff` never shows the untracked scratch). Second, this clean baseline *is* the point to return to — because the migration never commits, everything it touches stays in the working tree, so if it ever leaves the project in a state you can't drive forward to green, you can discard those changes wholesale and start over rather than untangling them: `git reset --hard HEAD` returns to the clean baseline, then re-run from step 3. (The reset leaves the untracked `.jeap-migration/` in place — which is what you want: the helper stays available for the re-run.) A hard reset is destructive and irreversible, so confirm with the user before running one.

3. **Run the migration command** from the project root:

   ```bash
   jeap migrate spring-boot-4
   ```

4. **Read the output for errors and warnings.** Failures are typically caused by moved/renamed packages or classes, or by missing/changed dependencies introduced in Spring Boot 4.

5. **Make the project compile — do NOT run tests yet.** Fix whatever blocked the command from completing: update import statements, add or adjust dependencies, and apply code changes for Spring Boot 4 API changes. Verify with a compile-only build, redirected to the log as always:

   ```bash
   .jeap-migration/jeap-run.sh run ./mvnw -DskipTests clean compile
   .jeap-migration/jeap-run.sh wait      # re-run until it prints maven_exit=<code>
   ```

   This is the first host build of the migrated Spring Boot 4 code, so the host JDK now matters: jEAP's Spring Boot 4 baseline is **Java 25**, and the migrated code will not compile on an older JDK. If the build fails with `release`/`--release` or class-file-version errors, check `java -version` and switch the host to JDK 25 before retrying.

   On a non-zero exit, read only the compilation errors:

   ```bash
   grep -nE '\.java:\[[0-9]+,[0-9]+\]|COMPILATION ERROR|Failed to execute goal' .jeap-migration/run.log | head -n 60
   ```

   The goal of this step is only successful compilation, so the migration command can finish cleanly.

6. **Re-run the migration and iterate.** Go back to step 3 and run `jeap migrate spring-boot-4` again. Some issues only surface after earlier ones are fixed, so repeat steps 3–5 until the command completes with no errors.

   **If the command fails while *running the recipes* (not on your code), update the jEAP CLI and re-run.** Recipe-step failures can be version- or environment-specific, so update with the `install-jeap-cli` skill and run `jeap migrate spring-boot-4` again. If an up-to-date CLI still can't run the recipes, **tell the user**: explain that this looks like a jEAP CLI bug that is interrupting the migration, and recommend they report it to the jEAP team so the CLI can be fixed (reporting it is the user's call — you can't and shouldn't contact the team yourself).

7. **Build and test the migrated project.** Now run the full `clean verify` (unit + integration tests) with the same patterns as the baseline — through the helper (`.jeap-migration/jeap-run.sh run ./mvnw clean verify ; wait`), then distill on a non-zero exit (see **Run builds without flooding your context**). Do not read the log whole.

8. **Fix every failing test caused by the migration.** This step is not optional and is not a hand-off. Tests that passed in the Spring Boot 3 baseline must pass again after the migration. Do not present remaining failures to the user as TODOs or recommendations — fix them yourself and re-run only the affected test(s), widening scope as fixes land (see **Re-run only the tests you're fixing**), always via the redirect-and-distill pattern, until the module and then the full suite are green. Stop and ask the user only when (a) you have tried at least two distinct fixes for the same failure and they have not helped, or (b) the failure is a genuine behavioral regression that requires project-specific domain knowledge you do not have. "I'm not sure what changed" is not by itself a reason to stop — investigate (read the failing test, the production code it exercises, the SB4 release notes, the relevant jEAP library) and try a fix.

   Common Spring Boot 4 migration fix-ups — **worked examples observed against Spring Boot 4.0 (GA Nov 2025), verified 2026-05; not eternal constants.** Package names, artifact ids, and which test slices live in which split module can shift across SB4 minor releases, so treat each item as a strong starting point, not a guarantee: if one doesn't apply cleanly (a package or artifact that doesn't resolve), confirm the *current* name via the routing in **Look up unfamiliar APIs** — the jEAP MCP server for jEAP, a docs MCP or the canonical migration guides for Spring/Hibernate/Jackson — before relying on it. The list is also not exhaustive; for anything not listed, use the same routing:

   - **Test-slice annotations moved to per-slice modules.** Spring Boot 4 split `spring-boot-test-autoconfigure` into one module per test slice. Imports change and you need to add the new artifact(s) explicitly (they are no longer transitives of `spring-boot-starter-test`):
     - `@WebMvcTest`, `@AutoConfigureMockMvc`: package → `org.springframework.boot.webmvc.test.autoconfigure`, artifact → `spring-boot-webmvc-test`
     - `@DataJpaTest`: package → `org.springframework.boot.data.jpa.test.autoconfigure`, artifact → `spring-boot-data-jpa-test`
     - `@DataJdbcTest`, `@JdbcTest`, `@JsonTest`, `@WebFluxTest`, `@RestClientTest`, etc. follow the same per-slice pattern — check the SB4 API docs for the exact package and artifact.
   - **`@AutoConfigureObservability` was split.** Replace with `@AutoConfigureMetrics` (`org.springframework.boot.micrometer.metrics.test.autoconfigure`, artifact `spring-boot-micrometer-metrics-test`) and/or `@AutoConfigureTracing` (`org.springframework.boot.micrometer.tracing.test.autoconfigure`, artifact `spring-boot-micrometer-tracing-test`). If the original `@AutoConfigureObservability` had no arguments, apply both.
   - **Other actuator/data autoconfig package moves.** Examples seen in practice: `org.springframework.boot.autoconfigure.domain.EntityScan` → `org.springframework.boot.persistence.autoconfigure.EntityScan`; `RepositoryMetricsAutoConfiguration` renamed to `DataRepositoryMetricsAutoConfiguration` in `org.springframework.boot.data.autoconfigure.metrics`. Search the SB4 API for the new location using the class's simple name.
   - **Hibernate 7 rejects JPQL queries without an explicit `select` when joins are present.** A query like `from Foo f join f.bar b where …` must become `select f from Foo f join f.bar b where …`. Fix every `@Query` that fits this pattern.
   - **A stale or wrong result from an existence/boolean check that depends on rows written earlier in the same transaction is an auto-flush problem — diagnose which of two kinds.** Hibernate's `AUTO` flush only flushes before a JPQL/HQL query when the query's *affected table-spaces overlap the pending entity actions* (Hibernate User Guide, *Flushing*). If a table the query depends on isn't registered as "affected", the pending insert/update to it is **not** flushed and the query reads stale data. The SQL Hibernate generates is correct — only the flush is skipped — so this is never a CASE / `exists` / boolean-literal *translation* regression. Two distinct cases, with different fixes:
     - **The modified table IS in the query's top-level `from`** (e.g. `from Order o where o.id = :justUpdated`) yet the result is still stale → the flush heuristic is fine; the cause is flush mode / transaction boundaries (a read-only or separate transaction, or a test that never flushes). Fix with a flush, not a query rewrite: `@Modifying(flushAutomatically = true, clearAutomatically = true)` on the *writing* method, an explicit `flush()`, or a flush-mode hint — and keep the `exists` predicate.
     - **The modified table appears ONLY in a subquery / `exists(…)`, or the query is a `from`-less existence check** (`select case when exists(select … from Child c where …) then true else false end`, with no top-level `from`) → Hibernate may fail to register that table as affected, skip the flush, and read stale data. This is a real, documented class of auto-flush table-space bug, not a query-translator bug (e.g. [HHH-17686](https://hibernate.atlassian.net/browse/HHH-17686), where a joined/subtype table wasn't registered and a pending update wasn't flushed). **The robust fix is to make the modified entity the query's top-level `from` root** — rewrite the `from`-less `select case when exists(select … from Child c where …) …` to `select count(c) > 0 from Child c where …` (and anchor an "are all children in a final state" check on the child entity, not the parent). This is correct *by construction* — the root `from` table is always registered for auto-flush — and is version-independent and refactor-safe, so prefer it. The `exists` → `count(…) > 0` change is benign for indexed existence checks; if short-circuiting matters on a large table, keep an `exists`/`in` predicate but still root the `from` on the modified entity. A query hint forcing `FlushMode.ALWAYS` (`@QueryHints(@QueryHint(name = "org.hibernate.flushMode", value = "ALWAYS"))`) is a valid alternative that preserves the exact query, but it flushes the whole persistence context on every call and is easy to lose in a refactor.
   - **Spring Boot 4 / Mockito changes break some test idioms.** `@Captor` is no longer auto-initialized inside `@SpringBootTest` — fix by adding `@ExtendWith(MockitoExtension.class)` to the test class or calling `MockitoAnnotations.openMocks(this)` in a `@BeforeEach`. `@WebMvcTest` no longer imports security autoconfig (its slice `.imports` carry no security classes). If a test's `SecurityFilterChain` bean fails to resolve `HttpSecurity`, add `@ImportAutoConfiguration(org.springframework.boot.security.autoconfigure.web.servlet.ServletWebSecurityAutoConfiguration.class)` — that is the autoconfig whose nested `@EnableWebSecurity` supplies `HttpSecurity` (add `UserDetailsServiceAutoConfiguration` from the same base package too if the chain needs the default user) — or move the security config out of the slice. Use `@ImportAutoConfiguration` (it honours autoconfig ordering and conditions), **not** plain `@Import`; and note that `SecurityAutoConfiguration` (moved to `org.springframework.boot.security.autoconfigure`) now only configures `SecurityProperties` and the auth-event publisher, so importing *it* does not supply `HttpSecurity`.
   - **`jakarta.servlet.Filter` may be missing on non-web modules' test classpaths.** Spring Boot 4 does not bring it in transitively for non-web modules, so any auto-config that references it (e.g. `JeapLoggingAutoconfig`) fails to introspect during context load. Add `jakarta.servlet:jakarta.servlet-api` (test scope, version managed by the parent BOM) to the affected module's `pom.xml`.
   - **jEAP TraceContext constructor gained a `Boolean sampled` parameter.** Update call sites to pass an additional argument (typically `true` for test fixtures).
   - **Jackson 2 → 3 namespace migration.** Spring Boot 4 ships Jackson 3 as the JSON stack, and the jEAP recipe repoints the usages it recognizes — but it can't reach every hand-written Jackson reference, so residual ones won't compile against the new namespace. The base package moves from `com.fasterxml.jackson.*` to `tools.jackson.*` (e.g. `com.fasterxml.jackson.databind.ObjectMapper` → `tools.jackson.databind.ObjectMapper`; artifact `com.fasterxml.jackson.core:jackson-databind` → `tools.jackson.core:jackson-databind`). **Don't blanket-replace the package** — there are exceptions: the core annotations in `jackson-annotations` (`com.fasterxml.jackson.annotation.*`, e.g. `@JsonProperty`, `@JsonIgnore`) are deliberately *retained* unchanged (shared between 2.x and 3.x), whereas databind-level annotations like `@JsonSerialize`/`@JsonDeserialize` *do* move to `tools.jackson.databind.annotation`. Grep for leftover `com.fasterxml.jackson` imports and repoint each per the rules above, verifying the new package per symbol rather than search-replacing the whole prefix.

   For each failure, open only its per-class report (step 4 of **Run builds without flooding your context**) and look at the **root cause** — the deepest `Caused by:` line — rather than the surface-level `IllegalStateException: Failed to load ApplicationContext`.

9. **Finish: green build, *then* the report file, *then* hand off.** Two conditions must **both** hold before the migration counts as done — a green build alone is **not** enough:

   1. A full `./mvnw clean verify` over the whole reactor is green (not just the narrow per-test runs from step 8); **and**
   2. the **`spring-boot-4-migration.md`** report file exists at the **project root**, filled in from the template.

   Until that file is on disk, the migration is **unfinished — do not report it as complete.** The report is a required deliverable, not optional bookkeeping: it is what survives after this session ends and gets reviewed and committed alongside the diff. A summary printed to the chat is **not** the report and does **not** satisfy this step — the chat is gone when the session is; the file is the deliverable. Do these in order, and don't skip ahead:

   **a. Confirm the full build is green.** Final-gate `./mvnw clean verify` over the whole reactor through the helper (same redirect-and-distill pattern as the baseline). Don't proceed past a non-`maven_exit=0` build.

   **b. Write the report file.** Read the template bundled with this skill and write the filled-in result to `spring-boot-4-migration.md` at the **project root** — **not** under `.jeap-migration/` (scratch, gets deleted) and **not** only to the chat. Use the template so the summary has the same shape in every project:

   - **Claude Code** — read `$CLAUDE_PLUGIN_ROOT/skills/migrate-spring-boot-4/assets/spring-boot-4-migration.template.md`.
   - **Other agents** — read `assets/spring-boot-4-migration.template.md` from this skill's own directory (the folder that holds this SKILL.md): Copilot and Codex surface that path to you; under OpenCode it is the `skills/migrate-spring-boot-4/` folder you copied in.

   Fill in every section and replace every `<placeholder>`, delete the guidance comments, and keep the section order fixed. The template captures: an overview of versions and the final build; the dependency changes (version bumps and any dependencies you added by hand); a pointer to the recipe-applied automated changes; the agentic fixes you made, each with a one-line *why*; a review checklist; and the verification evidence. Use prose only where a change's *why* needs more than a table cell.

   **c. Verify the report is actually on disk** — the same trust-but-check discipline as `maven_exit`. The write isn't done until this confirms a real, filled-in file:

   ```bash
   ls -l spring-boot-4-migration.md && grep -c '<[A-Za-z]' spring-boot-4-migration.md
   ```

   That must list the file **and** print `0`. The template's unfilled fields are angle-bracket placeholders (`<old>`, `<N>`, `<area>`, …) and the guidance comments contain them too, so a non-zero count means either a field is still unfilled or you haven't deleted the comments — in both cases the report isn't finished. If the file is missing or the count is non-zero, complete it now. Do **not** delete the scratch or hand off until this passes.

   **d. Delete the scratch.** Remove `.jeap-migration/` (`rm -rf .jeap-migration`) — it is untracked, so a stray `git add -A` would otherwise sweep the helper and logs into the user's commit. The report lives at the project root, not in the scratch, so deleting the scratch doesn't touch it.

   **e. Only now, summarise in chat.** With the file written and verified, give the user a short chat summary — the headline version changes and the count of agentic fixes — and point them to `spring-boot-4-migration.md` for the full, reviewable detail. **Hand the working tree off uncommitted.** Do not `git commit` or `git add` the migration changes or the report file — leave everything unstaged so the user can inspect, verify, and commit it themselves. Close by telling the user explicitly that nothing has been committed, that committing is theirs to do once they're satisfied, and that the report is at the project root for review.

## Guardrails

- **Never commit, and never stage for commit.** Do not run `git commit` or `git add` (nor stash the migration work away) at any point — not after the baseline, not after `jeap migrate spring-boot-4`, not at the finish. Leave every migration change unstaged in the working tree; inspecting, verifying, and committing the migration is the **user's** responsibility and their review gate. If the working tree isn't clean to begin with, ask the user to commit or stash their *own* changes first — you don't do it for them (step 2). The only git state you may change is the `git reset --hard HEAD` recovery path back to the clean baseline, and only with explicit user confirmation.
- Never let raw build or test output into your context — redirect to `.jeap-migration/run.log` and read back only the distilled report or a single test report (see **Run builds without flooding your context**).
- Recommend up front that the user set their CLI to a low-friction-but-safe mode so the long run isn't a wall of prompts — Claude Code `auto` mode, Codex `Auto` mode, or per-tool session approvals on Copilot (see **Cut down on permission prompts**).
- Run every Maven build and test through the helper (`.jeap-migration/jeap-run.sh run … ; wait`), which detaches the build into a new session so it survives both your tool-call timeout and any process-group teardown. **Install it by copying the skill's bundled `scripts/jeap-run.sh` — never hand-write it** — so its allowlist guardrail (it refuses to launch anything but `mvn`/`mvnw`) stays intact; that is what makes approving it for the whole session safe. Never block on one long foreground `./mvnw` call, and never launch a build with a bare `nohup … &` (a CLI that reaps the tool call's process group will kill it).
- Poll by re-issuing the *identical* `wait` command — never prepend your own `sleep` and never vary the command between polls. The helper already paces each call (~20 s); added sleeps only delay detection of a finished build, and a changed command line re-triggers Copilot's approval prompt.
- Before launching any long build (the baseline, a full `clean verify`, a module `verify` with integration tests), tell the user in one line that it will be quiet for several minutes by design and point them to `tail -f .jeap-migration/run.log` — so they don't mistake the silence for a hang and stop you (see **Run builds without flooding your context**).
- During step 8, re-run only the tests you're fixing and widen scope gradually — `-Dtest=` for unit tests, `-Dit.test=` for integration (`*IT`) tests, and refresh changed upstream modules (`install -DskipTests`) before testing a module that depends on them. The final gate is always a full-reactor `clean verify`; a narrow green never substitutes for it.
- Never run unit or integration tests in steps 5–6 — only compile. Tests come after the migration command succeeds (step 7).
- The migrated project requires **JDK 25** (jEAP's Spring Boot 4 baseline) — confirm `java -version` before the host builds in steps 5–9.
- Don't guess at moved, renamed, or removed APIs — verify first: the jEAP MCP server for jEAP, and a docs MCP (e.g. Context7) or the canonical migration guides for Spring / Hibernate / Jackson (see **Look up unfamiliar APIs**).
- Always ask before installing the jEAP CLI.
- Do not hand the user a list of failing tests to fix themselves — that is the job of this skill (step 8).
- The migration is **not complete — and must not be reported as complete — until the `spring-boot-4-migration.md` report file exists at the project root**, filled from the skill's bundled `assets/spring-boot-4-migration.template.md`. A green build alone does not finish the migration, and a chat summary is **not** the report. Write the file (never in the scratch `.jeap-migration/`, never only in the chat), verify it is on disk and free of `<placeholder>`s (`ls -l spring-boot-4-migration.md`), and only then delete the scratch and hand off (step 9). It is left for the user to commit; you never commit it.
