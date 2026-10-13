#!/usr/bin/env python3
"""Prepare local review material, assemble link tables and gate scratch cleanup.

No network access, deletion or editorial decisions. All outputs stay in scratch.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from confluence import AC, RI, NS, MigrationError, glossary_cell, local_name, parse_storage, write_file
from render_drawio import local_path, read_json, render


def visible_text(element):
    """Keep labels present in storage, including implicit Confluence references."""
    tag = local_name(element)
    if tag == "page":
        return element.get(f"{{{RI}}}content-title", element.get(f"{{{RI}}}content-id", ""))
    if tag == "attachment":
        return element.get(f"{{{RI}}}filename", "")
    if tag == "url":
        return element.get(f"{{{RI}}}value", "")
    if tag == "link":
        for name in ("link-body", "plain-text-link-body"):
            body = element.find(f"ac:{name}", NS)
            if body is not None:
                return visible_text(body)
    return (element.text or "") + "".join(visible_text(c) + (c.tail or "") for c in element)


def coverage(pid, version, tree):
    """Index structure, not inferred decisions: every table row and macro is included."""
    headings = []
    items = []
    # Element ordinals locate the saved XML even when headings repeat.
    for ordinal, element in enumerate(tree.iter(), 1):
        tag = local_name(element)
        kind = None
        details = {}
        if re.fullmatch(r"h[1-6]", tag):
            level = int(tag[1])
            headings = [(n, t) for n, t in headings if n < level]
            headings.append((level, visible_text(element).strip()))
            kind = "heading"
        elif tag == "tr":
            kind = "table-row"
            details["cells"] = [visible_text(c).strip() for c in element if local_name(c) in {"td", "th"}]
        elif tag == "task":
            kind = "task"
            details["task_status"] = element.findtext("ac:task-status", default="", namespaces=NS)
        elif tag in {"structured-macro", "macro"}:
            kind = "macro"
            details["macro"] = element.get(f"{{{AC}}}name", "unknown")
            details["parameters"] = [{"name": c.get(f"{{{AC}}}name", ""), "value": visible_text(c)}
                                     for c in element.findall("ac:parameter", NS)]
        if kind:
            items.append({"kind": "coverage", "page": pid, "version": version,
                          "source": f"sources/{pid}.storage.xml", "element": ordinal,
                          "section": [title for _, title in headings], "content_kind": kind,
                          "text": visible_text(element).strip(), **details})
    # A page without headings/tables still needs a destination and editorial review.
    items.insert(0, {"kind": "coverage", "page": pid, "version": version,
                     "source": f"sources/{pid}.storage.xml", "content_kind": "page",
                     "text": "Map the whole page and verify prose between indexed elements."})
    return items


def inventory(root):
    manifest = read_json(local_path(root, "migration.json"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("pages"), list):
        raise MigrationError("Invalid migration.json.")
    items = []
    for page in manifest["pages"]:
        pid = str(page["id"])
        source = local_path(root, f"sources/{pid}.storage.xml").read_bytes()
        tree = parse_storage(source.decode("utf-8"))
        for item in coverage(pid, page.get("version"), tree):
            item["source_sha256"] = hashlib.sha256(source).hexdigest()
            items.append(item)
    items += [{"kind": "issue", **issue} for issue in manifest.get("issues", [])]
    links = list(manifest.get("links_to_review", []))
    diagram_report = local_path(root, "diagram-report.json")
    if diagram_report.exists():
        for row in read_json(diagram_report):
            links.extend(row.get("links_to_review", []))
    grouped = {}
    for link in links:
        key = (str(link["page"]), link["target"])
        grouped.setdefault(key, [])
        if link["reason"] not in grouped[key]:
            grouped[key].append(link["reason"])
    items += [{"kind": "link", "page": page, "target": target, "reasons": reasons}
              for (page, target), reasons in grouped.items()]
    for item in items:
        item["id"] = hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        item.update(status="open", result="")
    return manifest, items


def reviewed_items(root):
    manifest, items = inventory(root)
    path = local_path(root, "review.json")
    if path.exists():
        saved = read_json(path)
        if not isinstance(saved, list) or any(not isinstance(r, dict) or not isinstance(r.get("id"), str) for r in saved):
            raise MigrationError("Invalid review.json.")
        if len({r["id"] for r in saved}) != len(saved):
            raise MigrationError("Duplicate review IDs.")
        answers = {r["id"]: r for r in saved}
        for item in items:
            answer = answers.get(item["id"], {})
            # Only the editorial answer is mutable. Deleted/new/stale items stay open.
            for key in ("status", "result"):
                item[key] = answer.get(key, item[key])
            if item["status"] not in {"open", "resolved", "omitted"} or not isinstance(item["result"], str):
                raise MigrationError("Review answers need status open/resolved/omitted and a text result.")
    return manifest, items


def cleanup_blockers(root, manifest, items):
    blockers = []
    if manifest.get("failed_pages") or not manifest.get("scope", {}).get("complete", False):
        blockers.append("Source scope is incomplete or pages failed; keep the export.")
    for item in items:
        if item["status"] == "open" or not item["result"].strip():
            blockers.append(f"Unresolved {item['kind']}: {item['id']}")
    for record in render(root, [], check_only=True):
        if record["status"] == "failed":
            blockers.append(f"Diagram {record.get('svg', record['source'])}: {record['reason']}")
    return blockers


def report_text(root, manifest, items, blockers):
    lines = ["# Migration review material", "", "Reconcile this material into the single final migration report.", "",
             "## Scope", ""]
    lines += [f"- Page {glossary_cell(str(p['id']))}, version {glossary_cell(str(p.get('version')))}"
              for p in manifest["pages"]]
    lines += ["", "## Links to review", "", "| Source page | Target | Reason | Result |", "| --- | --- | --- | --- |"]
    for item in items:
        if item["kind"] == "link":
            values = (item["page"], item["target"], " / ".join(item["reasons"]),
                      item["status"] + (": " + item["result"] if item["result"] else ""))
            lines.append("| " + " | ".join(glossary_cell(str(v)) for v in values) + " |")
    lines += ["", "## Coverage and conversion issues", "",
              "| Source | Item | Result |", "| --- | --- | --- |"]
    for item in items:
        if item["kind"] != "link":
            detail = item.get("message", item.get("text", ""))
            if item.get("content_kind") == "table-row":
                detail = " | ".join(item["cells"])
            source = f"Page {item['page']}" + (f", XML element {item['element']}" if "element" in item else "")
            lines.append("| " + " | ".join(glossary_cell(v) for v in
                         (source, detail, item["status"] + ": " + item["result"])) + " |")
    lines += ["", "## Scratch retention", ""]
    if blockers:
        lines += ["Keep working export: " + glossary_cell(str(root)), ""]
        lines += ["- " + glossary_cell(b) for b in blockers]
    else:
        lines += ["Cleanup checks passed. Remove only run-created scratch after the final report and target checks are complete."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "report", "check-cleanup"))
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--refresh", action="store_true", help="Refresh review.json after rendering/export changes, preserving answers for unchanged items (prepare only)")
    parser.add_argument("--output", default="review-material.md", help="Report filename inside scratch; existing differing files are not overwritten")
    args = parser.parse_args(argv)
    if args.refresh and args.action != "prepare":
        parser.error("--refresh is only supported by prepare")
    root = Path(args.work_dir).resolve()
    try:
        if args.action == "prepare":
            _, items = reviewed_items(root) if args.refresh else inventory(root)
            data = json.dumps(items, ensure_ascii=False, indent=2) + "\n"
            if args.refresh:
                local_path(root, "review.json").write_text(data, encoding="utf-8")
            else:
                write_file(root, "review.json", data)
            print("Prepared review.json; fill status and result with verified destinations or omission reasons.")
            return 0
        manifest, items = reviewed_items(root)
        blockers = cleanup_blockers(root, manifest, items)
        if args.action == "report":
            local_path(root, args.output)
            write_file(root, args.output, report_text(root, manifest, items, blockers))
        if blockers:
            print(f"Retain scratch: {root}. {len(blockers)} unresolved checks; see review material.")
            return 2
        print("Cleanup checks passed. This command has not deleted any files.")
        return 0
    except (MigrationError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"Error: {exc}; retain scratch at {root}.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
