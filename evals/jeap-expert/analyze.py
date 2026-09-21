#!/usr/bin/env python3
"""Aggregate the trigger-eval results produced by the two run_* scripts.

Claude (stream-json transcripts):
  skill_fired : a Skill tool_use naming jeap-expert
  mcp_calls   : jeap_* MCP tool_use count — grounding is the outcome that
                matters; the skill can steer without formally firing
Copilot (--share markdown transcripts):
  jeap_* tool invocations per run, compared A (with skill) vs B (no skill)
"""
import argparse, json, os, re
from pathlib import Path

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--workdir", default=str(HERE / "workdir"))
args = parser.parse_args()
results = Path(args.workdir) / "results"
queries = {q["id"]: q for q in json.loads((HERE / "queries.json").read_text())}

claude_dir = results / "claude"
if claude_dir.is_dir():
    print("=== Claude Code trigger evals ===")
    rows = []
    for f in sorted(claude_dir.glob("*.jsonl")):
        qid = f.stem
        skill, mcp = False, 0
        for line in f.open():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") != "assistant":
                continue
            for c in ev.get("message", {}).get("content", []):
                if c.get("type") != "tool_use":
                    continue
                name = c.get("name", "")
                if name == "Skill" and "jeap-expert" in json.dumps(c.get("input", {})):
                    skill = True
                if "jeap-mcp-service" in name or re.match(r"mcp__.*jeap_", name):
                    mcp += 1
        exp = queries[qid]["expect"]
        rows.append((qid, exp, skill, mcp, skill == exp))
        print(f"{qid:24} expect={str(exp):5} skill_fired={str(skill):5} "
              f"jeap_mcp_calls={mcp:2} {'OK' if skill == exp else 'MISS'}")
    pos = [r for r in rows if r[1]]
    neg = [r for r in rows if not r[1]]
    print(f"\nformal-trigger accuracy: {sum(1 for r in rows if r[4])}/{len(rows)}"
          f" | should-trigger fired: {sum(1 for r in pos if r[2])}/{len(pos)}"
          f" | should-not mistakenly fired: {sum(1 for r in neg if r[2])}/{len(neg)}")
    print(f"should-trigger runs grounded (>=1 jeap_* call): "
          f"{sum(1 for r in pos if r[3] > 0)}/{len(pos)}")

copilot_dir = results / "copilot"
if copilot_dir.is_dir():
    # Count only rendered tool-call headings — prose that merely mentions a
    # tool name ("I'll use jeap_find_code_examples") must not inflate counts.
    CALL_RE = re.compile(r"^### .*`jeap-mcp-service-jeap_[a-z_]+`", re.M)
    SKILL_RE = re.compile(r'"skill":\s*"jeap-expert"')
    ab = {}
    for f in sorted(copilot_dir.glob("*.md")):
        qid, var = f.stem.split("--")
        txt = f.read_text()
        ab.setdefault(qid, {})[var] = {"calls": len(CALL_RE.findall(txt)),
                                       "skill": bool(SKILL_RE.search(txt))}
    pos = {q: d for q, d in ab.items() if queries[q]["expect"]}
    neg = {q: d for q, d in ab.items() if not queries[q]["expect"]}

    print("\n=== Copilot A/B, should-trigger (jeap_* MCP calls per run) ===")
    print(f"{'query':24} {'A(with skill)':>14} {'B(no skill)':>12}   A skill_fired")
    for qid, d in sorted(pos.items()):
        a, b = d.get("A-with-skill", {}), d.get("B-no-skill", {})
        print(f"{qid:24} {a.get('calls', '-'):>14} {b.get('calls', '-'):>12}   {a.get('skill', '-')}")
    a_tot = sum(d.get("A-with-skill", {}).get("calls", 0) for d in pos.values())
    b_tot = sum(d.get("B-no-skill", {}).get("calls", 0) for d in pos.values())
    a_used = sum(1 for d in pos.values() if d.get("A-with-skill", {}).get("calls", 0) > 0)
    b_used = sum(1 for d in pos.values() if d.get("B-no-skill", {}).get("calls", 0) > 0)
    print(f"\ntotals: A={a_tot} calls ({a_used}/{len(pos)} runs used MCP)"
          f" | B={b_tot} calls ({b_used}/{len(pos)} runs used MCP)")

    if neg:
        print("\n=== Copilot should-NOT-trigger (run with the skill only) ===")
        for qid, d in sorted(neg.items()):
            a = d.get("A-with-skill", {})
            misfire = a.get("skill", False) or a.get("calls", 0) > 0
            print(f"{qid:24} skill_fired={str(a.get('skill', '-')):5} "
                  f"jeap_calls={a.get('calls', '-'):>2} {'MISFIRE' if misfire else 'OK'}")
