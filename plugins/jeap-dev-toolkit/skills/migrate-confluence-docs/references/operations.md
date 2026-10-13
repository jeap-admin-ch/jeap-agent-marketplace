# Export and rendering operations

## Authentication

Provide the canonical HTTPS instance URL, optionally with its context path. The exporter uses
`GET /rest/api/content/<id>?expand=body.storage,version,space`, the selected page's paginated
attachment listing, and its `/download/attachments/<id>/...` paths. With explicit subtree authorisation,
`--include-descendants` also uses paginated `GET /rest/api/content/<id>/child/page` endpoints beneath
the selected roots. There are no search, rendered-body, write, or arbitrary-URL commands. Server-provided pagination links and HTTP
redirects are never followed. Cross-origin attachment locations are refused.
REST attachment links such as `/download/attachments/<id>/file` are resolved relative to the configured
application base, including its context path. Already context-prefixed and absolute same-origin links are
also accepted only when they identify the selected page's attachment endpoint.

Anonymous access is attempted first. If needed, the developer creates a Confluence personal
access token with read access to the selected pages and configures **one** of:

- `CONFLUENCE_PAT_FILE`: absolute path to a UTF-8 file containing only the PAT. Keep it outside
  the repository and agent-readable source files; set its permissions to `600` where supported.
  The exporter reads it internally only after authentication is required.
- `CONFLUENCE_TOKEN`: PAT provided by the developer's local process environment.

The file takes precedence when both are configured. The agent must not inspect either value.
No credential file is created, overwritten or deleted by the scripts. They send a Bearer header
only to the configured HTTPS origin and do not print server error bodies or headers. A 401/403
after retry means the PAT or page permissions need attention. An anonymous 404 can also conceal missing
permissions: when a PAT is configured, the helper retries the identical scoped request once with it.
Without configured credentials, or if the authenticated retry also returns 404, that failure is reported;
do not search for a substitute page. Redirects or an HTML response often mean the
base URL points at SSO rather than the REST endpoint. TLS verification stays enabled.

## Working export and reruns

`--work-dir` is the temporary export directory, **not the final documentation folder**. `--output`
remains a compatibility alias with the same working-export semantics. Run both helpers against that
working directory outside the documentation set. The agent then fits the result to the target using
[target-repository.md](target-repository.md). Do not copy an export tree wholesale into a published folder.

Without `--include-descendants`, no child discovery takes place. With it, every `--page` is a root of an
authorised recursive subtree. `--max-pages` (default 100, including roots) and `--max-depth` (default 20,
roots at depth 0) bound discovery. Parent/child edges, roots, limits and discovery errors are saved under
`scope` in `migration.json`. Inaccessible child listings, repeated IDs, cycles and limit hits make the
scope incomplete and produce exit code 2. Already discovered pages are still exported; an incomplete
scope is unfinished work, not evidence that a branch is empty. Review the recorded scope before a larger
rerun. Ordinary links, include macros and title references never expand the allow-list.

`confluence.py --help` lists the input options. Exit codes: `0` means no automatic review issues,
`2` means inspect the reports, and `1` means a fatal input/output/configuration failure. Rendering
and agent review remain necessary even after a clean export. Per-page failures are included in
the reports while other selected pages continue. Attachment-listing and download transport failures,
including response-read timeouts and truncated bodies, are recorded while the remaining export continues.
Storage is saved before conversion so unsupported
formats can be recovered without another request. All attachments of selected pages are fetched into
scratch, even if not embedded, to avoid losing diagram sources. Only the assets used by the final
documentation and its required editable sources are promoted to the target repository. Check unused
attachments for relevant content before discarding them from the deliverable.

Use a new staging folder for a rerun and merge deliberately. Do not delete the old run to silence
an overwrite error. Restore the existing glossary into staging before rerunning. Source IDs and
attachment IDs make generated names deterministic and avoid collisions between pages. The script
does not choose the target's page organisation or names; that is the agent's reconciliation step.
The source IDs/versions and final path mapping belong in the single final migration report. The JSON
manifest, raw storage, per-step diagnostics and draft glossary remain in scratch. Fold the temporary
summary and link list into the final report rather than adding both as published documentation pages.

An already exported tree can serve as input for reconciliation. Preserve pre-existing export files and
user changes; removal of such files is not part of cleaning up scratch created by the current run.

The Markdown converter handles common headings, paragraphs, emphasis, lists, tables, code,
attachments, page links and selected macros. Merged tables, third-party macros, includes and
unrecognised elements require review against `sources/`. It intentionally does not request
`body.export_view`: rendering that representation can expand material from unselected pages.
Title-based links are resolved only against already selected pages in the same space; no title
search is made. External images are listed for review instead of embedded as remote images.
Page links become local only after the destination draft has been successfully written; failed destinations
retain their Confluence URL and get a review entry. Confluence anchors also retain the original URL until
the agent maps them to verified final headings. No raw HTML anchors or `<br>` tags are generated: prose
uses Markdown hard breaks, multiline table cells use ` / ` separators with a review issue, and info/note/
warning/tip macros keep their type and optional title in blockquotes. Complex tables still need editorial
reconciliation; the helper does not implement publisher-specific table directives.
Malformed content URLs are kept in the link-review report without aborting page conversion. Links to a
specific page version become local only when that version matches the exported page. History/actions,
ambiguous query parameters, and attachment links with unverified versions or fragments retain their
original destinations for review; the helper does not fetch older versions to resolve them.
Ordered lists retain their start value. Numbering jumps or reversed sequences use explicitly numbered
paragraphs with a layout-review issue, because Markdown renderers otherwise renumber them. Table pipes
are escaped for GFM tables, including inline code. Glossary imports accept row-header terms (`th` plus
`td`, or `scope="row"`) and exclude explicit table/column headers; ambiguous additional all-header rows
are reported for review.
Imported glossary text is escaped as literal Markdown, including angle brackets, entities and Markdown
metacharacters. Reimport recognises the escaped terms without replacing existing definitions. Implicit
reference labels use titles/filenames in saved storage or already selected pages; no referenced page is
fetched. Unresolved labels and incomplete rows are reported for review instead of silently dropped.

Draw.io attachments are identified by `application/vnd.jgraph.mxfile` or `.drawio`. PlantUML
attachments use `.puml`, `.plantuml`, or a PlantUML media type. Draw.io macro `diagramName` and
optional `pageId`, PlantUML plain-text bodies and attachment references are handled directly.
Other plugin variants remain in storage with a review marker; adapt them from the selected
page/attachment evidence without widening access. Compressed Draw.io tab contents stay intact.
Code macros labelled `plantuml` or `puml` also create editable `.puml` files. Attached and inline PlantUML
references/includes, Draw.io hyperlinks and image references are inspected locally. Compressed Draw.io
tabs are decoded only for inspection, with a size limit; source bytes are never rewritten. Unreadable
sources produce a review issue instead of silently passing the link check. Dynamic include/load expressions
remain unresolved review entries. No dependency is executed or fetched.
This includes `!include_once`, `!include_many`, `!includesub` and legacy `!includeurl`. Quoted literal paths
are distinguished from dynamic expressions, and `%load...()` arguments retain nested or quoted parentheses.

## Renderer setup

Run `render_drawio.py --smoke-test` (optionally with `--container-image`) before exporting pages with
diagrams. It tests the actual sandbox with synthetic inputs; it does not access Confluence. If setup is
missing, use [renderer-setup.md](renderer-setup.md) for native prerequisites and the local build recipe.

## Offline Draw.io rendering

The renderer uses Draw.io Desktop's SVG export, with one temporary input per tab. The original
`.drawio` bytes remain unchanged. Multi-tab files produce the matching `.svg` plus numbered
additional SVGs. It does not approximate a diagram with a different diagram language.
The known legacy SVG 1.1 DOCTYPE emitted by Draw.io is removed from new SVG output; it is unnecessary
for rendering and would otherwise leave an external DTD reference. Other DTDs/entities are refused.

Native Linux requires `bwrap` (bubblewrap) with namespace support, Draw.io Desktop and Xvfb.
The helper wraps the command in `xvfb-run -a` unless it already starts with `xvfb-run`. It creates an empty
filesystem with only system runtime directories (`/usr`, `/bin`, `/lib`, `/lib64`), `/opt/drawio` when
present, font configuration and the loader cache mounted read only. Only `/work` (the temporary renderer
input/output directory) and temporary runtime files are writable. Host home directories, repositories,
`/run`, the host process namespace, X11 sockets and display authentication are not exposed. Environment
variables are cleared inside the native sandbox and application settings are temporary. Nonstandard or
home-directory installations need a local container image; do not broaden mounts to make them work.
Confluence credentials are not forwarded to the renderer. If a system disables the required namespaces,
use the container mode or export offline manually; do not bypass network isolation.

Container mode requires a running local Docker engine and a **locally available** image containing
`xvfb-run` and `drawio` on PATH. Select an image already approved/used by the team. The helper uses
`--init --pull never --network none`, overrides the entrypoint to `xvfb-run -a -e /dev/stderr drawio`,
and mounts only its temporary input/output directory. The init process prevents `xvfb-run` from running
as PID 1 and hanging during Xvfb's readiness handshake. Xvfb errors are captured alongside Draw.io output.
It does not select or download a third-party image.
The client preserves the selected local endpoint: `DOCKER_CONTEXT`, then `DOCKER_HOST`, then the saved
Docker context. Context inspection reads local metadata; the container run uses only the resolved Unix
socket or local named pipe with an empty Docker configuration. Registry/proxy credentials and Confluence
credentials are not forwarded to the container. Remote Docker endpoints are refused; choose a local engine.
The helper queries the engine's user-namespace mode: rootless uses container UID/GID `0:0`, which maps to
the daemon's host user; ordinary Docker uses the invoking user's UID/GID. Electron uses `--no-sandbox`
for all container users because Docker blocks its nested namespace/setuid sandbox. Networking, the
default seccomp profile, capability restrictions and `no-new-privileges`
remain enforced. Rootful `userns-remap` is reported as unsupported rather than disabling that isolation or
making the private staging directory accessible to other users. Use rootless Docker or native rendering.
Each container gets a unique name and is explicitly force-removed after completion, failure or timeout.
On failure, bounded process/state/log diagnostics and SVG existence are captured before removal and saved
in `diagram-report.json`; unavailable diagnostics are recorded as such. The process snapshot distinguishes
Xvfb without a visible Draw.io/Electron process, but cannot establish whether Draw.io previously ran.
If removal cannot be confirmed, rendering stops and records the container name and endpoint in
`diagram-report.json`; remove that container on the recorded engine before retrying.
On macOS/Windows this mode avoids a dependency on Linux bubblewrap on the host. Native desktop
export without this helper must likewise run offline and save every expected SVG inside `assets/`.

`render_drawio.py` requires the working export's `migration.json` inventory; an empty or mistyped directory
fails. A valid inventory explicitly containing no diagrams is a no-op. Every expected source and tab is
checked, and unlisted sources are reported. Failed or malformed Draw.io downloads remain expected entries
in the export inventory, so their missing SVGs cannot silently pass.

The helper can be rerun after fixing a dependency. `diagram-report.json` records SHA-256 hashes of each
source and generated SVG. Existing SVGs are reused only if both hashes match and SVG validation passes;
otherwise they remain untouched and are reported as failed. Preserve/review an unverified or stale SVG
and remove only a known generated file deliberately before regenerating it. A manually exported SVG has
no automated provenance and needs manual source/visual verification, documented in the final report;
do not invent hash records to make it pass. `diagram-report.json` is refreshed on each renderer run.
Each SVG's entry also contains `links_to_review` collected from generated or verified existing SVGs.
Combine these entries with the export's link list in the final migration report; neither collection
authorises fetching link targets. A diagram source may contain references that do not survive rendering,
so retain the source inspection findings as well.

Validation rejects remote/local resource dependencies in SVG and embedded HTML, CSS imports and external
URLs, active content and unsupported/obfuscated resource syntax. Embedded raster data and local fragment
references are allowed, as are ordinary web hyperlinks. Embedded SVG data needs manual review because it
can contain further dependencies. These conservative checks are not a general-purpose SVG sanitizer.
External image dependencies remain unresolved offline: retain the source and report them instead
of fetching anything beyond the approved pages. SVG XML validation alone does not establish visual
fidelity; inspect the pictures and resolve all remaining issues in the migration summary.

Validation is also exposed as a CLI, so callers do not need to import helper internals:

```bash
python3 render_drawio.py --validate-svg path/to/one.svg --validate-svg path/to/two.svg
python3 render_drawio.py --work-dir ./export --check
```

The first command checks resource/syntax restrictions only; it neither normalises files nor certifies
their association with a source. The second checks the export's inventory and recorded source/SVG hashes
without invoking Docker/Draw.io or modifying files/reports. For final paths outside scratch, see the
pair-check command in [target-repository.md](target-repository.md#diagram-maintenance).

## Review and cleanup

Run the helper by absolute path, or keep all Python helpers together when copying to scratch:

```bash
python3 review_migration.py prepare --work-dir ./export
```

`review.json` is an editable scratch checklist with stable IDs. It contains a page-level coverage entry,
headings, all table rows (including header rows), tasks, macros with parameter names/values, conversion
issues and the union of export/SVG links. Jira keys and JQL remain literal, unresolved references. Source
versions, XML element ordinals and source hashes identify the evidence. This is a structural index, not
semantic classification: read prose and split multiple requirements/decisions within a row in the result.

Edit only each item's `status` and `result`: `open` for unfinished work, `resolved` with a verified target
file/heading and outcome, or `omitted` with a deliberate, justified omission. A retained remote link may
be resolved as a migration decision without claiming its reachability was tested; state that explicitly.
Macro content whose identity or meaning is still unknown stays open. Do not bulk-mark items to pass.

After rendering or changing the export, refresh the checklist; unchanged answers survive, while new or
changed evidence is open again. Removed checklist rows cannot hide current inventory entries.

```bash
python3 review_migration.py prepare --work-dir ./export --refresh
python3 review_migration.py report --work-dir ./export
python3 review_migration.py check-cleanup --work-dir ./export
```

`report` creates `review-material.md` **in scratch** with every deduplicated source/target link as its own
row, combined reasons and the editorial result. Fold it into the single final migration report, retaining
all link rows. Add the target validation results and summary; this helper does not certify publishability.
It refuses to replace differing report files; for another draft pass a new scratch filename via `--output`.

`report` and `check-cleanup` return `2` while review answers are missing, source discovery/pages failed or
Draw.io verification fails; `1` means invalid/missing inputs. Both print the scratch location to retain.
The cleanup gate always checks the current inventory, not just saved answers. It cannot prove that a
written resolution matches the final prose and does not provide a Doc-Service validator. It performs no
deletion; only after exit `0`, successful target validation and final report completion may the agent
remove this run's scratch. Pre-existing exports and credential files are never cleanup candidates.
Manual SVG exports without verified renderer hashes keep the automatic gate closed; preserve scratch
until the normal renderer succeeds instead of manufacturing hash evidence.

## Why the helper uses Python

The helper keeps bounded API access, authentication, storage parsing, attachment naming and draft conversion in one process,
using only the Python standard library. Curl would also be suitable for the HTTP transport; changing
transport would not remove the conversion and reconciliation work. Python is an implementation choice,
not a requirement imposed on the documentation format or target platform.

The helper deliberately stops at a deterministic draft. Chapter assignment, prose integration, target
metadata and glossary decisions belong to the agent with the target repository's conventions available.
Do not build another project-specific exporter to perform that editorial step.

## Format references

- [Atlassian storage format](https://confluence.atlassian.com/doc/confluence-storage-format-790796544.html)
  documents page/attachment references, rich text and macro bodies.
- [Draw.io SVG export](https://www.drawio.com/docs/manual/export/export-to-svg/) describes SVG output.
- [Draw.io Desktop CLI options](https://github.com/jgraph/drawio-desktop/blob/dev/src/main/args.js)
  define the export, format and output switches used by the helper.
