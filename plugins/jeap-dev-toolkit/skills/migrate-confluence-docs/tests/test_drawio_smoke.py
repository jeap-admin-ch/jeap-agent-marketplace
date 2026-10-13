"""Opt-in real rendering using CONFLUENCE_DRAWIO_TEST_IMAGE, never pulls an image."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from confluence import svg_paths, write_file
from render_drawio import render, validate_svg


@unittest.skipUnless(os.environ.get("CONFLUENCE_DRAWIO_TEST_IMAGE"),
                     "Set CONFLUENCE_DRAWIO_TEST_IMAGE to an existing local Draw.io image")
class DrawioSmokeTests(unittest.TestCase):
    def test_real_container_exports_each_tab_and_reuses_verified_svgs(self):
        labels = ("First diagram", "Second diagram")
        tabs = [f'''<diagram name="{label}"><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="2" value="{label}" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
<mxGeometry x="20" y="20" width="160" height="60" as="geometry"/>
</mxCell></root></mxGraphModel></diagram>''' for label in labels]
        source = ("<mxfile>" + "".join(tabs) + "</mxfile>").encode()
        with tempfile.TemporaryDirectory(prefix="drawio-smoke-") as tmp:
            root = Path(tmp)
            relative = "assets/example.drawio"
            destinations = svg_paths(relative, len(labels))
            write_file(root, relative, source)
            write_file(root, "migration.json", json.dumps({"drawio": [
                {"page": "smoke", "source": relative, "svgs": destinations}]}))
            results = render(root, ["drawio"], os.environ["CONFLUENCE_DRAWIO_TEST_IMAGE"])
            self.assertEqual(len(labels), len(results))
            self.assertTrue(all(r["status"].startswith("rendered") for r in results), results)
            for label, destination in zip(labels, destinations):
                data = (root / destination).read_bytes()
                validate_svg(data)
                self.assertIn(label, "".join(ET.fromstring(data).itertext()))
            self.assertEqual(source, (root / relative).read_bytes())
            repeated = render(root, ["drawio"], os.environ["CONFLUENCE_DRAWIO_TEST_IMAGE"])
            self.assertTrue(all(r["status"].startswith("verified existing") for r in repeated), repeated)


if __name__ == "__main__":
    unittest.main()
