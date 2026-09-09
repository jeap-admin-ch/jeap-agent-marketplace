#!/usr/bin/env python3
"""Copilot A/B measurement for the jeap-expert skill.

Runs the `"copilot": true` queries from queries.json non-interactively:
  A-with-skill : the full plugin from this repo, via --plugin-dir — all
                 queries, including the should-not-trigger negatives, which
                 measure false fires under the skill's prompt pressure
  B-no-skill   : the same plugin stripped of the jeap-expert skill (MCP
                 server only) — should-trigger queries only; without the
                 skill there is nothing to misfire

Both variants run in throwaway HOME copies of ~/.copilot so an installed
jeap-dev-toolkit plugin cannot contaminate the baseline, and so nothing touches
the real Copilot state. Auth is inherited from ~/.copilot. Each run costs one
Copilot premium request. Session transcripts are saved via --share for
analyze.py.

Note: --allow-tool jeap-mcp-service is required because non-interactive runs
cannot ask for permission and silently deny the MCP tools otherwise. It does
NOT put the server's instructions into the system prompt — Copilot marks
non-allowlisted servers' instructions as "deferred" either way (verified with
Copilot CLI 1.0.78 debug logs).
"""
import argparse, json, os, re, shutil, subprocess, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_FULL = HERE.parent.parent / "plugins" / "jeap-dev-toolkit"

parser = argparse.ArgumentParser()
parser.add_argument("--workdir", default=str(HERE / "workdir"),
                    help="where homes, the stripped plugin and results go")
args = parser.parse_args()
# Resolve: workdir-derived paths (--plugin-dir, --share) are passed to copilot
# runs whose cwd is a fixture directory — relative, they would silently point
# nowhere (B variant loses the plugin entirely, exports fail with ENOENT).
work = Path(args.workdir).resolve()
out_dir = work / "results" / "copilot"
out_dir.mkdir(parents=True, exist_ok=True)

# Copilot silently drops any skill whose description frontmatter exceeds 1024
# characters — the A variant would then measure a plugin without the skill,
# invalidating the whole A/B. Fail fast instead.
for skill_md in PLUGIN_FULL.glob("skills/*/SKILL.md"):
    m = re.search(r"^description: (.*)$", skill_md.read_text(), re.M)
    if m and len(m.group(1)) > 1024:
        raise SystemExit(f"{skill_md.parent.name}: description is "
                         f"{len(m.group(1))} chars — Copilot drops skills over "
                         "1024 characters; shorten it before measuring.")

# Stripped plugin variant for the baseline.
noskill = work / "plugin-noskill"
if noskill.exists():
    shutil.rmtree(noskill)
shutil.copytree(PLUGIN_FULL, noskill)
shutil.rmtree(noskill / "skills" / "jeap-expert", ignore_errors=True)

# Isolated HOME copies. config.json is JSONC and uses camelCase keys since
# Copilot 1.0.x, but older snake_case copies still parse — clear both spellings.
def make_home(name):
    home = work / name
    if home.exists():
        shutil.rmtree(home)
    shutil.copytree(Path.home() / ".copilot", home / ".copilot",
                    ignore=shutil.ignore_patterns("logs", "session-state",
                                                  "session-store.db*",
                                                  "installed-plugins",
                                                  # not needed for runs; keep the
                                                  # user's prompt history out of it
                                                  "command-history-state.json"))
    cfg = home / ".copilot" / "config.json"
    d = json.loads(re.sub(r"^\s*//.*$", "", cfg.read_text(), flags=re.M))
    trusted = [str(HERE / "fixtures" / f) for f in ["jeap-fixture", "plain-fixture", "neutral"]]
    for k in ("installed_plugins", "installedPlugins"):
        d[k] = []
    for k in ("trusted_folders", "trustedFolders"):
        d[k] = sorted(set(d.get(k, []) + trusted))
    cfg.write_text(json.dumps(d, indent=1))
    return home

variants = [("A-with-skill", make_home("copilot-home-A"), PLUGIN_FULL),
            ("B-no-skill", make_home("copilot-home-B"), noskill)]
all_queries = [q for q in json.loads((HERE / "queries.json").read_text()) if q["copilot"]]

meta = []
for var, home, plugin in variants:
    queries = all_queries if var == "A-with-skill" else [q for q in all_queries if q["expect"]]
    for item in queries:
        qid = f'{item["id"]}--{var}'
        cmd = ["copilot", "--plugin-dir", str(plugin), "-p", item["q"],
               "--allow-tool", "jeap-mcp-service",
               # the skill resolves doc citations to public URLs via the
               # sitemap of the public docs site
               "--allow-url", "jeap-admin-ch.github.io",
               "--share", str(out_dir / f"{qid}.md"),
               "--log-level", "error", "--no-color"]
        t0 = time.time()
        try:
            with open(out_dir / f"{qid}.out", "w") as fo:
                rc = subprocess.run(cmd, stdout=fo, stderr=subprocess.STDOUT,
                                    env=dict(os.environ, HOME=str(home)),
                                    cwd=HERE / "fixtures" / item["dir"], timeout=480).returncode
        except subprocess.TimeoutExpired:
            rc = -9
        dur = round(time.time() - t0, 1)
        print(f"{qid}: rc={rc} {dur}s", flush=True)
        meta.append({"id": item["id"], "variant": var, "rc": rc, "duration_s": dur})
(out_dir / "runs_meta.json").write_text(json.dumps(meta, indent=1))
print("ALL DONE — analyze with: python3 analyze.py --workdir", args.workdir)
