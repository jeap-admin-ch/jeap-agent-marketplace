#!/usr/bin/env python3
"""Prepare working drafts from selected Confluence Data Center pages. Python 3.10+.

Descendant discovery is opt-in and bounded. No macro expansion, remote rendering
or link checking is performed.
Keep this working export outside the published documentation root. The agent must
reconcile drafts into the target's structure; copying the export is not completion.
"""

import argparse
import base64
import html
import http.client
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse as url
import urllib.request
import uuid
import zlib
import xml.etree.ElementTree as ET

AC = "http://atlassian.com/content"
RI = "http://atlassian.com/resource/identifier"
NS = {"ac": AC, "ri": RI}


class MigrationError(Exception):
    pass


def page_id(value):
    if re.fullmatch(r"[0-9]+", value):
        return value
    parsed = url.urlsplit(value)
    ids = url.parse_qs(parsed.query).get("pageId", [])
    match = re.search(r"/pages/([0-9]+)(?:/|$)", parsed.path)
    candidate = ids[0] if ids else match[1] if match else ""
    if not re.fullmatch(r"[0-9]+", candidate):
        raise MigrationError("Use a page ID or a Confluence URL containing /pages/ID or pageId=ID.")
    return candidate


def origin(value):
    parsed = url.urlsplit(value)
    port = parsed.port
    return parsed.scheme, parsed.hostname, port if port is not None else (443 if parsed.scheme == "https" else 80)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Even same-origin redirects might leave the page allow-list.
        raise MigrationError("Redirect refused. Use the canonical HTTPS Confluence base URL; SSO is not PAT authentication.")


class Confluence:
    def __init__(self, base, selected):
        self.base = base.rstrip("/")
        try:
            parsed = url.urlsplit(self.base)
            origin(self.base)  # Validate ports as well as bracketed hosts before any request.
        except ValueError:
            raise MigrationError("Malformed Confluence base URL.") from None
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or any(p in (".", "..") for p in parsed.path.split("/"))):
            raise MigrationError("--base-url must be the canonical HTTPS instance URL, optionally with its context path.")
        self.context = parsed.path.rstrip("/")
        self.allowed = set()
        for value in selected:
            if not value.isdigit():
                self.same_origin(value)
                if not url.urlsplit(value).path.startswith(self.context + "/"):
                    raise MigrationError("Page URL is outside the configured Confluence context path.")
            self.allowed.add(page_id(value))
        self.auth = None
        self.roots = sorted(self.allowed, key=int)
        self.scope = {"roots": self.roots, "include_descendants": False,
                      "complete": True, "hierarchy": [], "errors": []}
        self.opener = urllib.request.build_opener(NoRedirect())

    def same_origin(self, value):
        try:
            resolved = url.urljoin(self.base + "/", value)
            parsed = url.urlsplit(resolved)
            matches = origin(resolved) == origin(self.base)
        except ValueError:
            raise MigrationError("Malformed Confluence URL.") from None
        if not matches or parsed.username or parsed.password:
            raise MigrationError("Refused URL outside the configured Confluence origin.")
        return resolved

    def check_page(self, pid):
        if pid not in self.allowed:
            raise MigrationError("Refused page outside the explicit page allow-list.")

    def credentials(self):
        # Read a token inside this process only. Never include it in argv or logs.
        token_file = os.environ.get("CONFLUENCE_PAT_FILE")
        try:
            token = Path(token_file).read_text().strip() if token_file else os.environ.get("CONFLUENCE_TOKEN", "").strip()
        except OSError:
            raise MigrationError("Cannot read CONFLUENCE_PAT_FILE.") from None
        if not token or "\n" in token or "\r" in token:
            raise MigrationError("Authentication required. Configure CONFLUENCE_PAT_FILE (raw PAT, outside the repository) or CONFLUENCE_TOKEN locally, then retry. Never paste the PAT into chat.")
        return "Bearer " + token

    def request(self, target):
        # Only internal methods construct targets; no arbitrary-URL command exists.
        target = self.same_origin(target)
        for attempt in range(2):
            headers = {"Accept": "application/json"}
            if self.auth:
                headers["Authorization"] = self.auth
            try:
                with self.opener.open(urllib.request.Request(target, headers=headers), timeout=60) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                configured_pat = bool(os.environ.get("CONFLUENCE_PAT_FILE") or os.environ.get("CONFLUENCE_TOKEN"))
                if (exc.code in (401, 403) or exc.code == 404 and configured_pat) and not self.auth and attempt == 0:
                    # Confluence may hide restricted content behind an anonymous 404.
                    # Retry this same scoped URL once, never search for another page.
                    self.auth = self.credentials()
                    continue
                # Do not emit response bodies, request headers or credentials.
                raise MigrationError(f"Confluence returned HTTP {exc.code}. Check access and PAT configuration; no other pages were searched.") from None
            except (OSError, http.client.HTTPException):
                # Includes URLError, socket timeouts/resets and truncated response bodies.
                raise MigrationError("Confluence connection or response read failed. Check the base URL, VPN and certificate trust; retry the failed resource.") from None
        raise MigrationError("Authentication failed.")

    def api(self, path):
        try:
            return json.loads(self.request(self.base + "/rest/api/" + path))
        except (ValueError, UnicodeError):
            raise MigrationError("Expected Confluence REST JSON; check the base URL and PAT (an SSO login page is not supported).") from None

    def page(self, pid):
        self.check_page(pid)
        # storage avoids server-side expansion of include, excerpt-include, Jira, etc.
        data = self.api(f"content/{pid}?expand=body.storage,version,space")
        if str(data.get("id")) != pid or data.get("type") != "page":
            raise MigrationError("Unexpected page identity/type in Confluence response.")
        if "storage" not in data.get("body", {}):
            raise MigrationError("Page has no storage body.")
        return data

    def attachments(self, pid):
        self.check_page(pid)
        result = []
        start = 0
        seen = set()
        while True:
            batch = self.api(f"content/{pid}/child/attachment?limit=100&start={start}")
            rows = batch.get("results", [])
            for row in rows:
                if row.get("id") in seen:
                    raise MigrationError("Attachment pagination repeated an ID; refusing an endless export.")
                seen.add(row.get("id"))
            result.extend(rows)
            if not batch.get("_links", {}).get("next"):
                return result
            if not rows:
                raise MigrationError("Invalid empty attachment pagination response.")
            # Never follow server-supplied next URLs; retain the scoped endpoint.
            start += len(rows)

    def children(self, pid):
        """Yield only direct children; never follow a supplied pagination URL."""
        self.check_page(pid)
        start, seen = 0, set()
        while True:
            batch = self.api(f"content/{pid}/child/page?limit=100&start={start}")
            rows = batch.get("results", [])
            for row in rows:
                child = str(row.get("id", ""))
                if not child.isdigit() or row.get("type") != "page" or child in seen:
                    raise MigrationError("Invalid or repeated child page identity in pagination.")
                seen.add(child)
                yield child
            if not batch.get("_links", {}).get("next"):
                return
            if not rows:
                raise MigrationError("Invalid empty child-page pagination response.")
            start += len(rows)

    def discover_descendants(self, max_pages=100, max_depth=20):
        if max_pages < len(self.allowed) or max_depth < 0:
            raise MigrationError("Tree limits must cover the selected roots and a nonnegative depth.")
        self.scope.update(include_descendants=True, max_pages=max_pages, max_depth=max_depth)
        queue = [(pid, 0) for pid in self.roots]
        parents = {}
        for pid, depth in queue:
            try:
                for child in self.children(pid):
                    ancestor = pid
                    while ancestor != child and ancestor in parents:
                        ancestor = parents[ancestor]
                    if ancestor == child or child in parents and parents[child] != pid:
                        raise MigrationError("Inconsistent child hierarchy (cycle or multiple parents).")
                    if child not in self.allowed:
                        if depth >= max_depth or len(self.allowed) >= max_pages:
                            raise MigrationError("Tree limit reached; export scope is incomplete. Review scope before raising limits.")
                        self.allowed.add(child)
                        queue.append((child, depth + 1))
                    parents[child] = pid
                    self.scope["hierarchy"].append({"parent": pid, "child": child})
            except MigrationError as exc:
                self.scope["complete"] = False
                self.scope["errors"].append({"page": pid, "message": str(exc)})
        return self.scope

    def attachment(self, pid, row):
        self.check_page(pid)
        download = row.get("_links", {}).get("download", "")
        if not isinstance(download, str) or not download:
            raise MigrationError("Attachment response has no download link.")
        try:
            link = url.urlsplit(download)
        except ValueError:
            raise MigrationError("Malformed attachment download URL.") from None
        # REST download links beginning /download are relative to the application,
        # unlike absolute URLs and links that already include the context path.
        if not link.scheme and not link.netloc and link.path.startswith("/download/attachments/"):
            download = self.base + download
        target = self.same_origin(download)
        parsed = url.urlsplit(target)
        prefix = self.context + f"/download/attachments/{pid}/"
        tail = parsed.path[len(prefix):] if parsed.path.startswith(prefix) else ""
        decoded = url.unquote(tail)
        if not tail or "/" in decoded or "\\" in decoded or decoded in (".", "..") or re.search(r"%[0-9a-f]{2}", decoded, re.I):
            raise MigrationError("Attachment download is outside the selected page's attachment endpoint.")
        return self.request(target)


def slug(value):
    return re.sub(r"[^\w.-]+", "-", value, flags=re.UNICODE).strip(".-")[:100] or "untitled"


def write_file(root, relative, data):
    """Never follow output symlinks or overwrite differing pre-existing content."""
    root = root.resolve()
    target = root / relative
    if not target.resolve().is_relative_to(root):
        raise MigrationError("Output path escapes the destination folder.")
    for part in (target, *target.parents):
        if part == root:
            break
        if part.is_symlink():
            raise MigrationError("Refused symlink in output path.")
    data = data.encode("utf-8") if isinstance(data, str) else data
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() == data:
            return
        raise MigrationError(f"Existing file differs: {relative}. Export into a new staging folder and merge deliberately.")
    with target.open("xb") as output:
        output.write(data)


def parse_storage(storage):
    # CDATA and comments may contain literal declaration examples. Preserve both
    # while checking actual markup before XML parsing or HTML entity conversion.
    parts = re.split(r"(<!\[CDATA\[.*?\]\]>|<!--.*?-->)", storage, flags=re.S)
    if any(re.search(r"<!\s*(DOCTYPE|ENTITY)", part, re.I) for part in parts[::2]):
        raise MigrationError("DTD/entity declarations in storage are not supported.")
    # Confluence sometimes uses HTML named entities outside literal blocks.
    for index in range(0, len(parts), 2):
        parts[index] = re.sub(
            r"&([A-Za-z][A-Za-z0-9]+);",
            lambda m: m[0] if m[1] in {"amp", "lt", "gt", "quot", "apos"}
            else "".join(f"&#{ord(c)};" for c in html.unescape(m[0])), parts[index])
    try:
        return ET.fromstring(f'<root xmlns:ac="{AC}" xmlns:ri="{RI}">' + "".join(parts) + "</root>")
    except ET.ParseError:
        raise MigrationError("Storage XML could not be parsed; the original storage file is retained for manual conversion.") from None


def local_name(element):
    return element.tag.rsplit("}", 1)[-1]


def plain(element):
    return "".join(element.itertext()) if element is not None else ""


def escaped(text):
    text = text.replace("&", "&amp;")
    return re.sub(r"([\\`*_\[\]<>|#])", r"\\\1", re.sub(r"\s+", " ", text))


def fenced(text, language=""):
    fence = "`" * max(3, max((len(m[0]) + 1 for m in re.finditer(r"`+", text)), default=3))
    language = re.sub(r"[^\w+-]", "", language)
    return f"\n\n{fence}{language}\n{text.rstrip(chr(10))}\n{fence}\n\n"


def table_cell(text):
    # Pipe tables cannot portably contain block content or hard line breaks.
    text = re.sub(r"\s*\n\s*", " / ", text.strip())
    # GFM table escaping also works inside inline code; HTML entities do not.
    return re.sub(r"(?<!\\)((?:\\\\)*)\|", lambda m: m[1] + r"\|", text)


def glossary_cell(text):
    """Encode plain storage text as literal inline Markdown in a pipe table."""
    text = html.escape(re.sub(r"\s*\n\s*", " / ", text.strip()), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", r"\\\1", text)


def inline_literal(text):
    # Decode escapes/entities once: an escaped ampersand is not an entity opener.
    return re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~])|(&\#?[A-Za-z0-9]+;)",
                  lambda m: m[1] if m[1] is not None else html.unescape(m[2]), text)


def diagram_links(data, kind):
    """Inspect saved diagram content only; never resolve a URL or an include."""
    targets = set()

    def add(value):
        value = value.strip()
        if value and not value.startswith("data:"):
            targets.add(value)

    def argument(value):
        value = value.strip()
        # Strip quotes only around a literal, never from a dynamic expression.
        if re.fullmatch(r'''(?:"[^"\r\n]*"|'[^'\r\n]*')''', value):
            return value[1:-1]
        return value

    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in {"href", "src"} and value:
                    add(value)

    def xml(data):
        if re.search(br"<!\s*(DOCTYPE|ENTITY)", data, re.I):
            raise MigrationError("Diagram link inspection refused a DTD/entity declaration.")
        return ET.fromstring(data)

    try:
        if kind == "plantuml":
            source = data.decode("utf-8-sig")
            for target in re.findall(r'''(?:https?://|mailto:)[^\s<>"'\[\]{}]+''', source):
                add(target.rstrip(").,;"))
            for target in re.findall(r"(?im)^[ \t]*!include(?:url|_once|_many|sub)?[ \t]+(.+)$", source):
                add(argument(target))
            for target in re.findall(r"\[\[([^\s\]{}]+)", source):
                add(target)
            for match in re.finditer(r"%load\w*\(", source):
                start = match.end()
                depth, quote, escaped_char = 1, None, False
                end = start
                while end < len(source):
                    char = source[end]
                    if quote:
                        if escaped_char:
                            escaped_char = False
                        elif char == "\\":
                            escaped_char = True
                        elif char == quote:
                            quote = None
                    elif char in "\"'":
                        quote = char
                    elif char == "(":
                        depth += 1
                    elif char == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    end += 1
                # Keep an unresolved/unterminated expression verbatim for review.
                add(argument(source[start:end]))
        else:
            tree = xml(data)
            trees = [tree]
            if kind == "drawio":
                for diagram in tree.findall("diagram"):
                    if not list(diagram) and (diagram.text or "").strip():
                        compressed = base64.b64decode(diagram.text.strip(), validate=True)
                        decoder = zlib.decompressobj(-15)
                        decoded = decoder.decompress(compressed, 16 * 1024 * 1024)
                        if decoder.unconsumed_tail or not decoder.eof:
                            raise MigrationError("Compressed Draw.io tab exceeds inspection limit or is incomplete.")
                        trees.append(xml(url.unquote(decoded.decode("utf-8")).encode("utf-8")))
            for tree in trees:
                for element in tree.iter():
                    tag = local_name(element)
                    for key, value in element.attrib.items():
                        attr = key.rsplit("}", 1)[-1]
                        if attr in {"link", "href", "src"} and (tag == "a" or not value.startswith("#")):
                            add(value)
                        if attr in {"value", "label"}:
                            Links().feed(value)
                        if attr == "style":
                            for target in re.findall(r"(?:^|;)image=([^;]+)", value):
                                add(target)
    except (ValueError, UnicodeError, ET.ParseError, zlib.error):
        raise MigrationError("Diagram links could not be fully inspected; review the retained source manually.") from None
    return sorted(targets)


def term_key(text):
    """Match ordinary glossary labels even when existing entries are links/bold."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "*_`":
        text = text[1:-1]
    return " ".join(inline_literal(text).split()).casefold()


def drawio_pages(data):
    if re.search(br"<!\s*(DOCTYPE|ENTITY)", data, re.I):
        raise MigrationError("DTD/entity declarations in Draw.io source are not supported.")
    try:
        tree = ET.fromstring(data)
    except ET.ParseError:
        raise MigrationError("Draw.io source is not XML; retain the source and review the attachment format.") from None
    if tree.tag == "mxGraphModel":
        return [data]
    diagrams = tree.findall("diagram")
    if tree.tag != "mxfile" or not diagrams:
        raise MigrationError("Draw.io source has no diagram pages.")
    # Separate each tab for SVG export; no layout conversion or decompression.
    result = []
    for diagram in diagrams:
        wrapper = ET.Element("mxfile", tree.attrib)
        wrapper.append(diagram)
        result.append(ET.tostring(wrapper, encoding="utf-8"))
    return result


def svg_paths(source, count):
    stem = str(Path(source).with_suffix(""))
    return [stem + ("" if i == 0 else f"-page-{i + 1}") + ".svg" for i in range(count)]


class Export:
    def __init__(self, client, destination, glossary="12-glossary.md", glossary_pages=()):
        self.client = client
        self.root = Path(destination).resolve()
        self.glossary = glossary
        if Path(glossary).is_absolute() or ".." in Path(glossary).parts:
            raise MigrationError("--glossary must be a relative path inside --output.")
        self.glossary_pages = set(glossary_pages)
        if not self.glossary_pages <= client.allowed:
            raise MigrationError("Every --glossary-page must also be explicitly selected with --page.")
        self.pages = {}
        self.files = {}
        self.candidates = {}
        self.page_links = {}
        self.assets = {}
        self.links = []
        self.issues = []
        self.terms = []
        self.diagrams = []
        self.failed_pages = []

    def issue(self, pid, message):
        item = {"page": pid, "message": message}
        if item not in self.issues:
            self.issues.append(item)

    def review_link(self, pid, target, reason):
        item = {"page": pid, "target": target, "reason": reason}
        if item not in self.links:
            self.links.append(item)

    def inspect_diagram(self, pid, source, data, kind):
        try:
            for target in diagram_links(data, kind):
                self.review_link(pid, target, f"Diagram reference in {source}; not fetched or resolved")
        except MigrationError as exc:
            self.issue(pid, f"{source}: {exc}")

    def source_url(self, pid):
        return self.client.base + "/pages/viewpage.action?pageId=" + pid

    def page_ref(self, element, current):
        pid = element.get(f"{{{RI}}}content-id")
        if pid and pid.isdigit():
            return pid
        title = element.get(f"{{{RI}}}content-title", "")
        space = element.get(f"{{{RI}}}space-key") or self.pages[current].get("space", {}).get("key", "")
        matches = [p for p, data in self.pages.items()
                   if data["title"] == title and data.get("space", {}).get("key", "") == space]
        return matches[0] if len(matches) == 1 else None

    def resolve_link(self, pid, target):
        try:
            return self._resolve_link(pid, target)
        except ValueError:
            self.review_link(pid, target, "Malformed URL; original target retained for manual review, not fetched")
            return None

    def _resolve_link(self, pid, target):
        parsed = url.urlsplit(target)
        if parsed.scheme in {"https", "http"} and not parsed.hostname:
            raise ValueError("HTTP URL has no host")
        if parsed.scheme and parsed.scheme not in {"https", "http", "mailto", "tel"}:
            self.review_link(pid, target, "Unsupported URL scheme; kept as text")
            return None
        absolute = url.urljoin(self.source_url(pid), target)
        absolute_parsed = url.urlsplit(absolute)
        if target.startswith("#"):
            self.review_link(pid, target, "Check Confluence anchor against Markdown heading")
            return absolute
        if origin(absolute) == origin(self.client.base):
            try:
                linked = page_id(absolute)
            except MigrationError:
                linked = None
            if linked in self.client.allowed:
                query = url.parse_qs(absolute_parsed.query, keep_blank_values=True)
                page_path = (absolute_parsed.path == self.client.context + "/pages/viewpage.action"
                             or re.fullmatch(re.escape(self.client.context) + r"/(?:spaces/[^/]+/)?pages/" + linked + r"(?:/.*)?", absolute_parsed.path))
                if not page_path or set(query) - {"pageId", "pageVersion"} or len(query.get("pageId", [])) > 1:
                    self.review_link(pid, absolute, "Page action, context or query does not identify an ordinary page view; original link retained")
                    return absolute
                versions = query.get("pageVersion")
                if versions is not None:
                    exported_version = self.pages.get(linked, {}).get("version", {}).get("number")
                    if exported_version is None or versions != [str(exported_version)]:
                        self.review_link(pid, absolute, "Historical or ambiguous page version does not match the export; original link retained")
                        return absolute
                if parsed.fragment:
                    self.review_link(pid, absolute, "Map Confluence anchor to a verified Markdown heading; original link retained")
                    return absolute
                marker = "migration-page-link-" + uuid.uuid4().hex
                self.page_links[marker] = (pid, linked, absolute)
                return marker
            attachment_match = re.fullmatch(re.escape(self.client.context) + r"/download/attachments/(\d+)/([^/]+)", absolute_parsed.path)
            if attachment_match:
                if absolute_parsed.query or absolute_parsed.fragment:
                    self.review_link(pid, absolute, "Attachment version, query or fragment is not verified against the exported asset; original link retained")
                    return absolute
                asset = self.assets.get((attachment_match[1], url.unquote(attachment_match[2])))
                if asset:
                    return asset
        self.review_link(pid, absolute, "Not fetched; verify target and access manually")
        return absolute

    def materialize_links(self, document, final=False):
        for marker, (pid, linked, original) in self.page_links.items():
            if marker not in document:
                continue
            destination = self.files.get(linked, original) if final else original
            if final and linked not in self.files:
                self.review_link(pid, original, "Selected page was not migrated; original link retained")
            document = document.replace(marker, destination)
        return document

    def fetch(self):
        for pid in sorted(self.client.allowed, key=int):
            try:
                data = self.client.page(pid)
                write_file(self.root, f"sources/{pid}.storage.xml", data["body"]["storage"]["value"])
                self.pages[pid] = data
                self.candidates[pid] = f"{pid}-{slug(data['title'])}.md"
            except (MigrationError, OSError) as exc:
                self.failed_pages.append(pid)
                self.issue(pid, str(exc))
        for pid in self.pages:
            try:
                attachments = self.client.attachments(pid)
            except MigrationError as exc:
                self.issue(pid, str(exc))
                continue
            for row in attachments:
                title = row.get("title", "attachment")
                media = row.get("metadata", {}).get("mediaType", "")
                drawio = media == "application/vnd.jgraph.mxfile" or title.lower().endswith(".drawio")
                puml = title.lower().endswith((".puml", ".plantuml")) or media in {"text/x-plantuml", "application/x-plantuml"}
                name = f"{pid}-{slug(str(row['id']))}-{slug(title)}"
                if drawio:
                    name = re.sub(r"\.(drawio|xml)$", "", name, flags=re.I) + ".drawio"
                elif puml:
                    name = re.sub(r"\.(puml|plantuml)$", "", name, flags=re.I) + ".puml"
                relative = "assets/" + name
                diagram = {"page": pid, "source": relative, "svgs": []}
                if drawio:
                    self.diagrams.append(diagram)
                try:
                    data = self.client.attachment(pid, row)
                    write_file(self.root, relative, data)
                    self.assets[(pid, title)] = relative
                    if drawio or puml:
                        self.inspect_diagram(pid, relative, data, "drawio" if drawio else "plantuml")
                    if drawio:
                        diagram["svgs"] = svg_paths(relative, len(drawio_pages(data)))
                except MigrationError as exc:
                    self.issue(pid, f"Attachment {title}: {exc}")

    def glossary_text(self, pid, element):
        """Extract visible labels from saved storage without fetching references."""
        tag = local_name(element)
        if tag == "link":
            for body_tag in ("ac:link-body", "ac:plain-text-link-body"):
                body = element.find(body_tag, NS)
                if body is not None:
                    return self.glossary_text(pid, body)
            ref = element.find("ri:page", NS)
            if ref is not None:
                title = ref.get(f"{{{RI}}}content-title")
                if not title:
                    title = self.pages.get(self.page_ref(ref, pid), {}).get("title")
                if title:
                    return title
            for ref_tag, attr in (("ri:attachment", "filename"), ("ri:url", "value")):
                ref = element.find(ref_tag, NS)
                if ref is not None and ref.get(f"{{{RI}}}{attr}"):
                    return ref.get(f"{{{RI}}}{attr}")
            if element.get(f"{{{AC}}}anchor"):
                return element.get(f"{{{AC}}}anchor")
            raise MigrationError("Unresolved reference label")
        if tag == "br":
            return "\n"
        if tag not in {"td", "th", "p", "div", "span", "strong", "b", "em", "i", "u", "s",
                       "del", "strike", "code", "pre", "sub", "sup", "link-body",
                       "plain-text-link-body", "a", "ul", "ol", "li"}:
            raise MigrationError(f"Unsupported glossary content: {tag}")
        value = element.text or ""
        for child in element:
            value += self.glossary_text(pid, child) + (child.tail or "")
        return value + ("\n" if tag in {"p", "div", "li"} else "")

    def merge_glossary(self):
        path = self.root / self.glossary
        if path.is_symlink() or not path.resolve().is_relative_to(self.root) or any(p.is_symlink() for p in path.parents if p != self.root and p.is_relative_to(self.root)):
            raise MigrationError("Glossary path escapes the destination or is a symlink.")
        existing = path.read_text(encoding="utf-8") if path.exists() else "# 12. Glossary\n"
        known = {}
        # Preserve the existing file verbatim; only append evidenced new entries.
        for line in existing.splitlines():
            cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
            if line.lstrip().startswith("|") and len(cells) >= 2:
                known[term_key(cells[0].strip())] = cells[1].strip()
        additions = []
        for pid, term, definition in self.terms:
            key = term_key(glossary_cell(term))
            formatted_definition = glossary_cell(definition)
            if key in known:
                if inline_literal(known[key]) != inline_literal(formatted_definition):
                    self.issue(pid, f"Glossary conflict for {term}: existing definition retained; compare the selected glossary page.")
                continue
            known[key] = formatted_definition
            reference = url.quote(os.path.relpath(self.root / self.files[pid], path.parent).replace(os.sep, "/"))
            additions.append(f"| {glossary_cell(term)} | {formatted_definition} | [{pid}]({reference}) |")
        if additions:
            existing += "\n\n## Imported terms\n\n| Term | Definition | Source |\n| --- | --- | --- |\n" + "\n".join(additions) + "\n"
        if not self.terms:
            self.issue("glossary", "Review terminology against chapter 12; no glossary table was selected for automatic import.")
        path.parent.mkdir(parents=True, exist_ok=True)
        # This is the one intentional merge, never a replacement of existing entries.
        path.write_text(existing, encoding="utf-8")

    def run(self, include_descendants=False, max_pages=100, max_depth=20):
        self.root.mkdir(parents=True, exist_ok=True)
        # Reports must never clobber the record of a previous run.
        for report in ("migration.json", "migration-summary.md", "links-to-review.md"):
            if (self.root / report).exists():
                raise MigrationError("Destination already contains a migration report. Use a new staging folder for another run.")
        if include_descendants:
            scope = self.client.discover_descendants(max_pages, max_depth)
            for error in scope["errors"]:
                self.issue(error["page"], error["message"])
        self.fetch()
        documents = {}
        trees = {}
        for pid, page in self.pages.items():
            try:
                tree = parse_storage(page["body"]["storage"]["value"])
                converter = Markdown(self, pid)
                body = converter.children(tree).strip()
                version = page.get("version", {}).get("number", "?")
                document = (f"# {escaped(page['title'])}\n\n"
                            f"Source: [{pid}]({self.source_url(pid)}) · Version: {version}\n\n"
                            f"{body}\n")
                filename = self.candidates[pid]
                if (self.root / filename).exists() or (self.root / filename).is_symlink():
                    raise MigrationError(f"Draft already exists: {filename}; use a fresh working export.")
                # First write keeps original links. Failed writes never become local targets.
                write_file(self.root, filename, self.materialize_links(document))
                self.files[pid] = filename
                documents[pid] = document
                trees[pid] = tree
            except (MigrationError, OSError) as exc:
                self.failed_pages.append(pid)
                self.issue(pid, str(exc))
        for pid, document in documents.items():
            path = self.root / self.files[pid]
            if path.is_symlink() or path.read_text(encoding="utf-8") != self.materialize_links(document):
                raise MigrationError("Draft changed during conversion; refusing to overwrite it.")
            path.write_text(self.materialize_links(document, final=True), encoding="utf-8")
            if pid in self.glossary_pages:
                for table_index, table in enumerate(trees[pid].iter("table"), 1):
                    header_rows = set(table.findall("./thead/tr"))
                    for row_index, row in enumerate(table.iter("tr")):
                        cells = list(row)
                        if row in header_rows or any(c.get("scope") == "col" for c in cells):
                            continue
                        if cells and all(local_name(c) == "th" for c in cells) and not any(c.get("scope") == "row" for c in cells):
                            if row_index:
                                self.issue(pid, "Additional all-header glossary row skipped; verify that it is a heading, not a term definition.")
                            continue
                        if len(cells) >= 2 and all(local_name(c) in {"td", "th"} for c in cells):
                            try:
                                term, definition = (self.glossary_text(pid, c).strip() for c in cells[:2])
                                if not term or not definition:
                                    raise MigrationError("Missing term or definition")
                                self.terms.append((pid, term, definition))
                            except MigrationError as exc:
                                self.issue(pid, f"Glossary table {table_index}, row {row_index + 1} was not imported: {exc}; review the saved storage.")
                        elif cells:
                            self.issue(pid, f"Glossary table {table_index}, row {row_index + 1} has no term/definition pair; review the saved storage.")
        self.merge_glossary()
        report = {
            "base_url": self.client.base,
            "selected_pages": sorted(self.client.allowed, key=int),
            "scope": self.client.scope,
            "pages": [{"id": p, "file": f, "version": self.pages[p].get("version", {}).get("number")}
                      for p, f in self.files.items() if p not in self.failed_pages],
            "failed_pages": self.failed_pages,
            "attachments": sorted(set(self.assets.values())),
            "drawio": self.diagrams, "glossary": self.glossary,
            "links_to_review": self.links, "issues": self.issues,
        }
        write_file(self.root, "migration.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        rows = ["# Links to review", "", "No link targets were fetched. Mark results here after an explicitly authorised review.", "",
                "| Source page | Target | Reason | Result |", "| --- | --- | --- | --- |"]
        rows += [f"| {i['page']} | {table_cell(escaped(i['target']))} | {table_cell(i['reason'])} | Open |" for i in self.links]
        write_file(self.root, "links-to-review.md", "\n".join(rows) + "\n")
        summary = ["# Confluence migration summary", "",
                   f"Selected pages: {len(self.client.allowed)}. Converted: {len(report['pages'])}. Failed: {len(self.failed_pages)}.",
                   f"Attachments: {len(report['attachments'])}. Draw.io sources: {len(self.diagrams)}.",
                   f"Glossary: [{self.glossary}]({url.quote(self.glossary)}). Links to review: {len(self.links)}.", "",
                   "## Pages", ""]
        summary += [f"- [{p['id']}]({p['file']}), source version {p['version']}" for p in report["pages"]]
        summary += ["", "## Follow-up", "", "- Check terminology, Markdown layout, tables and diagram fidelity against the saved storage and attachments."]
        if self.diagrams:
            summary.append("- Run render_drawio.py and resolve every missing SVG; export alone does not complete the migration. See diagram-report.json after rendering.")
        summary += [f"- Page {i['page']}: {i['message']}" for i in self.issues]
        summary += ["", f"Descendant discovery: {self.client.scope['include_descendants']}. Scope complete: {self.client.scope['complete']}.",
                    "No ordinary linked pages or external link targets were fetched. Confluence was read only.", ""]
        write_file(self.root, "migration-summary.md", "\n".join(summary))
        return report


class Markdown:
    def __init__(self, export, pid):
        self.export = export
        self.pid = pid
        self.plantuml_count = 0

    def children(self, element):
        result = escaped(element.text or "")
        for child in element:
            result += self.node(child) + escaped(child.tail or "")
        return result

    def asset(self, attachment):
        ref = attachment.find("ri:page", NS)
        owner = self.export.page_ref(ref, self.pid) if ref is not None else self.pid
        name = attachment.get(f"{{{RI}}}filename", "")
        path = self.export.assets.get((owner, name))
        if not path:
            self.export.review_link(self.pid, f"attachment: {name} (page {owner or plain(ref) or 'unresolved'})", "Attachment unavailable or belongs to an unselected page")
        return path, name

    def macro(self, element):
        name = element.get(f"{{{AC}}}name", "unknown")
        params = {c.get(f"{{{AC}}}name", ""): plain(c) for c in element.findall("ac:parameter", NS)}
        raw = element.find("ac:plain-text-body", NS)
        rich = element.find("ac:rich-text-body", NS)
        plantuml_code = name == "code" and params.get("language", "").strip().lower() in {"plantuml", "puml"}
        if name in {"code", "noformat"} and not plantuml_code:
            return fenced(plain(raw), params.get("language", ""))
        if name in {"plantuml", "plantumlrender"} or plantuml_code:
            source = plain(raw)
            if not source.strip():
                attachment = element.find(".//ri:attachment", NS)
                path, label = self.asset(attachment) if attachment is not None else (None, "")
                if path and path.endswith(".puml"):
                    return f"\n\n[PlantUML source: {escaped(label)}]({path})\n\n"
                filename = params.get("filename", params.get("attachment", ""))
                path = self.export.assets.get((self.pid, filename))
                if path and path.endswith(".puml"):
                    return f"\n\n[PlantUML source]({path})\n\n"
            else:
                self.plantuml_count += 1
                path = f"assets/{self.pid}-plantuml-{self.plantuml_count}.puml"
                write_file(self.export.root, path, source)
                self.export.inspect_diagram(self.pid, path, source.encode("utf-8"), "plantuml")
                return f"\n\n[PlantUML source]({path})" + fenced(source, "plantuml")
        if name in {"drawio", "drawio-sketch"}:
            owner = params.get("pageId", self.pid)
            title = params.get("diagramName", params.get("name", ""))
            source = self.export.assets.get((owner, title))
            if source and source.endswith(".drawio"):
                svgs = next((d['svgs'] for d in self.export.diagrams if d['source'] == source), [])
                if svgs:
                    images = "\n\n".join(f"![{escaped(title)} — {i}]({svg})" for i, svg in enumerate(svgs, 1))
                    return f"\n\n{images}\n\n[Draw.io source]({source})\n\n"
            self.export.review_link(self.pid, f"drawio: {title} (page {owner})", "Diagram source missing or outside selected pages")
        if name == "anchor":
            anchor = params.get("", "")
            self.export.review_link(self.pid, self.export.source_url(self.pid) + "#" + url.quote(anchor),
                                    "Explicit Confluence anchor omitted; map to a verified Markdown heading")
            return ""
        if name in {"info", "note", "warning", "tip"}:
            title = params.get("title", "")
            label = name.capitalize() + (": " + escaped(title) if title else "")
            body = self.children(rich).strip() if rich is not None else ""
            return "\n\n> **" + label + "**\n>\n> " + body.replace("\n", "\n> ") + "\n\n"
        if name in {"panel", "expand", "section", "column"}:
            title = params.get("title", "")
            return "\n\n" + (f"**{escaped(title)}**\n\n" if title else "") + (self.children(rich) if rich is not None else "") + "\n\n"
        if name == "toc":
            return ""  # Markdown renderer owns the table of contents.
        self.export.issue(self.pid, f"Macro '{name}' requires review; its storage is retained in sources/{self.pid}.storage.xml.")
        if name == "jira":
            for parameter in ("key", "jqlQuery"):
                if params.get(parameter, "").strip():
                    self.export.review_link(self.pid, f"jira:{parameter}: {params[parameter]}",
                                            "Jira identity/query retained from storage; issue content was not fetched")
        for ref in element.findall(".//ri:page", NS):
            target = self.export.page_ref(ref, self.pid)
            self.export.review_link(self.pid, self.export.source_url(target) if target else ref.get(f"{{{RI}}}content-title", "unknown"), "Macro page reference was not expanded")
        retained = self.children(rich) if rich is not None else fenced(plain(raw)) if raw is not None else ""
        return f"\n\n> Migration review: `{escaped(name)}` macro; see saved page storage.\n\n{retained}"

    def node(self, element):
        tag = local_name(element)
        if tag in {"structured-macro", "macro"}:
            return self.macro(element)
        if tag in {"script", "style"}:
            self.export.issue(self.pid, f"{tag} retained only in source storage.")
            return ""
        if tag in {"pre", "code"}:
            value = plain(element)
            if tag == "pre":
                return fenced(value)
            ticks = "`" * max(1, max((len(m[0]) + 1 for m in re.finditer(r"`+", value)), default=1))
            return f"{ticks} {value} {ticks}"
        if tag == "image":
            attachment = element.find("ri:attachment", NS)
            if attachment is not None:
                path, label = self.asset(attachment)
                if path:
                    return f"![{escaped(label)}]({path})"
                return f"[Missing image: {escaped(label)}]"
            external = element.find("ri:url", NS)
            if external is not None:
                target = external.get(f"{{{RI}}}value", "")
                self.export.review_link(self.pid, target, "External image not downloaded")
            return "[External image: see links-to-review.md]"
        if tag == "img":
            target = element.get("src", "")
            resolved = self.export.resolve_link(self.pid, target)
            if resolved and resolved.startswith("assets/"):
                return f"![{escaped(element.get('alt', ''))}]({resolved})"
            return "[External or unresolved image: see links-to-review.md]"
        if tag == "link":
            body = element.find("ac:link-body", NS)
            if body is None:
                body = element.find("ac:plain-text-link-body", NS)
            label = self.children(body).strip() if body is not None else ""
            attachment = element.find("ri:attachment", NS)
            page = element.find("ri:page", NS)
            anchor = element.get(f"{{{AC}}}anchor", "")
            if attachment is not None:
                path, title = self.asset(attachment)
                return f"[{label or escaped(title)}]({path})" if path else label or escaped(title)
            if page is not None:
                target = self.export.page_ref(page, self.pid)
                title = page.get(f"{{{RI}}}content-title") or self.export.pages.get(target, {}).get("title") or target or "page"
                if target:
                    href = self.export.source_url(target)
                else:
                    space = page.get(f"{{{RI}}}space-key", self.export.pages[self.pid].get("space", {}).get("key", ""))
                    href = self.export.client.base + "/display/" + url.quote(space, safe="") + "/" + url.quote(title, safe="")
                if anchor:
                    href += "#" + url.quote(anchor, safe="")
                resolved = self.export.resolve_link(self.pid, href)
                return f"[{label or escaped(title)}](<{resolved}>)"
            if anchor:
                target = self.export.resolve_link(self.pid, "#" + url.quote(anchor))
                return f"[{label or escaped(anchor)}](<{target}>)"
            self.export.issue(self.pid, "Unresolved Confluence link; inspect source storage.")
            return label or escaped(plain(element))
        if tag == "a":
            target = self.export.resolve_link(self.pid, element.get("href", ""))
            label = self.children(element).strip() or escaped(element.get("href", "link"))
            if target:
                destination = url.quote(target, safe=':/?=&%#@+;,-._~').replace("&", "&amp;")
                return f"[{label}](<{destination}>)"
            return label
        if tag in {"ul", "ol"}:
            entries = []
            step = -1 if "reversed" in element.attrib else 1
            try:
                index = int(element.get("start", str(len(element)) if step == -1 else "1"))
            except ValueError:
                index = 1
                self.export.issue(self.pid, "Invalid list start value; verify numbering against storage.")
            for child in element:
                if tag == "ol" and child.get("value") is not None:
                    try:
                        index = int(child.get("value"))
                    except ValueError:
                        self.export.issue(self.pid, "Invalid list item value; verify numbering against storage.")
                value = self.children(child).strip()
                entries.append((index, value))
                index += step
            nonsequential = tag == "ol" and (any(n < 0 or n > 999999999 for n, _ in entries)
                or any(entries[i][0] != entries[i - 1][0] + 1 for i in range(1, len(entries))))
            if nonsequential:
                self.export.issue(self.pid, "Nonsequential list numbering retained as labelled paragraphs; review layout against storage.")
            lines = []
            for index, value in entries:
                marker = (f"{index}\\. " if nonsequential else f"{index}. ") if tag == "ol" else "- "
                lines.append(marker + value.replace("\n", "\n" + " " * len(marker)))
            return "\n\n" + ("\n\n" if nonsequential else "\n").join(lines) + "\n\n"
        if tag == "table":
            rows = []
            for row in element.iter("tr"):
                cells = [c for c in row if local_name(c) in {"td", "th"}]
                if any(c.get("colspan", "1") != "1" or c.get("rowspan", "1") != "1" for c in cells):
                    self.export.issue(self.pid, "Table has merged cells; review the flattened Markdown table against source storage.")
                values = [self.children(c).strip() for c in cells]
                if any("\n" in value for value in values):
                    self.export.issue(self.pid, "Multiline table cells flattened with separators; review lists, paragraphs and code against source storage.")
                rows.append([table_cell(value) for value in values])
            if not rows:
                return ""
            width = max(map(len, rows))
            rows = [r + [""] * (width - len(r)) for r in rows]
            rows.insert(1, ["---"] * width)
            return "\n\n" + "\n".join("| " + " | ".join(r) + " |" for r in rows) + "\n\n"
        content = self.children(element)
        if re.fullmatch("h[1-6]", tag):
            return "\n\n" + "#" * int(tag[1]) + " " + content.strip() + "\n\n"
        if tag in {"p", "div", "section", "layout", "layout-section", "layout-cell"}:
            return "\n\n" + content.strip() + "\n\n"
        if tag in {"strong", "b"}:
            return "**" + content + "**"
        if tag in {"em", "i"}:
            return "*" + content + "*"
        if tag in {"s", "del", "strike"}:
            return "~~" + content + "~~"
        if tag == "blockquote":
            return "\n\n> " + content.strip().replace("\n", "\n> ") + "\n\n"
        if tag == "br":
            return "  \n"
        if tag == "hr":
            return "\n\n---\n\n"
        if tag not in {"span", "u", "li", "root", "rich-text-body", "link-body", "plain-text-link-body"}:
            self.export.issue(self.pid, f"Element '{tag}' flattened; inspect source storage for lost formatting.")
        return content


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="HTTPS Confluence Data Center base URL, including context path if any")
    parser.add_argument("--page", action="append", required=True, help="Explicit page ID or URL; repeat for every allowed page")
    parser.add_argument("--include-descendants", action="store_true", help="Discover recursive children of every --page root; requires explicit user scope")
    parser.add_argument("--max-pages", type=int, default=100, help="Tree scope limit including roots (default: 100)")
    parser.add_argument("--max-depth", type=int, default=20, help="Tree depth limit, roots are depth 0 (default: 20)")
    parser.add_argument("--work-dir", "--output", dest="work_dir", required=True,
                        help="Temporary working export OUTSIDE the published docs root; --output is a compatibility alias")
    parser.add_argument("--glossary", default="12-glossary.md", help="Copied chapter 12 path relative to the working export")
    parser.add_argument("--glossary-page", action="append", default=[], help="Selected page ID containing glossary tables; repeat as needed")
    args = parser.parse_args(argv)
    try:
        report = Export(Confluence(args.base_url, args.page), args.work_dir, args.glossary, args.glossary_page).run(
            args.include_descendants, args.max_pages, args.max_depth)
    except (MigrationError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Prepared {len(report['pages'])} page drafts. Diagnostics are in the working export. Reconcile into the target repository and produce one final migration report; do not copy the whole export into docs.")
    return 2 if report["issues"] else 0


if __name__ == "__main__":
    sys.exit(main())
