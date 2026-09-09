#!/usr/bin/env python3
"""Trigger evals for the jeap-expert skill in Claude Code.

Runs every query in queries.json via `claude -p` from its fixture directory and
records the stream-json transcript for analyze.py. The jeap-dev-toolkit plugin
must be installed in the Claude config that is used; for a clean measurement,
install it into a throwaway config first (see README.md) and pass it via
--config-dir.
"""
import argparse, json, os, subprocess, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP_TOOL_PREFIX = "mcp__plugin_jeap-dev-toolkit_jeap-mcp-service"

parser = argparse.ArgumentParser()
parser.add_argument("--workdir", default=str(HERE / "workdir"),
                    help="where results are written (default: evals/jeap-expert/workdir)")
parser.add_argument("--config-dir", default=None,
                    help="CLAUDE_CONFIG_DIR to use (default: the user's normal config)")
parser.add_argument("--workers", type=int, default=3)
parser.add_argument("--ids", nargs="*", help="run only these query ids")
args = parser.parse_args()

out_dir = Path(args.workdir) / "results" / "claude"
out_dir.mkdir(parents=True, exist_ok=True)
queries = json.loads((HERE / "queries.json").read_text())
if args.ids:
    queries = [q for q in queries if q["id"] in args.ids]

def run(item):
    qid = item["id"]
    env = dict(os.environ)
    if args.config_dir:
        env["CLAUDE_CONFIG_DIR"] = args.config_dir
    cmd = ["claude", "-p", item["q"],
           "--output-format", "stream-json", "--verbose",
           "--max-turns", "15",
           # WebFetch: the skill resolves doc citations to public URLs via the
           # sitemap of jeap-admin-ch.github.io — without a fetch tool that
           # part of the skill is untestable.
           "--allowedTools", "Skill", "Read", "Glob", "Grep", "WebFetch", MCP_TOOL_PREFIX]
    t0 = time.time()
    try:
        with open(out_dir / f"{qid}.jsonl", "w") as fo, open(out_dir / f"{qid}.err", "w") as fe:
            rc = subprocess.run(cmd, stdout=fo, stderr=fe, env=env,
                                cwd=HERE / "fixtures" / item["dir"], timeout=480).returncode
    except subprocess.TimeoutExpired:
        rc = -9
    dur = round(time.time() - t0, 1)
    print(f"{qid}: rc={rc} {dur}s", flush=True)
    return {"id": qid, "rc": rc, "duration_s": dur}

with ThreadPoolExecutor(max_workers=args.workers) as ex:
    meta = list(ex.map(run, queries))
(out_dir / "runs_meta.json").write_text(json.dumps(meta, indent=1))
print("ALL DONE — analyze with: python3 analyze.py --workdir", args.workdir)
