# jeap-expert trigger evals

Maintainer tooling — measures whether the [`jeap-expert`](../../plugins/jeap-dev-toolkit/skills/jeap-expert/SKILL.md)
skill actually makes agents ground jEAP answers in the `jeap_*` tools. Re-run after changing the
skill's description or body, or after a Copilot/Claude CLI update changes triggering behaviour.
This directory is not part of the plugin and is never shipped to plugin users.

## What is measured

[`queries.json`](queries.json) holds 19 realistic queries: 12 *should-trigger* (explicit jEAP,
concept-only without the keyword, casual/typo phrasing, project bootstrapping, a hand-rolled-vs-platform
question, a docs-link request and four debugging cases) and 7 *should-not-trigger* near-misses. Debugging is covered as
a 2×2 matrix on the JWT-rejection scenario — a `ch.admin.bit.jeap.*` log line vs. a generic Spring
Security log line, in the jEAP fixture vs. the plain fixture (`s8`, `s9-debug-generic-log`,
`n6`) — where `s9` is the hardest positive: nothing in the query names jEAP, only the project
markers can carry the trigger. Two more debug cues from the skill description get their own
queries: a `jeap.*` property without effect (`s10`, mirrored by the plain-Spring `n7`) and
messages not flowing out of the transactional outbox (`s11`, keyword-free). `s12-docs-link` forces
a read-on pointer and checks that it arrives as a public `jeap-admin-ch.github.io` URL resolved via
the site's sitemap, not as an internal corpus path — the Claude runner allows `WebFetch` and the
Copilot runner `--allow-url jeap-admin-ch.github.io` exactly so this part of the skill is
exercised. Each query runs from a
fixture directory in [`fixtures/`](fixtures/): a minimal but coherent jEAP-based service
(`jeap-fixture` — security starter, messaging + outbox dependencies, Kafka config, and a
deliberately half-configured `log.access-denied.debug` without `enabled` that `s10` diagnoses), a
plain Spring Boot project (`plain-fixture`), or an empty directory (`neutral`).

Two harness-specific measurements:

- **`run_claude_evals.py`** — runs each query via `claude -p` and records whether the skill formally
  fired (Skill tool) and, more importantly, whether the answer was grounded (`jeap_*` calls made).
- **`run_copilot_ab.py`** — runs every query via `copilot -p` with the full plugin (the negatives
  measure false fires under the skill's prompt pressure), and the should-trigger queries once more
  against a stripped copy (no jeap-expert skill) as baseline — in throwaway HOME copies so
  the installed plugin and real Copilot state stay out of it. It aborts if any skill description
  exceeds 1024 characters: Copilot silently drops such skills, which once turned an A/B into a
  measurement of two skill-less variants.

`analyze.py` aggregates both into per-query tables plus totals. Tool calls are counted from the
rendered tool-call headings of the session transcripts — a ranking-grade proxy, not an audited
count.

**Answer quality** is graded separately by **`grade_answers.py`**: the debug-relevant queries carry
an `assertions` list in `queries.json` stating corpus-verified ground truth (verified against
`jeap-spring-boot-security-starter.md` and the `jeap-messaging-outbox` docs), and an LLM judge
(`claude -p`, no tools) checks each assertion against the run's final answer. This catches the
failure the trigger metrics cannot see — a run that fired, grounded, and still produced a
plausible-but-wrong diagnosis. Assertions on the negatives grade the flip side: that a soft misfire
still ends in a correct, jEAP-free answer. Queries without assertions (the how-to ones) remain
ungraded — quality claims about those still cannot be made from this harness.

## Running

Prerequisites: authenticated `claude` and/or `copilot` CLIs.
Each Copilot run costs a premium request; Claude runs bill the logged-in account.

```bash
cd evals/jeap-expert

# Claude Code — cleanest with a throwaway config that has only this plugin:
export CLAUDE_CONFIG_DIR=/tmp/jeap-eval-claude
cp ~/.claude/.credentials.json "$CLAUDE_CONFIG_DIR"/   # after mkdir
claude plugin marketplace add ../..
claude plugin install jeap-dev-toolkit@jeap
python3 run_claude_evals.py --config-dir "$CLAUDE_CONFIG_DIR"

# Copilot A/B (sets up its own throwaway homes under workdir/):
python3 run_copilot_ab.py

python3 analyze.py
python3 grade_answers.py    # answer quality on the assertion-carrying queries; --sources copilot for the A-variant
```

Results land in `workdir/` (gitignored).

## Baseline (2026-08-06, 19 queries, plugin 0.9.0 as published, `workdir/run-2026-08-06e`)

The reference point for all future runs, measuring exactly the skill state published as 0.9.0:
the troubleshooting rewrite (debugging first-class in description and body, including the
search-by-error-message-text entry point), the sitemap-based public-URL resolution for read-on
pointers, the docs-link trigger cue, and the description compacted to 1017 characters. Earlier
runs are not comparable and were dropped from this README — git history has them. Two of them
deserve a note: run `c` measured the pre-URL-rule skill, and run `d` was discarded because the
description had grown to 1207 characters and Copilot silently dropped the skill (its "A/B"
compared two skill-less variants — the origin of the 1024-character guard in `run_copilot_ab.py`).
Single run per query — **indicative, not statistical**. Assertions were corrected three times
during grading when the judge's reasoning, checked against the corpus, showed them over-narrow or
incomplete (allow source-code citations, require provenance instead of a closed vocabulary, and
docs-vs-source ground truth on `s10` — where the eval surfaced a genuine documentation gap: the
security starter's table does not mention that `log.access-denied.debug=true` moves the line to
DEBUG log level, so a `logging.level` raise is legitimately part of the fix). The numbers below
use the corrected assertions.

**Claude Code** (`claude -p`, throwaway config):

- Should-trigger **12/12 fired and grounded** (1–10 `jeap_*` calls per run) — including the
  hardest positives `s9` (generic Spring Security log line, only project markers carry the
  trigger — 7 calls) and `s11` (keyword-free outbox symptom — 7 calls), and `s12`, which fetched
  the sitemap via `WebFetch` and returned the correct public URL.
- Negatives **4/7 clean**; `n1`, `n6`, `n7` soft-misfired (skill load, 0–4 lookups), each ending
  in a correct answer that leads with "this project is not jEAP-based".
- Answer grading **21/23**: `s8` 5/5, `s9` 4/4, `s10` 3/3 (the run out-researched the docs and
  read the handler source), `s12` 3/3, `n7` 2/2. Lost: one provenance assertion on `s11` (named
  real outbox internals without attributing them) and the recurring `n6` fail — an unrequested
  `jeap.*` configuration section appended to a correct plain-Spring diagnosis, confirmed in three
  consecutive runs and the designated candidate for the next skill tweak (sharpen the "offer the
  jEAP route only if there is a real one" bullet).

**Copilot CLI 1.0.78 A/B**:

- The **null result persists** in short single-question runs: A (with skill) 43 `jeap_*` calls,
  B (skill stripped) 49; both grounded 12/12; formal skill load 4/12 in A. Standing caveats: the
  setting where the skill is hypothesized to matter — long mixed sessions where one-line tool
  descriptions drown, sessions without pre-approved tools — is not measured here, and Copilot
  drops MCP server instructions (logged "deferred"; `--allow-tool` does not change that), which is
  the channel gap the skill closes.
- Negatives **3/7 clean**: `n1` (3 lookups), `n2` (skill load + 1), `n6` (2 lookups),
  `n7` (skill load + 1).
- Answer grading **16/23**, and the losses profile Copilot rather than the skill: citation
  discipline fails on `s8`/`s9`/`s10`/`s11` (grounded lookups happen, sources are almost never
  named — the skill that mandates citing loaded on none of those runs), `s12` 1/3 (no skill load
  on that run, so the URL rule was not in context and the corpus path came back as the pointer —
  the URL conversion on Copilot works only when the skill loads), and `n6` 1/2 (asserted jEAP iss
  semantics as fact in the plain project). `n7` passed 2/2 this run — the previous run's genuinely
  wrong answer ("jEAP Messaging ignores `spring.kafka.consumer.group-id`") did not recur, notably
  with the skill loaded on that run; treat the wrong-answer mode as variance to watch, correlated
  with whether the skill is in context.

Takeaway: triggering, grounding and (on Claude Code) the public-URL conversion are solved,
including the marker-only and keyword-free debug cases. The open gaps are behavioural — Claude's
thrice-confirmed platform-lecture habit on plain-project diagnoses (`n6`), and on Copilot the
citation discipline and URL conversion that depend on its unreliable formal skill loading; the
durable Copilot-side fix would be `jeap-mcp-service` returning public URLs with its doc results.
Judge model for the grading: Claude Sonnet via `claude -p`.
