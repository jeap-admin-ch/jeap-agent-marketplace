"""Offline workflow regressions for review preparation and validation CLIs."""

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from confluence import Export, MigrationError, inline_literal, parse_storage, write_file
from render_drawio import check_pairs, render, smoke_test
from review_migration import coverage, inventory, main, cleanup_blockers
from test_migration import FixtureClient, SVG, DRAWIO, page


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class ReviewToolsTests(unittest.TestCase):
    def prepare(self, root):
        def renderer(cmd, cwd, **kwargs):
            (cwd / "output.svg").write_bytes(SVG)
            return subprocess.CompletedProcess(cmd, 0)
        Export(FixtureClient(), root, glossary_pages=["2"]).run()
        with patch("render_drawio.shutil.which", return_value="/bin/bwrap"), patch("render_drawio.subprocess.run", side_effect=renderer):
            render(root, ["drawio"])

    def test_validation_cli_from_unrelated_directory_without_pythonpath(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "diagram.svg"
            path.write_bytes(SVG)
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            cmd = [sys.executable, str(SCRIPTS / "render_drawio.py"), "--validate-svg", str(path)]
            result = subprocess.run(cmd, cwd=tmp, env=env, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            path.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.org/a.png"/></svg>')
            result = subprocess.run(cmd, cwd=tmp, env=env, capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(1, len(list(Path(tmp).iterdir())))

    def test_outline_preserves_table_decisions_tasks_and_jira_identity(self):
        tree = parse_storage('''<h2>Decisions</h2><table><tr><th>Choice</th><th>Reason</th></tr>
<tr><td><ac:link><ri:page ri:content-title="API"/></ac:link></td><td>Keep &amp;copy; literal</td></tr></table>
<ac:task-list><ac:task><ac:task-status>incomplete</ac:task-status><ac:task-body>Check delivery</ac:task-body></ac:task></ac:task-list>
<ac:structured-macro ac:name="jira"><ac:parameter ac:name="key">TEAM-42</ac:parameter></ac:structured-macro>
<h2>Decisions</h2>''')
        items = coverage("1", 7, tree)
        rows = [r for r in items if r["content_kind"] == "table-row"]
        self.assertEqual(["API", "Keep &copy; literal"], rows[1]["cells"])
        self.assertEqual(["Decisions"], rows[1]["section"])
        self.assertTrue(any(r["content_kind"] == "task" and "Check delivery" in r["text"] for r in items))
        macro = next(r for r in items if r["content_kind"] == "macro")
        self.assertEqual([{"name": "key", "value": "TEAM-42"}], macro["parameters"])
        headings = [r for r in items if r["content_kind"] == "heading"]
        self.assertNotEqual(headings[0]["element"], headings[1]["element"])

    def test_jira_references_are_reported_without_fetching_issues(self):
        client = FixtureClient()
        original = client.page
        storage = '''<ac:structured-macro ac:name="jira"><ac:parameter ac:name="key">TEAM-42</ac:parameter>
<ac:parameter ac:name="jqlQuery">project = TEAM AND status = Open</ac:parameter></ac:structured-macro>'''
        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "page", side_effect=lambda pid: page(pid, "Issues", storage) if pid == "1" else original(pid)):
            result = Export(client, tmp, glossary_pages=["2"]).run()
        self.assertIn("jira:key: TEAM-42", [r["target"] for r in result["links_to_review"]])
        self.assertIn("jira:jqlQuery: project = TEAM AND status = Open", [r["target"] for r in result["links_to_review"]])
        self.assertTrue(any("Macro 'jira'" in r["message"] for r in result["issues"]))

    def test_review_report_unions_links_and_preserves_one_row_per_source_target(self):
        with tempfile.TemporaryDirectory(prefix="review_test_") as tmp, patch("sys.stdout", new_callable=io.StringIO):
            root = Path(tmp)
            self.prepare(root)
            manifest = json.loads((root / "migration.json").read_text())
            link = {"page": "1", "target": "https://external.example/a|b?x=&copy;", "reason": "Source link"}
            manifest["links_to_review"] += [link, {**link, "reason": "Other source reason"}]
            (root / "migration.json").write_text(json.dumps(manifest))
            path = root / "diagram-report.json"
            diagrams = json.loads(path.read_text())
            diagrams[0]["links_to_review"] = [{**link, "reason": "SVG link"}, {**link, "page": "2"}]
            path.write_text(json.dumps(diagrams))
            self.assertEqual(0, main(["prepare", "--work-dir", tmp]))
            items = json.loads((root / "review.json").read_text())
            linked = [r for r in items if r.get("target") == link["target"]]
            self.assertEqual(2, len(linked))
            self.assertEqual(["Source link", "Other source reason", "SVG link"], linked[0]["reasons"])
            self.assertEqual(2, main(["report", "--work-dir", tmp]))
            text = (root / "review-material.md").read_text()
            self.assertEqual(2, len([line for line in text.splitlines() if "a\\|b?x=&amp;copy;" in line]))
            self.assertIn(str(root), inline_literal(text))
            self.assertIn("sources/1.storage.xml", (root / "review.json").read_text())

    def test_cleanup_gate_rechecks_deleted_changed_and_unresolved_items(self):
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stdout", new_callable=io.StringIO):
            root = Path(tmp)
            self.prepare(root)
            self.assertEqual(2, main(["check-cleanup", "--work-dir", tmp]))
            self.assertEqual(0, main(["prepare", "--work-dir", tmp]))
            path = root / "review.json"
            items = json.loads(path.read_text())
            for row in items:
                row.update(status="resolved", result="Reviewed against target.md#heading; evidence retained in final report")
            path.write_text(json.dumps(items))
            self.assertEqual(0, main(["check-cleanup", "--work-dir", tmp]))
            self.assertTrue((root / "sources/1.storage.xml").exists(), "Gate never deletes scratch")
            path.write_text(json.dumps(items[1:]))
            self.assertEqual(2, main(["check-cleanup", "--work-dir", tmp]))
            path.write_text(json.dumps(items))
            items[0]["result"] = ""
            path.write_text(json.dumps(items))
            self.assertEqual(2, main(["check-cleanup", "--work-dir", tmp]))
            items[0]["result"] = "Reviewed destination"
            path.write_text(json.dumps(items))
            source = root / "sources/1.storage.xml"
            source.write_text(source.read_text() + "<p>New requirement</p>")
            self.assertEqual(2, main(["check-cleanup", "--work-dir", tmp]))

    def test_cleanup_rejects_failed_scope_or_missing_svg_even_after_editorial_review(self):
        for failure in ("scope", "page", "svg"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.prepare(root)
                manifest, items = inventory(root)
                for row in items:
                    row.update(status="resolved", result="Reviewed")
                if failure == "scope":
                    manifest["scope"]["complete"] = False
                elif failure == "page":
                    manifest["failed_pages"] = ["3"]
                else:
                    (root / manifest["drawio"][0]["svgs"][0]).unlink()
                with patch("render_drawio.subprocess.run", side_effect=AssertionError("A check must never render")):
                    self.assertTrue(cleanup_blockers(root, manifest, items))

    def test_refresh_keeps_answers_and_opens_new_svg_links(self):
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stdout", new_callable=io.StringIO):
            root = Path(tmp)
            self.prepare(root)
            main(["prepare", "--work-dir", tmp])
            path = root / "review.json"
            items = json.loads(path.read_text())
            items[0].update(status="resolved", result="docs/strategy.md#decision")
            path.write_text(json.dumps(items))
            diagrams_path = root / "diagram-report.json"
            diagrams = json.loads(diagrams_path.read_text())
            diagrams[0]["links_to_review"] = [{"page": "1", "target": "https://example.org/new", "reason": "SVG link"}]
            diagrams_path.write_text(json.dumps(diagrams))
            self.assertEqual(0, main(["prepare", "--work-dir", tmp, "--refresh"]))
            updated = json.loads(path.read_text())
            self.assertEqual(items[0], updated[0])
            new = next(r for r in updated if r.get("target") == "https://example.org/new")
            self.assertEqual("open", new["status"])
            self.assertEqual("", new["result"])

    def test_review_commands_refuse_symlinks_and_report_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
            root = Path(tmp)
            self.prepare(root)
            report = root / "review-material.md"
            report.write_text("Manual report")
            self.assertEqual(1, main(["report", "--work-dir", tmp]))
            self.assertEqual("Manual report", report.read_text())
            (root / "review.json").symlink_to(report)
            self.assertEqual(1, main(["prepare", "--work-dir", tmp, "--refresh"]))
            self.assertEqual("Manual report", report.read_text())

    def test_check_mode_preserves_report_and_rejects_source_and_svg_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare(root)
            before = (root / "diagram-report.json").read_bytes()
            with patch("render_drawio.subprocess.run", side_effect=AssertionError("A check must never render")):
                self.assertTrue(all(r["status"].startswith("verified") for r in render(root, [], check_only=True)))
                source = next((root / "assets").glob("*.drawio"))
                source.write_bytes(DRAWIO.replace(b"Context", b"Changed"))
                self.assertTrue(all(r["status"] == "failed" for r in render(root, [], check_only=True)))
            self.assertEqual(before, (root / "diagram-report.json").read_bytes())

    def test_pair_check_supports_separate_roots_and_detects_missing_tabs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_file(root, "editable/a.drawio", DRAWIO)
            rows = []
            for name in ("one", "two"):
                write_file(root, f"docs/{name}.svg", SVG)
                rows.append({"source": "editable/a.drawio", "source_sha256": hashlib.sha256(DRAWIO).hexdigest(),
                             "svg": f"docs/{name}.svg", "svg_sha256": hashlib.sha256(SVG).hexdigest()})
            check_pairs(root, rows)
            with self.assertRaises(MigrationError):
                check_pairs(root, rows[:1])
            (root / "docs/one.svg").write_bytes(SVG.replace(b"Architecture", b"Changed"))
            with self.assertRaises(MigrationError):
                check_pairs(root, rows)
            rows[0]["source"] = "../outside.drawio"
            with self.assertRaises(MigrationError):
                check_pairs(root, rows)

    def test_pair_check_compares_puml_with_actual_fences_not_embedded_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = "@startuml\nA -> B\n@enduml\n"
            write_file(root, "editable/sequence.puml", source)
            write_file(root, "docs/runtime.md", "````markdown\n```plantuml\nWrong\n```\n````\n\n~~~plantuml\n" + source + "~~~\n")
            rows = [{"source": "editable/sequence.puml", "document": "docs/runtime.md", "fence": 1}]
            check_pairs(root, rows)
            (root / "editable/sequence.puml").write_text(source.replace("A -> B", "A -> C"))
            with self.assertRaises(MigrationError):
                check_pairs(root, rows)

    def test_smoke_test_exercises_two_tabs_and_detects_broken_installation(self):
        def renderer(cmd, cwd, **kwargs):
            tab = ET.fromstring((cwd / "input.drawio").read_bytes()).find("diagram")
            (cwd / "output.svg").write_bytes(SVG.replace(b"Architecture", tab.get("name").encode()))
            return subprocess.CompletedProcess(cmd, 0)
        with patch("render_drawio.shutil.which", return_value="/bin/bwrap"), patch("render_drawio.subprocess.run", side_effect=renderer) as call:
            smoke_test(["drawio"])
            self.assertEqual(2, call.call_count)
        with patch("render_drawio.shutil.which", return_value=None), self.assertRaises(MigrationError):
            smoke_test(["drawio"])


if __name__ == "__main__":
    unittest.main()
