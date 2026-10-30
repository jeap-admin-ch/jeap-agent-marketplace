#!/usr/bin/env python3
"""Configure a local OpenCode installation with the jeap-dev-toolkit MCP server and skills.

OpenCode has no marketplace/plugin-install mechanism, so this reproduces what one would
otherwise be installed by hand (see ../../../docs/opencode-setup.md):

- merges the mcp.jeap-mcp-service entry from this directory's opencode.json into the target
  OpenCode config (global ~/.config/opencode/opencode.json, or a project's ./opencode.json),
  leaving every other key in that file untouched;
- copies the selected skill folders from ../skills/ into the directory OpenCode scans for
  skills (global ~/.config/opencode/skills/, or a project's ./.opencode/skills/).

Safe to re-run: it only ever touches the single managed mcp key and overwrites its own
previously-copied skill folders, never other config or other skills. Run with --dry-run first
to see what would change without writing anything.

Requires Python 3.8+ (standard library only). No network access.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent               # plugins/jeap-dev-toolkit/.opencode-plugin
SKILLS_DIR = SCRIPT_DIR.parent / "skills"                   # plugins/jeap-dev-toolkit/skills
MCP_SNIPPET_PATH = SCRIPT_DIR / "opencode.json"             # this plugin's own OpenCode MCP snippet


def available_skills():
    return sorted(p.name for p in SKILLS_DIR.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())


def load_json_object(path):
    """Read a JSON object from path, or {} if it doesn't exist / is empty."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object at the top level.")
    return data


def merge_mcp(config_path, dry_run):
    """Merge this plugin's mcp.jeap-mcp-service entry into config_path. Returns whether it changed."""
    snippet = load_json_object(MCP_SNIPPET_PATH)
    entry = snippet.get("mcp", {}).get("jeap-mcp-service")
    if entry is None:
        raise ValueError(f"{MCP_SNIPPET_PATH} has no mcp.jeap-mcp-service entry; is this checkout intact?")
    config = load_json_object(config_path)
    config.setdefault("mcp", {})
    if not isinstance(config["mcp"], dict):
        raise ValueError(f"{config_path} has a non-object top-level 'mcp' key; merge it by hand.")
    before = config["mcp"].get("jeap-mcp-service")
    changed = before != entry
    config["mcp"]["jeap-mcp-service"] = entry
    if changed and not dry_run:
        if config_path.exists():
            # Keep a copy of what was there before we overwrite it.
            backup = config_path.with_name(config_path.name + ".bak")
            backup.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return changed


def copy_skills(names, dest_dir, dry_run):
    """Copy each named skill folder into dest_dir, overwriting a prior copy. Returns the (src, dst) pairs."""
    pairs = [(SKILLS_DIR / name, dest_dir / name) for name in names]
    if not dry_run:
        for src, dst in pairs:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst, dirs_exist_ok=True)
    return pairs


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scope", choices=("global", "project"), default="global",
                         help="global: ~/.config/opencode (default); "
                              "project: --project-dir's opencode.json and .opencode/skills/")
    parser.add_argument("--project-dir", default=".",
                         help="Project root to configure when --scope=project (default: current directory)")
    parser.add_argument("--skills", default="all",
                         help="Comma-separated skill names to install, or 'all' (default)")
    parser.add_argument("--list-skills", action="store_true",
                         help="Print the available skill names and exit; installs nothing")
    parser.add_argument("--dry-run", action="store_true",
                         help="Print what would change without writing anything")
    args = parser.parse_args(argv)

    all_skills = available_skills()
    if args.list_skills:
        print("\n".join(all_skills))
        return 0

    if args.skills == "all":
        names = all_skills
    else:
        names = [n.strip() for n in args.skills.split(",") if n.strip()]
        unknown = sorted(set(names) - set(all_skills))
        if unknown:
            print(f"Unknown skill(s): {', '.join(unknown)}. Available: {', '.join(all_skills)}",
                  file=sys.stderr)
            return 2

    if args.scope == "global":
        base = Path.home() / ".config" / "opencode"
        config_path = base / "opencode.json"
        skills_dir = base / "skills"
    else:
        base = Path(args.project_dir).resolve()
        config_path = base / "opencode.json"
        skills_dir = base / ".opencode" / "skills"

    try:
        changed = merge_mcp(config_path, args.dry_run)
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    copied = copy_skills(names, skills_dir, args.dry_run)

    if args.dry_run:
        print(f"Would {'update' if changed else 'leave unchanged'} mcp.jeap-mcp-service in {config_path}")
        for src, dst in copied:
            print(f"Would copy {src} -> {dst}")
        print("\nDry run only - nothing was written.")
        return 0

    print(f"{'Updated' if changed else 'Already up to date:'} mcp.jeap-mcp-service in {config_path}")
    for src, dst in copied:
        print(f"Copied {src} -> {dst}")
    print("\nVerify: run `opencode`, then `/mcp` and confirm jeap-mcp-service is connected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
