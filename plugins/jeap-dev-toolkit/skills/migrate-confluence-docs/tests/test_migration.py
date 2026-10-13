"""Offline behavioural tests. No Confluence access or renderer installation required."""

import io
import base64
import http.client
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from confluence import (Confluence, Export, MigrationError, NoRedirect, diagram_links, drawio_pages,
                        main as export_main, parse_storage, plain, svg_paths, write_file)
from render_drawio import (ContainerCleanupError, local_docker_endpoint, main as render_main,
                          normalize_renderer_svg, render, render_command, validate_svg)


BASE = "https://wiki.example.org/confluence"
DRAWIO = b'<mxfile><diagram name="Context"><mxGraphModel><root/></mxGraphModel></diagram><diagram name="Detail">compressed-source-unchanged</diagram></mxfile>'
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><text x="1" y="2">Architecture</text></svg>'
STORAGE = '''
<h2>Architecture &amp; requirements</h2>
<p>API &lt; Gateway&nbsp;<strong>must</strong> stay.</p>
<ul><li>Service<ul><li>Database</li></ul></li></ul>
<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">java</ac:parameter><ac:plain-text-body><![CDATA[if (x < y) {
  print("``` & raw");
}]]></ac:plain-text-body></ac:structured-macro>
<ac:structured-macro ac:name="drawio"><ac:parameter ac:name="diagramName">Context</ac:parameter></ac:structured-macro>
<ac:structured-macro ac:name="plantuml"><ac:plain-text-body><![CDATA[@startuml
Alice -> Bob: <data> & message
!include https://not-selected.example/definitions.puml
@enduml]]></ac:plain-text-body></ac:structured-macro>
<ac:link><ri:page ri:content-id="2"/><ac:link-body>Glossary</ac:link-body></ac:link>
<ac:link><ri:page ri:content-title="Secret" ri:space-key="PRIVATE"/></ac:link>
<a href="https://external.example/path?q=1&amp;b=2">External</a>
<ac:image><ri:attachment ri:filename="photo.png"/></ac:image>
<ac:image><ri:attachment ri:filename="secret.png"><ri:page ri:content-id="99"/></ri:attachment></ac:image>
<ac:structured-macro ac:name="include"><ac:parameter ac:name=""><ri:page ri:content-id="99"/></ac:parameter></ac:structured-macro>
<ac:structured-macro ac:name="unknown"><ac:rich-text-body><p>Retained body</p></ac:rich-text-body></ac:structured-macro>
'''
GLOSSARY = '<table><tbody><tr><th>Term</th><th>Definition</th></tr><tr><td>API</td><td>Application programming interface</td></tr><tr><td>Gateway</td><td>Entry point</td></tr></tbody></table>'


def page(pid, title, storage):
    return {"id": pid, "type": "page", "title": title, "space": {"key": "ANYTEAM"},
            "version": {"number": 7}, "body": {"storage": {"value": storage}}}


def attachment(aid, title, media="image/png", pid="1"):
    return {"id": aid, "title": title, "metadata": {"mediaType": media},
            "_links": {"download": f"/download/attachments/{pid}/{title}"}}


class FixtureClient(Confluence):
    def __init__(self):
        super().__init__(BASE, ["1", "2"])
        self.requests = []

    def request(self, target):
        self.requests.append(target)
        responses = {
            BASE + "/rest/api/content/1?expand=body.storage,version,space": page("1", "Architecture", STORAGE),
            BASE + "/rest/api/content/2?expand=body.storage,version,space": page("2", "Glossary", GLOSSARY),
            BASE + "/rest/api/content/1/child/attachment?limit=100&start=0": {
                "results": [attachment("11", "Context", "application/vnd.jgraph.mxfile"), attachment("12", "photo.png")],
                "_links": {"next": "https://evil.example/not-followed"}},
            BASE + "/rest/api/content/1/child/attachment?limit=100&start=2": {
                "results": [attachment("13", "sequence.plantuml", "text/plain")], "_links": {}},
            BASE + "/rest/api/content/2/child/attachment?limit=100&start=0": {"results": [], "_links": {}},
        }
        if target in responses:
            return json.dumps(responses[target]).encode()
        binaries = {
            BASE + "/download/attachments/1/Context": DRAWIO,
            BASE + "/download/attachments/1/photo.png": b"PNG fixture",
            BASE + "/download/attachments/1/sequence.plantuml": b"@startuml\nA -> B\n@enduml",
        }
        if target in binaries:
            return binaries[target]
        raise AssertionError("Unexpected network access: " + target)


class MigrationTests(unittest.TestCase):
    def test_literal_declarations_in_code_and_comments_do_not_fail_export(self):
        examples = (
            '<!DOCTYPE html>\n<html><body>&copy;</body></html>',
            '<!DOCTYPE sample [<!ENTITY example "literal">]>\n<sample>&example;</sample>',
            '<!-- example comment -->\n<!DOCTYPE html>',
        )
        for example in examples:
            with self.subTest(example=example), tempfile.TemporaryDirectory() as tmp:
                storage = ('<!-- <!DOCTYPE ignored> <!ENTITY ignored "text"> <![CDATA[ -->'
                           '<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">xml</ac:parameter>'
                           '<ac:plain-text-body><![CDATA[' + example + ']]></ac:plain-text-body>'
                           '</ac:structured-macro><p>Following prose</p>')
                client = FixtureClient()
                original = client.page
                with patch.object(client, "page", side_effect=lambda pid: page(pid, "Examples", storage) if pid == "1" else original(pid)):
                    result = Export(client, tmp, glossary_pages=["2"]).run()
                self.assertEqual([], result["failed_pages"])
                draft = (Path(tmp) / "1-Examples.md").read_text()
                self.assertIn("```xml\n" + example + "\n```", draft)
                self.assertIn("Following prose", draft)
                self.assertNotIn("ignored", draft)
                self.assertEqual(storage, (Path(tmp) / "sources/1.storage.xml").read_text())

    def test_real_declarations_are_rejected_outside_cdata_and_comments(self):
        literals = ('<!-- <!DOCTYPE example> <![CDATA[ -->'
                    '<ac:plain-text-body><![CDATA[<!-- <!ENTITY literal "text">]]></ac:plain-text-body>')
        declarations = ('<!DOCTYPE root>', '<!DOCTYPE root [<!ENTITY x "value">]>',
                        '<!ENTITY x SYSTEM "file:///never-read">')
        for declaration in declarations:
            for storage in (declaration + literals, literals + declaration, '<!-- harmless -->' + declaration + literals):
                with self.subTest(storage=storage), self.assertRaisesRegex(MigrationError, "DTD/entity declarations"):
                    parse_storage(storage)

    def test_malformed_content_urls_are_reported_without_losing_page_drafts(self):
        targets = ('https://example.org:PORT/path', 'https://example.org:70000/path',
                   'https://[broken/path', '//[broken/path',
                   'https:///confluence/pages/viewpage.action?pageId=2')
        storage = ''.join(f'<p><a href="{html.escape(target, quote=True)}">Broken link</a></p>' for target in targets)
        client = FixtureClient()
        original = client.page
        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "page", side_effect=lambda pid: page(pid, "Architecture", storage) if pid == "1" else original(pid)):
            report = Export(client, tmp, glossary_pages=["2"]).run()
            self.assertEqual([], report["failed_pages"])
            self.assertTrue((Path(tmp) / '1-Architecture.md').is_file())
            self.assertTrue((Path(tmp) / '2-Glossary.md').is_file())
            saved_report = json.loads((Path(tmp) / 'migration.json').read_text())
        self.assertEqual(set(targets), {r['target'] for r in saved_report['links_to_review']})
        self.assertTrue(all('Malformed URL' in r['reason'] for r in saved_report['links_to_review']))
        self.assertTrue(all(request.startswith(BASE) for request in client.requests))

    def test_page_links_only_use_drafts_for_matching_versions(self):
        for query, local in (('pageId=2&pageVersion=3', False), ('pageId=2&pageVersion=7', True),
                             ('pageId=2&pageVersion=', False), ('pageId=2&pageVersion=7&pageVersion=3', False),
                             ('pageId=2', True)):
            with self.subTest(query=query), tempfile.TemporaryDirectory() as tmp:
                target = BASE + '/pages/viewpage.action?' + query
                storage = f'<p><a href="{html.escape(target, quote=True)}">Reference</a></p>'
                client = FixtureClient()
                original = client.page
                with patch.object(client, 'page', side_effect=lambda pid: page(pid, 'Architecture', storage) if pid == '1' else original(pid)):
                    report = Export(client, tmp, glossary_pages=['2']).run()
                draft = (Path(tmp) / '1-Architecture.md').read_text()
                if local:
                    self.assertIn('[Reference](<2-Glossary.md>)', draft)
                    self.assertFalse(report['links_to_review'])
                else:
                    self.assertNotIn('2-Glossary.md', draft)
                    self.assertIn(target.replace('&', '&amp;'), draft)
                    self.assertTrue(any(r['target'] == target and 'version' in r['reason'] for r in report['links_to_review']))

    def test_qualified_asset_and_page_action_links_are_not_retargeted(self):
        targets = (BASE + '/pages/viewpreviousversions.action?pageId=2',
                   BASE + '/pages/viewpage.action?pageId=2&focusedCommentId=123',
                   BASE + '/pages/viewpage.action?pageId=2&pageId=1',
                   'https://wiki.example.org/other/pages/viewpage.action?pageId=2',
                   BASE + '/download/attachments/1/photo.png?version=3',
                   BASE + '/download/attachments/1/photo.png#detail')
        storage = ''.join(f'<p><a href="{html.escape(target, quote=True)}">Reference</a></p>' for target in targets)
        client = FixtureClient()
        original = client.page
        with tempfile.TemporaryDirectory() as tmp, patch.object(client, 'page', side_effect=lambda pid: page(pid, 'Architecture', storage) if pid == '1' else original(pid)):
            report = Export(client, tmp, glossary_pages=['2']).run()
            draft = (Path(tmp) / '1-Architecture.md').read_text()
        for target in targets:
            self.assertIn(target.replace('&', '&amp;'), draft)
        self.assertEqual(set(targets), {r['target'] for r in report['links_to_review']})
        self.assertNotIn('assets/1-12-photo.png', draft)
        self.assertNotIn('2-Glossary.md', draft)

    def test_malformed_attachment_urls_are_reported_and_export_continues(self):
        for target in ('https://wiki.example.org:PORT/confluence/download/attachments/1/file',
                       'https://[broken/download/attachments/1/file'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                client = FixtureClient()
                original = client.attachments

                def attachments(pid):
                    rows = original(pid)
                    if pid == '1':
                        rows[0]['_links']['download'] = target
                    return rows

                with patch.object(client, 'attachments', side_effect=attachments):
                    report = Export(client, tmp, glossary_pages=['2']).run()
                self.assertEqual([], report['failed_pages'])
                self.assertTrue((Path(tmp) / 'assets/1-12-photo.png').exists())
                self.assertTrue((Path(tmp) / 'migration.json').exists())
                self.assertTrue(any('Malformed' in r['message'] for r in report['issues']))

    def test_malformed_configured_urls_fail_cleanly_before_network_access(self):
        for target in ('https://wiki.example.org:PORT', 'https://[broken', 'https://wiki.example.org:70000'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp, patch('sys.stderr', new_callable=io.StringIO) as errors:
                self.assertEqual(1, export_main(['--base-url', target, '--page', '1', '--work-dir', tmp]))
                self.assertIn('Malformed Confluence base URL', errors.getvalue())
                self.assertNotIn('Traceback', errors.getvalue())

    def test_plantuml_underscored_includes_are_reported_from_inline_and_attached_sources(self):
        source = b'@startuml\n!include_once common.puml\n!include_many "relative/repeated.puml"\n!include_once $base + "/dynamic.puml"\n!includesub parts.puml!PART\n!includeurl https://example.org/legacy.puml\n@enduml'
        expected = {'common.puml', 'relative/repeated.puml', '$base + "/dynamic.puml"',
                    'parts.puml!PART', 'https://example.org/legacy.puml'}
        for mode in ('inline', 'attached'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                client = FixtureClient()
                original_page, original_attachment = client.page, client.attachment
                storage = '<ac:structured-macro ac:name="plantuml"><ac:plain-text-body><![CDATA[' + source.decode() + ']]></ac:plain-text-body></ac:structured-macro>'
                with patch.object(client, 'page', side_effect=lambda pid: page(pid, 'Architecture', storage) if pid == '1' and mode == 'inline' else original_page(pid)), patch.object(client, 'attachment', side_effect=lambda pid, row: source if row['title'] == 'sequence.plantuml' and mode == 'attached' else original_attachment(pid, row)):
                    report = Export(client, tmp).run()
                targets = {r['target'] for r in report['links_to_review']}
                self.assertTrue(expected <= targets, targets)
                path = 'assets/1-plantuml-1.puml' if mode == 'inline' else 'assets/1-13-sequence.puml'
                self.assertEqual(source, (Path(tmp) / path).read_bytes())
                self.assertTrue(all(request.startswith(BASE) for request in client.requests))

    def test_plantuml_dynamic_arguments_and_parentheses_are_not_truncated(self):
        source = b'''!include_once "prefix" + $file
!include_many $root + "/common.puml"
!include_once 'literal path.puml'
!$a = %load_json("data(1).json")
!$b = %load_json($path + %string(7))
!$c = %load_json("prefix" + $file)
!$d = %load_json($unfinished
'''
        self.assertEqual({'"prefix" + $file', '$root + "/common.puml"', 'literal path.puml',
                          'data(1).json', '$path + %string(7)', '$unfinished'},
                         set(diagram_links(source, 'plantuml')))

    def test_prose_entities_are_literal_and_code_is_unchanged(self):
        storage = '''<h2>&amp;copy; and &amp;#169;</h2><p>&amp;copy; &amp;amp; &amp;lt;T&amp;gt; &amp;#x41; &amp;copy and ©</p>
<table><tr><td>&amp;copy;</td><td>Literal &amp;#169;</td></tr></table>
<p><a href="https://example.org/?example=&amp;copy;">&amp;copy;</a></p>
<p><code>&amp;copy;</code></p>
<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA[&copy; &amp;]]></ac:plain-text-body></ac:structured-macro>'''
        client = FixtureClient()
        original = client.page
        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "page", side_effect=lambda pid: page(pid, "Entity &copy;", storage) if pid == "1" else original(pid)):
            report = Export(client, tmp, glossary_pages=["2"]).run()
            document = (Path(tmp) / next(p["file"] for p in report["pages"] if p["id"] == "1")).read_text()
        self.assertIn('# Entity &amp;copy;', document)
        self.assertIn(r'## &amp;copy; and &amp;\#169;', document)
        self.assertIn(r'&amp;copy; &amp;amp; &amp;lt;T&amp;gt; &amp;\#x41; &amp;copy and ©', document)
        self.assertIn(r'| &amp;copy; | Literal &amp;\#169; |', document)
        self.assertIn('` &copy; `', document)
        self.assertIn('```\n&copy; &amp;\n```', document)
        self.assertIn('[&amp;copy;](<https://example.org/?example=&amp;copy;>)', document)

    def test_glossary_reference_labels_require_no_additional_fetches(self):
        storage = '''<table><tr><th>Term</th><th>Definition</th></tr>
<tr><td><ac:link><ri:page ri:content-title="API" ri:space-key="UNSELECTED"/></ac:link></td><td>Application interface</td></tr>
<tr><td><ac:link><ri:page ri:content-id="2"/></ac:link></td><td>Selected page title</td></tr>
<tr><td><ac:link><ri:page ri:content-title="Wrong"/><ac:link-body><strong>Explicit</strong> label</ac:link-body></ac:link></td><td>Explicit body wins</td></tr>
<tr><td>Definition links</td><td>See <ac:link><ri:page ri:content-title="API"/></ac:link> and <ac:link><ri:attachment ri:filename="schema.avsc"/></ac:link>.</td></tr>
<tr><td>Ordinary</td><td><p>First</p><p>Second<br/>Third</p></td></tr></table>'''
        client = FixtureClient()
        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "page", side_effect=lambda pid: page(pid, "Glossary" if pid == "2" else "Architecture", storage if pid == "2" else "<p>Source</p>")), patch.object(client, "attachments", return_value=[]):
            report = Export(client, tmp, glossary_pages=["2"]).run()
            content = (Path(tmp) / "12-glossary.md").read_text()
            draft = (Path(tmp) / "2-Glossary.md").read_text()
        self.assertIn('| API | Application interface |', content)
        self.assertIn('| Glossary | Selected page title |', content)
        self.assertIn('[Glossary](<2-Glossary.md>)', draft)
        self.assertIn('| Explicit label | Explicit body wins |', content)
        self.assertIn(r'| Definition links | See API and schema\.avsc\. |', content)
        self.assertIn('| Ordinary | First / Second / Third |', content)
        self.assertFalse(any("not imported" in issue["message"] for issue in report["issues"]))
        self.assertEqual([], client.requests)

    def test_unimportable_glossary_rows_are_reported_alongside_valid_rows(self):
        storage = '''<table><tr><th>Term</th><th>Definition</th></tr>
<tr><td>Valid</td><td>Retained</td></tr>
<tr><td><ac:link><ri:page ri:content-id="99"/></ac:link></td><td>Unknown title</td></tr>
<tr><td>Empty definition</td><td/></tr>
<tr><td>Missing column</td></tr>
<tr><td>Partial definition</td><td>Before <ac:link><ri:user ri:userkey="unresolved"/></ac:link> after</td></tr>
<tr><td>Unsupported inline content</td><td>State <ac:emoticon ac:name="tick"/></td></tr>
</table>'''
        client = FixtureClient()
        original = client.page
        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "page", side_effect=lambda pid: page(pid, "Glossary", storage) if pid == "2" else original(pid)):
            report = Export(client, tmp, glossary_pages=["2"]).run()
            content = (Path(tmp) / "12-glossary.md").read_text()
        self.assertIn('| Valid | Retained |', content)
        self.assertNotIn('Partial definition', content)
        self.assertNotIn('Unsupported inline content', content)
        for row in (3, 4, 5, 6, 7):
            self.assertTrue(any(f"Glossary table 1, row {row}" in issue["message"] for issue in report["issues"]))
        self.assertFalse(any('/99' in req for req in client.requests))

    def test_glossary_literal_text_survives_import_and_reimport(self):
        storage = r'''<table><tr><th>Term</th><th>Definition</th></tr>
<tr><td><code>&lt;T&gt;</code></td><td>Use *literal* and [label](url), `code`, ~~old~~ &amp;lt;tag&amp;gt; | C:\tmp.</td></tr>
<tr><td>**API**</td><td>Literal stars</td></tr>
<tr><td>API</td><td>Without stars</td></tr>
<tr><td>&amp;lt;T&amp;gt;</td><td>Literal entity</td></tr>
<tr><td>Multi
line</td><td>More
than one line</td></tr>
</table>'''
        client = FixtureClient()
        original = client.page
        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "page", side_effect=lambda pid: page(pid, "Glossary", storage) if pid == "2" else original(pid)):
            root = Path(tmp)
            first = root / "first"
            report = Export(client, first, glossary_pages=["2"]).run()
            content = (first / "12-glossary.md").read_text()
            self.assertIn(r'| &lt;T&gt; | Use \*literal\* and \[label\]\(url\), \`code\`, \~\~old\~\~ &amp;lt;tag&amp;gt; \| C:\\tmp\. |', content)
            self.assertIn(r'| \*\*API\*\* | Literal stars |', content)
            self.assertIn('| API | Without stars |', content)
            self.assertIn('| &amp;lt;T&amp;gt; | Literal entity |', content)
            self.assertIn('| Multi / line | More / than one line |', content)
            second = root / "second"
            second.mkdir()
            (second / "12-glossary.md").write_text(content)
            report = Export(client, second, glossary_pages=["2"]).run()
            self.assertEqual(content, (second / "12-glossary.md").read_text())
            self.assertFalse(any("Glossary conflict" in r["message"] for r in report["issues"]))

    def test_title_based_links_keep_anchors_in_drafts_and_review_reports(self):
        for title, space in (("Unselected page", "OTHER TEAM"), ("Unselected page", None),
                             ("Glossary", "ANYTEAM")):
            with self.subTest(title=title, space=space), tempfile.TemporaryDirectory() as tmp:
                anchor = "Details / Einschränkungen #1"
                space_attr = f' ri:space-key="{space}"' if space else ""
                storage = f'<ac:link ac:anchor="{anchor}"><ri:page ri:content-title="{title}"{space_attr}/><ac:plain-text-link-body>Details</ac:plain-text-link-body></ac:link>'
                client = FixtureClient()
                original = client.page
                with patch.object(client, "page", side_effect=lambda pid: page(pid, "Architecture", storage) if pid == "1" else original(pid)):
                    report = Export(client, tmp, glossary_pages=["2"]).run()
                href = (BASE + "/pages/viewpage.action?pageId=2" if title == "Glossary" else
                        BASE + "/display/" + urllib.parse.quote(space or "ANYTEAM", safe="") + "/" + urllib.parse.quote(title, safe=""))
                href += "#Details%20%2F%20Einschr%C3%A4nkungen%20%231"
                self.assertIn(f"[Details](<{href}>)", (Path(tmp) / "1-Architecture.md").read_text())
                self.assertIn(href, [r["target"] for r in report["links_to_review"]])
                self.assertIn(href.replace("#", "\\#"), (Path(tmp) / "links-to-review.md").read_text())
                self.assertFalse(any("/display/" in req for req in client.requests))

    def test_attachment_transport_failures_keep_drafts_and_reports(self):
        for phase in ("listing", "download"):
            for error in (TimeoutError("private-detail"), ConnectionResetError("private-detail"),
                          http.client.IncompleteRead(b"private-detail", 100)):
                with self.subTest(phase=phase, error=type(error).__name__), tempfile.TemporaryDirectory() as tmp:
                    fixture = FixtureClient()
                    client = Confluence(BASE, ["1", "2"])
                    failed_url = (BASE + "/rest/api/content/1/child/attachment?limit=100&start=0"
                                  if phase == "listing" else BASE + "/download/attachments/1/Context")

                    class BrokenResponse(io.BytesIO):
                        def read(self, *args):
                            raise error

                    def open_request(req, timeout):
                        return BrokenResponse() if req.full_url == failed_url else io.BytesIO(fixture.request(req.full_url))

                    with patch.object(client.opener, "open", side_effect=open_request):
                        report = Export(client, tmp, glossary_pages=["2"]).run()
                    root = Path(tmp)
                    self.assertEqual(["1", "2"], [p["id"] for p in report["pages"]])
                    self.assertTrue((root / "1-Architecture.md").is_file())
                    self.assertTrue((root / "2-Glossary.md").is_file())
                    self.assertTrue(any("response read failed" in issue["message"] for issue in report["issues"]))
                    self.assertNotIn("private-detail", (root / "migration.json").read_text())
                    self.assertIn("response read failed", (root / "migration-summary.md").read_text())
                    if phase == "download":
                        self.assertTrue((root / "assets/1-12-photo.png").is_file())
                        self.assertTrue((root / "assets/1-13-sequence.puml").is_file())
                        self.assertEqual("assets/1-11-Context.drawio", report["drawio"][0]["source"])

    def test_plantuml_code_macros_preserve_exact_sources_and_dependencies(self):
        source = '@startuml\n!include "relative/common.puml"\nA -> B: hello\n@enduml\n'
        for language in ("plantuml", "PlantUML", "puml"):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as tmp:
                storage = f'<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">{language}</ac:parameter><ac:plain-text-body><![CDATA[{source}]]></ac:plain-text-body></ac:structured-macro>'
                client = FixtureClient()
                original = client.page
                with patch.object(client, "page", side_effect=lambda pid: page(pid, "Architecture", storage) if pid == "1" else original(pid)):
                    report = Export(client, tmp, glossary_pages=["2"]).run()
                root = Path(tmp)
                self.assertEqual(source, (root / "assets/1-plantuml-1.puml").read_text())
                self.assertIn("```plantuml", (root / "1-Architecture.md").read_text())
                self.assertIn("relative/common.puml", [r["target"] for r in report["links_to_review"]])

    def test_glossary_row_headers_import_without_importing_column_headers(self):
        storage = '''<table><thead><tr><th>Term</th><td>Meaning</td></tr></thead><tbody>
<tr><th>API</th><td>Application interface</td></tr>
<tr><th scope="row">Gateway</th><td>Entry point</td></tr>
<tr><th scope="row">Queue</th><th>Message buffer</th></tr>
</tbody></table>'''
        client = FixtureClient()
        original = client.page
        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "page", side_effect=lambda pid: page(pid, "Glossary", storage) if pid == "2" else original(pid)):
            Export(client, tmp, glossary_pages=["2"]).run()
            glossary = (Path(tmp) / "12-glossary.md").read_text()
        self.assertIn("| API | Application interface |", glossary)
        self.assertIn("| Gateway | Entry point |", glossary)
        self.assertIn("| Queue | Message buffer |", glossary)
        self.assertNotIn("| Term | Meaning |", glossary)

    def test_attached_plantuml_links_and_includes_are_reported_without_fetching(self):
        client = FixtureClient()
        original = client.attachment
        source = b'@startuml\n!include https://external.example/common.puml\n!include <C4/C4_Context>\nA -> B: [[../concept.md Details]]\n!$x = %load_json("local-data.json")\n@enduml'
        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "attachment", side_effect=lambda pid, row: source if row["title"] == "sequence.plantuml" else original(pid, row)):
            report = Export(client, tmp).run()
            self.assertEqual(source, (Path(tmp) / "assets/1-13-sequence.puml").read_bytes())
        targets = {r["target"] for r in report["links_to_review"]}
        self.assertTrue({"https://external.example/common.puml", "<C4/C4_Context>", "../concept.md", "local-data.json"} <= targets)
        self.assertTrue(all(request.startswith(BASE) for request in client.requests))

    def test_drawio_compressed_and_plain_tabs_report_links_and_preserve_source(self):
        graph = '<mxGraphModel><root><UserObject link="https://external.example/design"><mxCell value="&lt;a href=&quot;../details.md&quot;&gt;Details&lt;/a&gt;" style="image=https://external.example/image.png;"/></UserObject></root></mxGraphModel>'
        compressor = zlib.compressobj(wbits=-15)
        encoded = urllib.parse.quote(graph).encode()
        compressed = base64.b64encode(compressor.compress(encoded) + compressor.flush()).decode()
        for body in (graph, compressed):
            data = ('<mxfile><diagram>' + body + '</diagram></mxfile>').encode()
            with self.subTest(compressed=body == compressed), tempfile.TemporaryDirectory() as tmp:
                client = FixtureClient()
                original = client.attachment
                with patch.object(client, "attachment", side_effect=lambda pid, row: data if row["title"] == "Context" else original(pid, row)):
                    report = Export(client, tmp).run()
                self.assertEqual(data, (Path(tmp) / "assets/1-11-Context.drawio").read_bytes())
                self.assertTrue({"https://external.example/design", "../details.md", "https://external.example/image.png"} <= {r["target"] for r in report["links_to_review"]})

    def test_unreadable_diagram_links_are_flagged_for_manual_review(self):
        for data, kind in ((b'\xff', "plantuml"), (DRAWIO, "drawio")):
            with self.subTest(kind=kind), self.assertRaises(MigrationError):
                diagram_links(data, kind)

    def test_ordered_list_start_jumps_and_table_pipes_are_preserved(self):
        storage = '<ol start="5"><li>Five</li><li>Six</li></ol><ol start="3"><li>Three</li><li value="7">Seven</li></ol><table><tr><th>Expression</th></tr><tr><td>A | B</td></tr><tr><td><code>A | B</code></td></tr></table>'
        client = FixtureClient()
        original = client.page
        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "page", side_effect=lambda pid: page(pid, "Architecture", storage) if pid == "1" else original(pid)):
            report = Export(client, tmp).run()
            markdown = (Path(tmp) / "1-Architecture.md").read_text()
        self.assertIn("5. Five\n6. Six", markdown)
        self.assertIn("3\\. Three", markdown)
        self.assertIn("7\\. Seven", markdown)
        self.assertIn(r"A \| B", markdown)
        self.assertNotIn("&#124;", markdown)
        self.assertTrue(any("Nonsequential" in r["message"] for r in report["issues"]))

    def test_glossary_pipe_terms_are_not_duplicated_on_merge(self):
        client = FixtureClient()
        original = client.page
        storage = '<table><tr><td>A | B</td><td>Either A | B</td></tr></table>'
        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "page", side_effect=lambda pid: page(pid, "Glossary", storage) if pid == "2" else original(pid)):
            root = Path(tmp)
            original_glossary = '# Glossary\n\n| Term | Meaning |\n| --- | --- |\n| A \\| B | Either A \\| B |\n'
            (root / "12-glossary.md").write_text(original_glossary)
            Export(client, tmp, glossary_pages=["2"]).run()
            self.assertEqual(original_glossary, (root / "12-glossary.md").read_text())

    def test_tree_discovery_uses_only_scoped_child_endpoints_and_records_hierarchy(self):
        client = Confluence(BASE, ["1"])
        batches = {
            "content/1/child/page?limit=100&start=0": {"results": [{"id": "2", "type": "page"}], "_links": {"next": "https://evil.example/search"}},
            "content/1/child/page?limit=100&start=1": {"results": [{"id": "3", "type": "page"}]},
            "content/2/child/page?limit=100&start=0": {"results": [{"id": "4", "type": "page"}]},
            "content/3/child/page?limit=100&start=0": {"results": []},
            "content/4/child/page?limit=100&start=0": {"results": []},
        }
        with patch.object(client, "api", side_effect=lambda path: batches[path]) as api:
            scope = client.discover_descendants()
        self.assertTrue(scope["complete"])
        self.assertEqual({"1", "2", "3", "4"}, client.allowed)
        self.assertEqual([{"parent": "1", "child": "2"}, {"parent": "1", "child": "3"}, {"parent": "2", "child": "4"}], scope["hierarchy"])
        self.assertEqual(5, api.call_count)

    def test_tree_limits_errors_and_cycles_never_silently_complete(self):
        for mode in ("page-limit", "depth-limit", "forbidden", "cycle", "duplicate", "empty-next"):
            with self.subTest(mode=mode):
                client = Confluence(BASE, ["1"])

                def response(path):
                    if mode == "forbidden":
                        raise MigrationError("Confluence returned HTTP 403.")
                    if mode == "empty-next":
                        return {"results": [], "_links": {"next": "/more"}}
                    child = "1" if mode == "cycle" else "2"
                    return {"results": [{"id": child, "type": "page"}], "_links": {"next": "/more"} if mode == "duplicate" else {}}

                with patch.object(client, "api", side_effect=response):
                    scope = client.discover_descendants(max_pages=1 if mode == "page-limit" else 100, max_depth=0 if mode == "depth-limit" else 20)
                self.assertFalse(scope["complete"])
                self.assertTrue(scope["errors"])
                if mode in {"page-limit", "depth-limit", "forbidden", "empty-next"}:
                    self.assertEqual({"1"}, client.allowed)

    def test_tree_cli_adds_descendants_and_reports_partial_discovery(self):
        client = FixtureClient()
        client.allowed = {"1"}
        client.roots = ["1"]
        client.scope["roots"] = ["1"]

        def children(pid):
            if pid == "1":
                yield "2"
            else:
                raise MigrationError("Confluence returned HTTP 403.")

        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "children", side_effect=children), patch("confluence.Confluence", return_value=client), patch("sys.stdout", new_callable=io.StringIO):
            result = export_main(["--base-url", BASE, "--page", "1", "--include-descendants", "--work-dir", tmp])
            report = json.loads((Path(tmp) / "migration.json").read_text())
        self.assertEqual(2, result)
        self.assertEqual(["1", "2"], report["selected_pages"])
        self.assertFalse(report["scope"]["complete"])
        self.assertEqual([{"parent": "1", "child": "2"}], report["scope"]["hierarchy"])

    def test_failed_conversion_or_write_preserves_incoming_confluence_links(self):
        for failure in ("xml", "write"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                client = FixtureClient()
                original_page = client.page

                def get_page(pid):
                    return page("2", "Glossary", "<p>broken") if failure == "xml" and pid == "2" else original_page(pid)

                def write(root, relative, data):
                    if failure == "write" and relative == "2-Glossary.md":
                        raise OSError("Simulated write failure")
                    return write_file(root, relative, data)

                with patch.object(client, "page", side_effect=get_page), patch("confluence.write_file", side_effect=write):
                    report = Export(client, tmp, glossary_pages=["2"]).run()
                text = (Path(tmp) / "1-Architecture.md").read_text()
                self.assertNotIn("2-Glossary.md", text)
                self.assertNotIn("migration-page-link-", text)
                self.assertIn(BASE + "/pages/viewpage.action?pageId=2", text)
                self.assertEqual(["2"], report["failed_pages"])
                self.assertTrue(any("not migrated" in r["reason"] for r in report["links_to_review"]))
                self.assertNotIn("[2]", (Path(tmp) / "12-glossary.md").read_text())

    def test_markdown_breaks_anchors_and_table_cells_need_no_raw_html(self):
        storage = '''<p>First<br/>Second</p>
<ac:structured-macro ac:name="anchor"><ac:parameter ac:name="">explicit</ac:parameter></ac:structured-macro>
<a href="#explicit">Here</a><ac:link ac:anchor="explicit"/>
<ac:link ac:anchor="explicit"><ri:page ri:content-id="2"/></ac:link>
<table><tr><th>Term</th></tr><tr><td>One<br/>Two<p>Three</p></td></tr></table>'''
        client = FixtureClient()
        original = client.page
        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "page", side_effect=lambda pid: page(pid, "Architecture", storage) if pid == "1" else original(pid)):
            report = Export(client, tmp).run()
            text = (Path(tmp) / "1-Architecture.md").read_text()
        self.assertIn("First  \nSecond", text)
        self.assertIn("| One / Two / Three |", text)
        self.assertNotIn("<br>", text)
        self.assertNotIn("<a ", text)
        self.assertNotIn("2-Glossary.md#", text)
        self.assertIn("pageId=2#explicit", text)
        self.assertTrue(any("Multiline table" in r["message"] for r in report["issues"]))
        self.assertTrue(any("Explicit Confluence anchor" in r["reason"] for r in report["links_to_review"]))

    def test_admonitions_preserve_type_title_and_all_paragraphs(self):
        for kind in ("warning", "note", "info", "tip"):
            for title in ("", "Important"):
                with self.subTest(kind=kind, title=title), tempfile.TemporaryDirectory() as tmp:
                    storage = f'<ac:structured-macro ac:name="{kind}"><ac:parameter ac:name="title">{title}</ac:parameter><ac:rich-text-body><p>Keep authentication.</p><p>Review access.</p></ac:rich-text-body></ac:structured-macro>'
                    client = FixtureClient()
                    client.allowed = {"1"}
                    with patch.object(client, "page", return_value=page("1", "Architecture", storage)):
                        Export(client, tmp).run()
                    text = (Path(tmp) / "1-Architecture.md").read_text()
                    self.assertIn("> **" + kind.capitalize() + (": " + title if title else "") + "**", text)
                    self.assertIn("> Keep authentication.", text)
                    self.assertIn("> Review access.", text)

    def test_end_to_end_export_preserves_sources_and_scope(self):
        client = FixtureClient()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "12-glossary.md").write_text("# Existing chapter 12\n\n| Term | Definition |\n| --- | --- |\n| API | Existing team definition |\n")
            report = Export(client, root, glossary_pages=["2"]).run()
            markdown = (root / "1-Architecture.md").read_text()
            self.assertIn("Architecture &amp; requirements", markdown)
            self.assertIn("**must** stay.", markdown)
            self.assertIn('  print("``` & raw");', markdown)
            self.assertIn("2-Glossary.md", markdown)
            self.assertIn("assets/1-11-Context.svg", markdown)
            self.assertIn("assets/1-11-Context-page-2.svg", markdown)
            self.assertIn("Retained body", markdown)
            self.assertIn("assets/1-12-photo.png", markdown)
            self.assertEqual(DRAWIO, (root / "assets/1-11-Context.drawio").read_bytes())
            self.assertEqual("@startuml\nAlice -> Bob: <data> & message\n!include https://not-selected.example/definitions.puml\n@enduml", (root / "assets/1-plantuml-1.puml").read_text())
            self.assertTrue((root / "assets/1-13-sequence.puml").exists())
            self.assertEqual(STORAGE, (root / "sources/1.storage.xml").read_text())
            glossary = (root / "12-glossary.md").read_text()
            self.assertIn("Existing team definition", glossary)
            self.assertIn("Gateway | Entry point", glossary)
            self.assertNotIn("Application programming interface", glossary)
            self.assertTrue(any("Glossary conflict" in i["message"] for i in report["issues"]))
            links = (root / "links-to-review.md").read_text()
            self.assertIn("external.example", links)
            self.assertIn("Secret", links)
            self.assertIn("99", links)
            self.assertTrue((root / "migration-summary.md").exists())
            self.assertFalse(any("export_view" in r or "child/page" in r or "/99" in r for r in client.requests))
            self.assertTrue(all(r.startswith(BASE) for r in client.requests))
            with self.assertRaises(MigrationError):
                Export(client, root).run()

    def test_page_selection_is_validated_before_network_access(self):
        for selected in ["https://evil.example/pages/1", "https://wiki.example.org/other/pages/1", "../1", "https://wiki.example.org/confluence/display/SPACE/Title"]:
            with self.subTest(selected=selected), self.assertRaises(MigrationError):
                Confluence(BASE, [selected])
        client = Confluence(BASE, [BASE + "/spaces/TEAM/pages/1/Name"])
        with self.assertRaises(MigrationError):
            client.page("99")
        with self.assertRaises(MigrationError):
            client.attachments("99")
        with self.assertRaises(MigrationError):
            Export(client, "/unused", glossary_pages=["99"])

    def test_downloads_cannot_escape_page_or_origin(self):
        client = Confluence(BASE, ["1"])
        for target in ["https://evil.example/file", "/confluence/download/attachments/99/file", "/confluence/rest/api/content/99", "/confluence/download/attachments/1/../99/file", "/confluence/download/attachments/1/%2e%2e%2ffile",
                       "/download/attachments/99/file", "/download/attachments/1/../99/file", "/download/attachments/1/%252e%252e%252ffile",
                       "//evil.example/download/attachments/1/file", "https://wiki.example.org/download/attachments/1/file",
                       "/other/download/attachments/1/file", "https://user:password@wiki.example.org/confluence/download/attachments/1/file",
                       "https://wiki.example.org:0/confluence/download/attachments/1/file"]:
            with self.subTest(target=target), patch.object(client, "request", side_effect=AssertionError("Rejected download must not access the network")), self.assertRaises(MigrationError):
                client.attachment("1", {"_links": {"download": target}})
        with self.assertRaises(MigrationError):
            NoRedirect().redirect_request(None, None, 302, "", {}, BASE + "/pages/99")

    def test_attachment_links_resolve_with_and_without_application_context(self):
        suffix = "/download/attachments/1/test%20file.txt?version=2&modificationDate=123"
        for context in ("", "/confluence", "/wiki/confluence"):
            base = "https://wiki.example.org" + context
            links = (suffix, suffix.lstrip("/"), context + suffix, base + suffix,
                     "//wiki.example.org" + context + suffix)
            for link in links:
                with self.subTest(context=context, link=link):
                    client = Confluence(base, ["1"])
                    with patch.object(client.opener, "open", return_value=io.BytesIO(b"attachment")) as opened:
                        self.assertEqual(b"attachment", client.attachment("1", {"_links": {"download": link}}))
                    self.assertEqual(base + suffix, opened.call_args.args[0].full_url)
                    self.assertIsNone(opened.call_args.args[0].get_header("Authorization"))

    def test_masked_404_uses_configured_pat_and_exports_context_relative_attachments(self):
        for credential in ("file", "environment"):
            with self.subTest(credential=credential), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                pat_file = root / "pat"
                pat_file.write_text("test-secret\n")
                settings = ({"CONFLUENCE_PAT_FILE": str(pat_file)} if credential == "file"
                            else {"CONFLUENCE_TOKEN": "test-secret"})
                client = Confluence(BASE, ["1", "2"])
                fixture = FixtureClient()
                calls = []

                def open_request(req, timeout):
                    calls.append(req)
                    if req.get_header("Authorization") is None:
                        raise urllib.error.HTTPError(req.full_url, 404, "", {}, None)
                    self.assertEqual("Bearer test-secret", req.get_header("Authorization"))
                    return io.BytesIO(fixture.request(req.full_url))

                with patch.dict(os.environ, settings, clear=True), patch.object(client.opener, "open", side_effect=open_request):
                    report = Export(client, root / "export", glossary_pages=["2"]).run()
                self.assertEqual([], report["failed_pages"])
                self.assertEqual(3, len(report["attachments"]))
                self.assertEqual(calls[0].full_url, calls[1].full_url)
                self.assertEqual(1, sum(req.get_header("Authorization") is None for req in calls))
                self.assertEqual(DRAWIO, (root / "export/assets/1-11-Context.drawio").read_bytes())
                self.assertNotIn("test-secret", (root / "export/migration.json").read_text())
                self.assertEqual("test-secret\n", pat_file.read_text())

    def test_404_without_configured_credentials_stays_404_without_retry(self):
        client = Confluence(BASE, ["1"])
        with patch.dict(os.environ, {}, clear=True), patch.object(client, "credentials", side_effect=AssertionError("No credentials configured")), patch.object(client.opener, "open", side_effect=urllib.error.HTTPError(BASE, 404, "", {}, None)) as opened:
            with self.assertRaisesRegex(MigrationError, "HTTP 404"):
                client.page("1")
        self.assertEqual(1, opened.call_count)

    def test_authenticated_404_is_not_retried_or_searched(self):
        client = Confluence(BASE, ["1"])
        with patch.dict(os.environ, {"CONFLUENCE_TOKEN": "test-secret"}, clear=True), patch.object(client.opener, "open", side_effect=urllib.error.HTTPError(BASE, 404, "test-secret", {}, None)) as opened:
            with self.assertRaisesRegex(MigrationError, "HTTP 404") as error:
                client.page("1")
        self.assertEqual(2, opened.call_count)
        requests = [call.args[0] for call in opened.call_args_list]
        self.assertEqual(requests[0].full_url, requests[1].full_url)
        self.assertEqual([None, "Bearer test-secret"], [req.get_header("Authorization") for req in requests])
        self.assertNotIn("test-secret", str(error.exception))

    def test_pat_used_only_after_auth_required_and_never_in_errors(self):
        client = Confluence(BASE, ["1"])
        target = BASE + "/rest/api/content/1"
        calls = []

        def open_request(req, timeout):
            calls.append(req)
            if len(calls) == 1:
                raise urllib.error.HTTPError(target, 401, "", {}, None)
            return io.BytesIO(b"payload")

        with patch.dict(os.environ, {"CONFLUENCE_TOKEN": "test-secret"}, clear=True), patch.object(client.opener, "open", side_effect=open_request):
            self.assertEqual(b"payload", client.request(target))
        self.assertIsNone(calls[0].get_header("Authorization"))
        self.assertEqual("Bearer test-secret", calls[1].get_header("Authorization"))
        with patch.object(client.opener, "open", side_effect=urllib.error.HTTPError(target, 403, "test-secret", {}, io.BytesIO(b"test-secret"))):
            with self.assertRaises(MigrationError) as caught:
                client.request(target)
            self.assertNotIn("test-secret", str(caught.exception))

    def test_public_access_does_not_read_credentials(self):
        client = Confluence(BASE, ["1"])
        with patch.object(client, "credentials", side_effect=AssertionError("Must not read credentials")), patch.object(client.opener, "open", return_value=io.BytesIO(b"public")):
            self.assertEqual(b"public", client.request(BASE + "/rest/api/content/1"))

    def test_failed_page_is_reported_and_remaining_pages_convert(self):
        client = FixtureClient()
        original = client.page

        def failing(pid):
            if pid == "1":
                raise MigrationError("Confluence returned HTTP 403.")
            return original(pid)

        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "page", side_effect=failing):
            report = Export(client, tmp, glossary_pages=["2"]).run()
            self.assertEqual(["1"], report["failed_pages"])
            self.assertEqual(["2"], [p["id"] for p in report["pages"]])
            self.assertIn("403", (Path(tmp) / "migration-summary.md").read_text())

    def test_output_traversal_symlink_and_overwrites_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "out"
            root.mkdir()
            write_file(root, "a.md", "existing")
            with self.assertRaises(MigrationError):
                write_file(root, "a.md", "new")
            with self.assertRaises(MigrationError):
                write_file(root, "../escape", "no")
            (root / "assets").symlink_to(Path(tmp), target_is_directory=True)
            with self.assertRaises(MigrationError):
                write_file(root, "assets/escape", "no")
            self.assertEqual("existing", (root / "a.md").read_text())

    def test_nested_glossary_links_resolve_and_existing_entries_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = Export(FixtureClient(), root, glossary="chapters/12.md", glossary_pages=["2"]).run()
            glossary = (root / report["glossary"]).read_text()
            self.assertIn("[2](../2-Glossary.md)", glossary)
            self.assertTrue((root / "chapters" / "../2-Glossary.md").is_file())

    def test_formatted_glossary_terms_are_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = "---\ntitle: Glossary\n---\n\n# Glossary\n\n| Term | Meaning |\n| --- | --- |\n| [**API**](https://example.org/api) | Established team meaning |\n| **Gateway** | Entry point |\n"
            (root / "glossary.md").write_text(original)
            report = Export(FixtureClient(), root, glossary="glossary.md", glossary_pages=["2"]).run()
            self.assertEqual(original, (root / "glossary.md").read_text())
            self.assertTrue(any("Glossary conflict for API" in issue["message"] for issue in report["issues"]))

    def test_cli_working_export_and_legacy_alias_preserve_target_docs(self):
        for option in ("--work-dir", "--output"):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                docs = root / "repo" / "docs"
                docs.mkdir(parents=True)
                (docs / "existing.md").write_text("Existing documentation")
                scratch = root / "scratch"
                with patch("confluence.Confluence", return_value=FixtureClient()), patch("sys.stdout", new_callable=io.StringIO):
                    result = export_main(["--base-url", BASE, "--page", "1", "--page", "2", option, str(scratch), "--glossary-page", "2"])
                self.assertEqual(2, result)  # The fixture contains unresolved macros.
                self.assertTrue((scratch / "migration.json").exists())
                self.assertTrue((scratch / "1-Architecture.md").exists())
                self.assertEqual(["existing.md"], [p.name for p in docs.iterdir()])
                self.assertEqual("Existing documentation", (docs / "existing.md").read_text())

    def test_unsafe_attachment_titles_remain_inside_assets(self):
        client = FixtureClient()
        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "attachments", return_value=[attachment("13", "../../diagram.drawio", "application/vnd.jgraph.mxfile")]), patch.object(client, "attachment", return_value=DRAWIO):
            root = Path(tmp)
            report = Export(client, root, glossary_pages=["2"]).run()
            for asset in report["attachments"]:
                self.assertTrue((root / asset).resolve().is_relative_to(root / "assets"))
                self.assertEqual(DRAWIO, (root / asset).read_bytes())

    def test_cdata_and_named_entities(self):
        tree = parse_storage('<p>A&nbsp;B &copy;</p><ac:plain-text-body><![CDATA[a &amp; <b>]]></ac:plain-text-body>')
        self.assertEqual("A\u00a0B ©a &amp; <b>", plain(tree))
        with self.assertRaises(MigrationError):
            parse_storage('<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]><p>&x;</p>')

    def test_failed_drawio_download_remains_in_expected_inventory(self):
        client = FixtureClient()
        original = client.attachment

        def download(pid, row):
            if row["title"] == "Context":
                raise MigrationError("Confluence returned HTTP 403.")
            return original(pid, row)

        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "attachment", side_effect=download):
            report = Export(client, tmp).run()
            self.assertEqual("assets/1-11-Context.drawio", report["drawio"][0]["source"])
            self.assertEqual([], report["drawio"][0]["svgs"])
            result = render(Path(tmp), ["drawio"])
            self.assertEqual("failed", result[0]["status"])


class RenderingTests(unittest.TestCase):
    def test_native_renderer_mounts_no_host_home_root_or_display(self):
        with patch("render_drawio.shutil.which", return_value="/bin/bwrap"):
            cmd = render_command(["drawio"], None, Path("/tmp/work"))
        mounts = [cmd[i + 1:i + 3] for i, arg in enumerate(cmd) if arg in {"--ro-bind", "--bind"}]
        self.assertNotIn(["/", "/"], mounts)
        self.assertTrue(all(source not in {"/home", "/root", "/run", "/tmp/.X11-unix"} for source, _ in mounts))
        self.assertIn(["/tmp/work", "/work"], mounts)
        self.assertIn("--clearenv", cmd)
        self.assertIn("--unshare-pid", cmd)
        self.assertIn("--new-session", cmd)
        self.assertIn("xvfb-run", cmd)
        self.assertNotIn("DISPLAY", cmd)
        self.assertNotIn("XAUTHORITY", cmd)

    def test_generated_and_reused_svg_links_are_in_diagram_report(self):
        linked_svg = SVG.replace(b"</svg>", b'<a href="https://external.example/svg"><text>Details</text></a></svg>')

        def renderer(cmd, cwd, env, **kwargs):
            self.assertNotIn("XAUTHORITY", env)
            self.assertNotIn("DISPLAY", env)
            (cwd / "output.svg").write_bytes(linked_svg)
            return subprocess.CompletedProcess(cmd, 0)

        with tempfile.TemporaryDirectory() as tmp, patch("render_drawio.shutil.which", return_value="/bin/bwrap"), patch("render_drawio.subprocess.run", side_effect=renderer), patch.dict(os.environ, {"DISPLAY": ":0", "XAUTHORITY": "/home/private/.Xauthority"}):
            root = Path(tmp)
            self.prepare(root)
            for _ in range(2):
                results = render(root, ["drawio"])
                for result in results:
                    self.assertEqual("https://external.example/svg", result["links_to_review"][0]["target"])
                self.assertEqual(results, json.loads((root / "diagram-report.json").read_text()))

    def prepare(self, root):
        write_file(root, "assets/context.drawio", DRAWIO)
        write_file(root, "migration.json", json.dumps({"drawio": [{"source": "assets/context.drawio", "svgs": svg_paths("assets/context.drawio", 2)}]}))

    def test_renderer_cli_accepts_work_dir(self):
        with tempfile.TemporaryDirectory() as tmp, patch("render_drawio.shutil.which", return_value=None), patch("sys.stdout", new_callable=io.StringIO):
            root = Path(tmp)
            self.prepare(root)
            self.assertEqual(2, render_main(["--work-dir", str(root)]))
            self.assertTrue((root / "diagram-report.json").exists())
            self.assertFalse((root / "assets/context.svg").exists())

    def test_multitab_drawio_is_split_without_losing_compressed_content(self):
        pages = drawio_pages(DRAWIO)
        self.assertEqual(2, len(pages))
        self.assertIn(b"compressed-source-unchanged", pages[1])
        self.assertNotIn(b"Context", pages[1])

    def test_renderer_generates_all_svgs_without_forwarding_pat(self):
        def renderer(cmd, cwd, env, **kwargs):
            self.assertNotIn("CONFLUENCE_TOKEN", env)
            self.assertIn("--unshare-net", cmd)
            (cwd / "output.svg").write_bytes(SVG)
            return subprocess.CompletedProcess(cmd, 0)

        with tempfile.TemporaryDirectory() as tmp, patch("render_drawio.shutil.which", return_value="/bin/bwrap"), patch("render_drawio.subprocess.run", side_effect=renderer), patch.dict(os.environ, {"CONFLUENCE_TOKEN": "private"}):
            root = Path(tmp)
            self.prepare(root)
            result = render(root, ["xvfb-run", "-a", "drawio"])
            self.assertEqual(2, len(result))
            self.assertTrue(all(r["status"].startswith("rendered") for r in result))
            self.assertEqual(SVG, (root / "assets/context.svg").read_bytes())
            self.assertEqual(SVG, (root / "assets/context-page-2.svg").read_bytes())
            with patch("render_drawio.subprocess.run", side_effect=AssertionError("Verified SVGs should be reused")):
                repeated = render(root, ["drawio"])
            self.assertTrue(all(r["status"].startswith("verified existing") for r in repeated))
            (root / "assets/context.drawio").write_bytes(DRAWIO.replace(b"Context", b"Changed"))
            stale = render(root, ["drawio"])
            self.assertTrue(all(r["status"] == "failed" for r in stale))
            self.assertEqual(SVG, (root / "assets/context.svg").read_bytes())

    def test_missing_renderer_is_reported_not_replaced_by_fake_svg(self):
        with tempfile.TemporaryDirectory() as tmp, patch("render_drawio.shutil.which", return_value=None):
            root = Path(tmp)
            self.prepare(root)
            result = render(root, ["drawio"])
            self.assertTrue(all(r["status"] == "failed" for r in result))
            self.assertFalse((root / "assets/context.svg").exists())
            self.assertTrue((root / "diagram-report.json").exists())

    def test_container_never_pulls_or_uses_network(self):
        with patch("render_drawio.shutil.which", return_value="/bin/docker"), patch("render_drawio.container_user", return_value="0:0"), patch.dict(os.environ, {"DOCKER_HOST": "unix:///run/user/1000/docker.sock"}, clear=True):
            cmd = render_command(["drawio"], "local-renderer:1", Path("/tmp/work"))
            self.assertEqual("none", cmd[cmd.index("--network") + 1])
            self.assertEqual("never", cmd[cmd.index("--pull") + 1])

    def test_container_preserves_local_endpoint_without_forwarding_credentials(self):
        cases = [
            ({"DOCKER_HOST": "unix:///run/user/1000/docker.sock"}, "unix:///run/user/1000/docker.sock"),
            ({"DOCKER_CONTEXT": "desktop-linux", "DOCKER_HOST": "tcp://ignored:2375"}, "unix:///home/example/.docker/desktop/docker.sock"),
            ({}, "unix:///home/example/.docker/run/docker.sock"),
            ({"DOCKER_HOST": "npipe:////./pipe/docker_engine"}, "npipe:////./pipe/docker_engine"),
        ]
        for selection, endpoint in cases:
            with self.subTest(selection=selection), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.prepare(root)
                docker_config = root / "private-docker-config"
                docker_config.mkdir()
                config = '{"auths":{"registry":{"auth":"private"}},"proxies":{"default":{"httpsProxy":"https://user:secret@proxy"}}}'
                (docker_config / "config.json").write_text(config)
                inspected = []

                def docker(cmd, env, **kwargs):
                    self.assertNotIn("CONFLUENCE_TOKEN", env)
                    self.assertNotIn("HTTPS_PROXY", env)
                    if cmd[1:3] == ["context", "inspect"]:
                        inspected.append(cmd)
                        self.assertEqual(str(docker_config), env["DOCKER_CONFIG"])
                        if selection.get("DOCKER_CONTEXT"):
                            self.assertEqual(["--", selection["DOCKER_CONTEXT"]], cmd[-2:])
                        return subprocess.CompletedProcess(cmd, 0, json.dumps(endpoint).encode())
                    self.assertEqual(endpoint, cmd[cmd.index("--host") + 1])
                    self.assertNotIn("DOCKER_CONFIG", env)
                    self.assertNotIn("HOME", env)
                    self.assertNotIn(str(docker_config), " ".join(cmd))
                    self.assertFalse(Path(cmd[cmd.index("--config") + 1]).exists())
                    if "info" in cmd:
                        return subprocess.CompletedProcess(cmd, 0, b'["name=rootless"]')
                    if "rm" in cmd:
                        return subprocess.CompletedProcess(cmd, 0)
                    self.assertEqual(["HOME=/tmp", "DRAWIO_DISABLE_UPDATE=true"],
                                     [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--env"])
                    self.assertFalse(Path(cmd[cmd.index("--config") + 1]).is_relative_to(kwargs["cwd"]))
                    (kwargs["cwd"] / "output.svg").write_bytes(SVG)
                    return subprocess.CompletedProcess(cmd, 0)

                env = {"HOME": str(root), "DOCKER_CONFIG": str(docker_config), "CONFLUENCE_TOKEN": "private",
                       "HTTPS_PROXY": "https://user:secret@proxy", **selection}
                with patch.dict(os.environ, env, clear=True), patch("render_drawio.shutil.which", return_value="/bin/docker"), patch("render_drawio.subprocess.run", side_effect=docker):
                    results = render(root, ["drawio"], "local-renderer:1")
                self.assertTrue(all(r["status"].startswith("rendered") for r in results))
                self.assertEqual(0 if "DOCKER_HOST" in selection and "DOCKER_CONTEXT" not in selection else 2, len(inspected))
                self.assertEqual(config, (docker_config / "config.json").read_text())

    def test_container_user_follows_engine_mapping(self):
        for options, expected in ((["name=rootless", "name=seccomp,profile=builtin"], "0:0"),
                                  (["name=seccomp,profile=builtin"], "1001:1002")):
            with self.subTest(options=options), patch("render_drawio.shutil.which", return_value="/bin/docker"), patch("render_drawio.local_docker_endpoint", return_value="unix:///tmp/local.sock"), patch("render_drawio.os.getuid", return_value=1001), patch("render_drawio.os.getgid", return_value=1002), patch("render_drawio.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(options).encode())):
                cmd = render_command(["drawio"], "local-renderer:1", Path("/tmp/work"))
                self.assertEqual(expected, cmd[cmd.index("--user") + 1])
                self.assertIn("--no-sandbox", cmd)
                self.assertNotIn("--userns", cmd)
                self.assertIn("no-new-privileges", cmd)
                self.assertNotIn("--privileged", cmd)
                self.assertNotIn("seccomp=unconfined", cmd)
                self.assertEqual("ALL", cmd[cmd.index("--cap-drop") + 1])
                self.assertEqual("none", cmd[cmd.index("--network") + 1])

    def test_unknown_or_unsupported_engine_mapping_does_not_start_container(self):
        for output in (b'["name=userns"]', b'null', b'{}', b'not-json'):
            with self.subTest(output=output), tempfile.TemporaryDirectory() as tmp, patch("render_drawio.shutil.which", return_value="/bin/docker"), patch("render_drawio.local_docker_endpoint", return_value="unix:///tmp/local.sock"):
                def docker(cmd, **kwargs):
                    self.assertIn("info", cmd)
                    return subprocess.CompletedProcess(cmd, 0, output)
                root = Path(tmp)
                self.prepare(root)
                with patch("render_drawio.subprocess.run", side_effect=docker):
                    results = render(root, ["drawio"], "local-renderer:1")
                self.assertTrue(all(r["status"] == "failed" for r in results))

    def test_container_cleanup_on_timeout_error_and_success(self):
        for outcome in ("timeout", "failed", "success", "not-created"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp, patch("render_drawio.shutil.which", return_value="/bin/docker"), patch("render_drawio.local_docker_endpoint", return_value="unix:///tmp/local.sock"):
                active = set()
                started = []
                removed = []
                def docker(cmd, env, **kwargs):
                    self.assertEqual("unix:///tmp/local.sock", cmd[2])
                    if "info" in cmd:
                        return subprocess.CompletedProcess(cmd, 0, b'["name=rootless"]')
                    if "run" in cmd:
                        self.assertFalse(active, "Previous container must be removed before the next tab")
                        self.assertIn("--init", cmd)
                        name = cmd[cmd.index("--name") + 1]
                        started.append(name)
                        if outcome == "not-created":
                            return subprocess.CompletedProcess(cmd, 125)
                        active.add(name)
                        self.assertEqual(0o700, kwargs["cwd"].stat().st_mode & 0o777)
                        if outcome == "timeout":
                            raise subprocess.TimeoutExpired(cmd, 120)
                        if outcome == "success":
                            (kwargs["cwd"] / "output.svg").write_bytes(SVG)
                        return subprocess.CompletedProcess(cmd, 1 if outcome == "failed" else 0)
                    if any(action in cmd for action in ("inspect", "top", "logs")):
                        return subprocess.CompletedProcess(cmd, 1, b"", b"")
                    if "rm" in cmd:
                        self.assertIn("--force", cmd)
                        removed.append(cmd[-1])
                        if outcome == "not-created":
                            return subprocess.CompletedProcess(cmd, 1)
                        active.remove(cmd[-1])
                        return subprocess.CompletedProcess(cmd, 0)
                    self.assertIn("ps", cmd)
                    self.assertEqual(f"name=^/{started[-1]}$", cmd[cmd.index("--filter") + 1])
                    return subprocess.CompletedProcess(cmd, 0, b"")
                root = Path(tmp)
                self.prepare(root)
                with patch("render_drawio.subprocess.run", side_effect=docker):
                    results = render(root, ["drawio"], "local-renderer:1")
                self.assertEqual(2, len(set(started)))
                self.assertEqual(started, removed)
                self.assertFalse(active)
                self.assertTrue(all(r["status"].startswith("rendered") if outcome == "success" else r["status"] == "failed" for r in results))

    def test_failed_container_cleanup_aborts_and_records_container_identity(self):
        for failure in ("still-present", "timeout", "daemon-unavailable"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp, patch("render_drawio.shutil.which", return_value="/bin/docker"), patch("render_drawio.local_docker_endpoint", return_value="unix:///tmp/local.sock"):
                started = []
                def docker(cmd, **kwargs):
                    if "info" in cmd:
                        return subprocess.CompletedProcess(cmd, 0, b'["name=rootless"]')
                    if "run" in cmd:
                        started.append(cmd[cmd.index("--name") + 1])
                        raise subprocess.TimeoutExpired(cmd, 120)
                    if "rm" in cmd:
                        if failure == "timeout":
                            raise subprocess.TimeoutExpired(cmd, 15)
                        return subprocess.CompletedProcess(cmd, 1)
                    self.assertIn("ps", cmd)
                    return subprocess.CompletedProcess(cmd, 1 if failure == "daemon-unavailable" else 0, b"remaining-id")
                root = Path(tmp)
                self.prepare(root)
                with patch("render_drawio.subprocess.run", side_effect=docker), self.assertRaises(ContainerCleanupError):
                    render(root, ["drawio"], "local-renderer:1")
                self.assertEqual(1, len(started))
                report = json.loads((root / "diagram-report.json").read_text())
                self.assertEqual("failed", report[0]["status"])
                self.assertIn(started[0], report[0]["reason"])

    def test_aborted_render_preserves_unvisited_svg_verification_for_retry(self):
        for changed_svg in (False, True):
            with self.subTest(changed_svg=changed_svg), tempfile.TemporaryDirectory() as tmp, patch("render_drawio.shutil.which", return_value="/bin/docker"), patch("render_drawio.local_docker_endpoint", return_value="unix:///tmp/local.sock"):
                cleanup_fails = False
                rendered = []

                def docker(cmd, **kwargs):
                    if "info" in cmd:
                        return subprocess.CompletedProcess(cmd, 0, b'["name=rootless"]')
                    if "run" in cmd:
                        rendered.append((kwargs["cwd"] / "input.drawio").read_bytes())
                        if cleanup_fails:
                            raise subprocess.TimeoutExpired(cmd, 120)
                        (kwargs["cwd"] / "output.svg").write_bytes(SVG)
                        return subprocess.CompletedProcess(cmd, 0)
                    if "rm" in cmd:
                        return subprocess.CompletedProcess(cmd, 1 if cleanup_fails else 0)
                    self.assertIn("ps", cmd)
                    return subprocess.CompletedProcess(cmd, 0, b"remaining-id")

                root = Path(tmp)
                self.prepare(root)
                with patch("render_drawio.subprocess.run", side_effect=docker):
                    previous = render(root, ["drawio"], "local-renderer:1")
                    (root / previous[0]["svg"]).unlink()
                    untouched = root / previous[1]["svg"]
                    cleanup_fails = True
                    with self.assertRaises(ContainerCleanupError):
                        render(root, ["drawio"], "local-renderer:1")
                    aborted = json.loads((root / "diagram-report.json").read_text())
                    self.assertEqual(2, len(aborted))
                    self.assertEqual("failed", aborted[0]["status"])
                    self.assertNotIn("svg_sha256", aborted[0])
                    self.assertEqual(previous[1], aborted[1])
                    self.assertEqual(SVG, untouched.read_bytes())
                    self.assertEqual(3, len(rendered))

                    cleanup_fails = False
                    if changed_svg:
                        untouched.write_bytes(SVG.replace(b"</svg>", b"<text>Changed</text></svg>"))
                    retried = render(root, ["drawio"], "local-renderer:1")
                    self.assertEqual(4, len(rendered), "Only the missing SVG should be rendered again")
                    self.assertTrue(retried[0]["status"].startswith("rendered"))
                    if changed_svg:
                        self.assertEqual("failed", retried[1]["status"])
                    else:
                        self.assertTrue(retried[1]["status"].startswith("verified existing"))
                        self.assertEqual(previous[1]["svg_sha256"], retried[1]["svg_sha256"])
                    self.assertEqual(retried, json.loads((root / "diagram-report.json").read_text()))

    def test_docker_endpoint_failures_are_reported_without_default_fallback(self):
        for endpoint in ("tcp://remote:2375", "ssh://user@remote", "npipe:////remote/pipe/docker_engine", "", None):
            with self.subTest(endpoint=endpoint), patch.dict(os.environ, {}, clear=True), patch("render_drawio.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(endpoint).encode())):
                with self.assertRaises(MigrationError):
                    local_docker_endpoint()
        for result in (subprocess.CompletedProcess([], 1, b"", b"private-detail"),
                       subprocess.CompletedProcess([], 0, b"invalid-json-private-detail")):
            with self.subTest(result=result), patch.dict(os.environ, {}, clear=True), patch("render_drawio.subprocess.run", return_value=result):
                with self.assertRaises(MigrationError) as caught:
                    local_docker_endpoint()
                self.assertNotIn("private-detail", str(caught.exception))
        for error in (subprocess.TimeoutExpired("docker", 15, stderr=b"private-detail"), OSError("private-detail")):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), patch("render_drawio.shutil.which", return_value="/bin/docker"), patch("render_drawio.subprocess.run", side_effect=error):
                root = Path(tmp)
                self.prepare(root)
                results = render(root, ["drawio"], "local-renderer:1")
                self.assertTrue(all(r["status"] == "failed" for r in results))
                self.assertNotIn("private-detail", (root / "diagram-report.json").read_text())

    def test_bad_svg_and_external_image_are_rejected(self):
        for data in [b"not SVG", b"<html/>", b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://elsewhere/image.png"/></svg>']:
            with self.subTest(data=data), self.assertRaises(MigrationError):
                validate_svg(data)
        validate_svg(SVG)

    def test_missing_inventory_sources_or_tabs_are_failures(self):
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(1, render_main(["--work-dir", tmp]))
        for failure in ("missing-source", "missing-tab", "unlisted-source", "symlink-parent"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.prepare(root)
                if failure == "missing-source":
                    (root / "assets/context.drawio").unlink()
                elif failure == "missing-tab":
                    (root / "migration.json").write_text(json.dumps({"drawio": [{"source": "assets/context.drawio", "svgs": ["assets/context.svg"]}]}))
                elif failure == "unlisted-source":
                    (root / "migration.json").write_text('{"drawio": []}')
                else:
                    (root / "assets").rename(root / "actual-assets")
                    (root / "assets").symlink_to(root / "actual-assets", target_is_directory=True)
                with patch("render_drawio.subprocess.run", side_effect=AssertionError("Invalid inventory must not render")):
                    result = render(root, ["drawio"])
                self.assertTrue(result)
                self.assertTrue(all(r["status"] == "failed" for r in result))

    def test_valid_manifest_without_diagrams_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_file(root, "migration.json", '{"drawio": []}')
            self.assertEqual([], render(root, ["drawio"]))

    def test_existing_svg_without_source_proof_is_rejected_and_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare(root)
            for destination in svg_paths("assets/context.drawio", 2):
                write_file(root, destination, SVG)
            with patch("render_drawio.subprocess.run", side_effect=AssertionError("Must preserve unverified SVGs")):
                result = render(root, ["drawio"])
            self.assertTrue(all(r["status"] == "failed" for r in result))
            self.assertEqual(SVG, (root / "assets/context.svg").read_bytes())

    def test_modified_svg_fails_hash_check_and_missing_tab_is_regenerated(self):
        def renderer(cmd, cwd, **kwargs):
            (cwd / "output.svg").write_bytes(SVG)
            return subprocess.CompletedProcess(cmd, 0)

        with tempfile.TemporaryDirectory() as tmp, patch("render_drawio.shutil.which", return_value="/bin/bwrap"), patch("render_drawio.subprocess.run", side_effect=renderer) as run:
            root = Path(tmp)
            self.prepare(root)
            render(root, ["drawio"])
            changed = SVG.replace(b"Architecture", b"Edited")
            (root / "assets/context.svg").write_bytes(changed)
            (root / "assets/context-page-2.svg").unlink()
            result = render(root, ["drawio"])
            self.assertEqual("failed", result[0]["status"])
            self.assertTrue(result[1]["status"].startswith("rendered"))
            self.assertEqual(3, run.call_count)
            self.assertEqual(changed, (root / "assets/context.svg").read_bytes())

    def test_drawio_standard_doctype_removed_but_arbitrary_dtds_rejected(self):
        standard = b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
        self.assertEqual(SVG, normalize_renderer_svg(standard + SVG))
        validate_svg(normalize_renderer_svg(standard + SVG))
        for declaration in (b'<!DOCTYPE svg SYSTEM "https://external/evil.dtd">', b'<!DOCTYPE svg [<!ENTITY e "replacement">]>'):
            with self.assertRaises(MigrationError):
                validate_svg(normalize_renderer_svg(declaration + SVG))

    def test_svg_nested_resources_css_and_active_content_are_rejected(self):
        fragments = [
            '<foreignObject><img xmlns="http://www.w3.org/1999/xhtml" src="https://external/image.png"/></foreignObject>',
            '<style>@import "https://external/font.css";</style>',
            '<style>text {font-family: sans-serif} @font-face {src: url(https://external/font.woff)}</style>',
            '<rect style="fill: url(https://external/pattern.svg#x)"/>',
            r'<rect fill="u\72l(https://external/pattern.svg#x)"/>',
            '<rect style="background: image-set(\'https://external/img.png\' 1x)"/>',
            '<image href="data:image/svg+xml;base64,PHN2Zz4="/>',
            '<image href="local.png"/>', '<script>run()</script>', '<g onload="run()"/>',
            '<set attributeName="href" to="https://external/image.png"/>',
            '<a href="javascript:run()"/>', '<g xml:base="https://external/"><use href="#x"/></g>',
            '<foreignObject><img srcset="https://external/img.png 2x"/></foreignObject>',
        ]
        for fragment in fragments:
            with self.subTest(fragment=fragment), self.assertRaises(MigrationError):
                validate_svg(('<svg xmlns="http://www.w3.org/2000/svg">' + fragment + '</svg>').encode())
        for prefix in ('<?xml-stylesheet href="https://external/style.css"?>', '<!DOCTYPE svg>'):
            with self.assertRaises(MigrationError):
                validate_svg(prefix.encode() + SVG)
        validate_svg(b'<svg xmlns="http://www.w3.org/2000/svg"><a href="https://example.org"><text>Link</text></a><use href="#local"/><image href="data:image/png;base64,YQ=="/><rect style="fill: url(#local)"/></svg>')


if __name__ == "__main__":
    unittest.main()
