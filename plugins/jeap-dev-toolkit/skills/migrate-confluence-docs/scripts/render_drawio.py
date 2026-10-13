#!/usr/bin/env python3
"""Render exported .drawio sources locally with networking disabled.

Native Linux: bubblewrap plus Draw.io Desktop and an isolated xvfb-run display.
Other platforms: an already installed local container image with xvfb-run and drawio.
No image pulling, package installation or public rendering services.
"""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET

from confluence import MigrationError, diagram_links, drawio_pages, svg_paths, write_file


class ContainerCleanupError(MigrationError):
    """Do not launch more containers when cleanup cannot be confirmed."""


class RendererError(MigrationError):
    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


def output_tail(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return (value or "")[-8192:]


def container_diagnostics(client, name, env):
    """Inspect only our renderer, before removing it; never dump Docker config/env."""
    details = {"container": name, "endpoint": client[2]}
    for key, args in (
        ("state", ["inspect", "--format", '{"image_id":{{json .Image}},"state":{{json .State}}}', name]),
        ("processes", ["top", name, "-eo", "pid,ppid,stat,comm"]),
        ("logs", ["logs", "--tail", "40", name]),
    ):
        try:
            result = subprocess.run([*client, *args], env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=5, check=False)
            details[key] = output_tail(result.stdout) if result.returncode == 0 else "unavailable (container may have exited)"
            if key == "state" and result.returncode == 0:
                try:
                    state = json.loads(result.stdout)
                    if isinstance(state, dict) and isinstance(state.get("image_id"), str):
                        details.update(image_id=state["image_id"], state=state.get("state"))
                except (ValueError, TypeError):
                    pass
            if key == "logs" and result.returncode == 0:
                details["log_stderr"] = output_tail(result.stderr)
        except (OSError, subprocess.TimeoutExpired):
            details[key] = "unavailable (diagnostic command failed/timed out)"
    processes = details["processes"].splitlines()[1:]
    names = {line.split()[-1].lower() for line in processes if line.split()}
    if "xvfb" in names and not any("drawio" in name or "electron" in name for name in names):
        details["observation"] = "Xvfb is running but no Draw.io/Electron process is visible; it may not have started or may have exited."
    return details


def renderer_env():
    return {key: value for key, value in os.environ.items()
            if key in {"PATH", "SYSTEMROOT", "SystemRoot"}}


def container_user(client):
    try:
        result = subprocess.run([*client, "info", "--format", "{{json .SecurityOptions}}"],
                                env=renderer_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=15, check=False)
        options = json.loads(result.stdout) if result.returncode == 0 else None
        if not isinstance(options, list) or any(not isinstance(option, str) for option in options):
            raise ValueError()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise MigrationError("Cannot determine Docker user-namespace mapping; check the selected local engine.") from None
    modes = {option.split(",", 1)[0] for option in options}
    if "name=rootless" in modes:
        # Container root maps to the rootless daemon's host user, who owns /work.
        return "0:0"
    if "name=userns" in modes:
        raise MigrationError("Rootful Docker userns-remap cannot access the private staging directory with host IDs. Use a local rootless engine or native rendering; namespace isolation was not disabled.")
    return f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "0:0"


def remove_container(client, name, env):
    try:
        result = subprocess.run([*client, "rm", "--force", "--", name], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False)
        if result.returncode == 0:
            return
        # A failed start might never have created a container. Verify absence rather
        # than relying on translated stderr or silently ignoring removal failures.
        result = subprocess.run([*client, "ps", "--all", "--filter", f"name=^/{name}$", "--format", "{{.ID}}"],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False)
        if result.returncode == 0 and not result.stdout.strip():
            return
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise ContainerCleanupError(f"Could not confirm removal of renderer container {name} on {client[2]}. Remove it on that engine before retrying; no further containers were started.")


def run_renderer(cmd, work, container=False, timeout=120):
    env = renderer_env()
    env["DRAWIO_DISABLE_UPDATE"] = "true"
    details = {"timeout_seconds": timeout}
    if container:
        client = cmd[:cmd.index("run")]
        name = cmd[cmd.index("--name") + 1]
        details["image"] = cmd[cmd.index("--entrypoint") + 2]
    try:
        try:
            result = subprocess.run(cmd, cwd=work, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=timeout, check=False)
            details.update(returncode=result.returncode, stdout=output_tail(result.stdout), stderr=output_tail(result.stderr),
                           output_exists=(work / "output.svg").is_file())
            if result.returncode == 0 and (work / "output.svg").is_file():
                return result
            reason = "Renderer produced no SVG." if result.returncode == 0 else f"Renderer exited with code {result.returncode}."
        except subprocess.TimeoutExpired as exc:
            details.update(stdout=output_tail(exc.stdout), stderr=output_tail(exc.stderr))
            reason = f"Renderer timed out after {timeout}s."
        details["output_exists"] = (work / "output.svg").is_file()
        if container:
            details.update(container_diagnostics(client, name, env))
        raise RendererError(reason + " See diagram-report.json diagnostics; fix the runtime before retrying.", details)
    finally:
        if container:
            try:
                remove_container(client, name, env)
            except ContainerCleanupError as exc:
                exc.diagnostics = details
                raise


def local_docker_endpoint():
    """Resolve client selection without copying Docker config into the renderer."""
    context = os.environ.get("DOCKER_CONTEXT")
    endpoint = os.environ.get("DOCKER_HOST") if not context else None
    if not endpoint:
        # Context inspection reads local metadata only; it does not contact the engine.
        # DOCKER_CONTEXT overrides DOCKER_HOST, then Docker's saved context applies.
        cmd = ["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"]
        if context:
            cmd += ["--", context]
        env = {key: value for key, value in os.environ.items()
               if key in {"PATH", "SYSTEMROOT", "SystemRoot", "HOME", "USERPROFILE", "DOCKER_CONFIG"}}
        try:
            result = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=15, check=False)
            if result.returncode:
                raise MigrationError("Cannot inspect the selected Docker context. Check the local Docker configuration.")
            endpoint = json.loads(result.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise MigrationError("Cannot resolve the selected local Docker endpoint.") from None
    # Remote engines cannot use the local staging bind mount and would receive source data.
    if not isinstance(endpoint, str) or not (
            re.fullmatch(r"unix:///[^\x00\r\n]+", endpoint)
            or re.fullmatch(r"npipe:////\./pipe/[^/\\\x00\r\n]+", endpoint)):
        raise MigrationError("Rendering requires a local Docker Unix socket or named pipe; select a local context or DOCKER_HOST.")
    return endpoint


def render_command(command, image, work, docker_config=None):
    args = ["--export", "--format", "svg", "--embed-svg-images",
            "--embed-svg-fonts", "false", "--output", "output.svg", "input.drawio"]
    if image:
        if not shutil.which("docker"):
            raise MigrationError("Docker is required for --container-image.")
        endpoint = local_docker_endpoint()
        client = ["docker", "--host", endpoint, "--config", str(docker_config or work / "docker-config")]
        user = container_user(client)
        # Docker's seccomp/capability restrictions prevent Chromium namespace setup,
        # and no-new-privileges blocks its setuid fallback, including for non-root.
        # Keep Docker's isolation; disable only the nested Chromium sandbox.
        args = ["--no-sandbox", *args]
        # xvfb-run must not be PID 1: Xvfb's readiness signal needs an ordinary
        # parent process. Docker's init also reaps orphaned Electron processes.
        # Explicit cleanup in run_renderer keeps exited containers inspectable
        # until failure evidence has been captured.
        return [*client, "run", "--init", "--name", "confluence-drawio-" + uuid.uuid4().hex,
                "--pull", "never", "--network", "none", "--user", user,
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--env", "HOME=/tmp", "--env", "DRAWIO_DISABLE_UPDATE=true",
                "--mount", f"type=bind,source={work},target=/work", "--workdir", "/work",
                "--entrypoint", "xvfb-run", image, "-a", "-e", "/dev/stderr", "drawio", *args]
    if not shutil.which("bwrap"):
        raise MigrationError("Native rendering requires bubblewrap to block network access. Use an installed local --container-image or export offline manually.")
    # Start with an empty filesystem, never bind the host root/home, /run or X11.
    # Only system runtime directories, fonts and the conventional Draw.io install
    # directory are readable. Nonstandard installs should use container mode.
    runtime = []
    for name in ("/usr", "/bin", "/lib", "/lib64", "/opt/drawio", "/etc/fonts", "/etc/ld.so.cache"):
        path = Path(name)
        if path.exists():
            if path.is_symlink():
                if not path.resolve().is_relative_to("/usr"):
                    raise MigrationError("Unsupported runtime symlink; use an isolated local renderer image.")
                runtime += ["--symlink", os.readlink(path), name]
            else:
                runtime += ["--ro-bind", name, name]
    headless = command if Path(command[0]).name == "xvfb-run" else ["xvfb-run", "-a", *command]
    return ["bwrap", "--die-with-parent", "--new-session", "--unshare-net", "--unshare-pid",
            "--unshare-ipc", "--unshare-uts", "--clearenv", *runtime,
            "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
            "--bind", str(work), "/work", "--chdir", "/work",
            "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
            "--setenv", "HOME", "/work", "--setenv", "TMPDIR", "/tmp",
            "--setenv", "XDG_CONFIG_HOME", "/work/config",
            "--setenv", "XDG_CACHE_HOME", "/work/cache",
            "--setenv", "DRAWIO_DISABLE_UPDATE", "true", *headless, *args]


def normalize_renderer_svg(data):
    # Draw.io emits this legacy external declaration. It is unnecessary for SVG
    # rendering; remove this exact known form instead of allowing arbitrary DTDs.
    return data.replace(b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">', b"")


def validate_svg(data):
    try:
        data.decode("utf-8")
    except UnicodeError:
        raise MigrationError("SVG must be UTF-8.") from None
    if b"\x00" in data:
        raise MigrationError("SVG must be UTF-8 without NUL characters.")
    # Reject declarations and processing instructions that could add dependencies.
    without_declaration = re.sub(br"^\s*<\?xml\s+[^?]*\?>", b"", data)
    if re.search(br"<!\s*(DOCTYPE|ENTITY)|<\?", without_declaration, re.I):
        raise MigrationError("SVG contains a DTD, entity or processing instruction.")
    try:
        tree = ET.fromstring(data)
    except ET.ParseError:
        raise MigrationError("Renderer did not produce valid SVG XML.") from None
    if tree.tag != "{http://www.w3.org/2000/svg}svg":
        raise MigrationError("Renderer output is not SVG.")

    def resource(value):
        value = value.strip()
        # Embedded SVG/HTML can themselves contain remote dependencies: refuse them.
        return value.startswith("#") or bool(re.fullmatch(
            r"data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\s]+", value, re.I))

    def css(value):
        # Conservative checks, not a general CSS sanitizer. Obfuscation requires review.
        if ("\\" in value or "/*" in value or re.search(r"@import|image-set\s*\(", value, re.I)):
            raise MigrationError("SVG contains unsupported CSS resource syntax; review offline.")
        for match in re.finditer(r"url\s*\((.*?)\)", value, re.I | re.S):
            if not resource(match[1].strip().strip("\"'")):
                raise MigrationError("SVG CSS references an external resource.")
        if re.search(r"url\s*\(", re.sub(r"url\s*\(.*?\)", "", value, flags=re.I | re.S), re.I):
            raise MigrationError("SVG contains unrecognised CSS URL syntax.")

    for element in tree.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag.startswith("animate") or tag in {"script", "iframe", "object", "embed", "link", "base", "set", "discard"}:
            raise MigrationError("SVG contains active content or an unsupported resource element.")
        if tag == "style":
            css("".join(element.itertext()))
        for key, value in element.attrib.items():
            attr = key.rsplit("}", 1)[-1].lower()
            if attr.startswith("on") or attr in {"base", "srcset"}:
                raise MigrationError("SVG contains active content or unsupported resource attributes.")
            if attr in {"href", "src", "poster", "background", "data"}:
                if tag == "a" and attr == "href":
                    if not re.match(r"^(?:https?://|mailto:|tel:|#)", value, re.I):
                        raise MigrationError("SVG contains an unsupported hyperlink scheme.")
                elif not resource(value):
                    raise MigrationError("SVG still references external images/resources. Copy authorised assets locally and re-export offline.")
            if attr in {"style", "fill", "stroke", "filter", "mask", "clip-path", "cursor", "marker", "marker-start", "marker-mid", "marker-end"} or re.search(r"url\s*\(", value, re.I):
                css(value)


def local_path(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise MigrationError("Invalid path in diagram manifest.")
    path = root / relative
    if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents) if p != root and p.is_relative_to(root)):
        raise MigrationError("Refused symlink or escaping diagram path.")
    return path


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise MigrationError(f"Missing or invalid {path.name}; use a complete working export.") from None


def render(root, command, image=None, check_only=False, timeout=120, stop_on_failure=False):
    root = root.resolve()
    manifest = read_json(local_path(root, "migration.json"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("drawio"), list):
        raise MigrationError("migration.json must contain the expected Draw.io inventory.")
    expected = manifest["drawio"]
    report = local_path(root, "diagram-report.json")
    previous = read_json(report) if report.exists() else []
    if not isinstance(previous, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("source"), str)
            or row.get("svg") is not None and not isinstance(row["svg"], str) for row in previous):
        raise MigrationError("Invalid diagram-report.json.")
    cache = {(r.get("source"), r.get("svg")): r for r in previous}
    results, seen = [], set()
    for item in expected:
        if not isinstance(item, dict) or not isinstance(item.get("source"), str) or not isinstance(item.get("svgs"), list):
            raise MigrationError("Invalid Draw.io entry in migration.json.")
        relative = item["source"]
        try:
            if relative in seen or not relative.endswith(".drawio"):
                raise MigrationError("Duplicate or invalid diagram source in manifest.")
            seen.add(relative)
            source = local_path(root, relative)
            source_data = source.read_bytes()
            source_hash = hashlib.sha256(source_data).hexdigest()
            pages = drawio_pages(source_data)
            destinations = svg_paths(relative, len(pages))
            if destinations != item["svgs"]:
                raise MigrationError("Expected SVG inventory differs from source tabs; review the export manifest.")
            for page, destination in zip(pages, destinations):
                record = {"source": relative, "svg": destination, "source_sha256": source_hash}
                try:
                    target = local_path(root, destination)
                    if target.exists():
                        data = target.read_bytes()
                        svg_hash = hashlib.sha256(data).hexdigest()
                        old = cache.get((relative, destination), {})
                        if old.get("source_sha256") != source_hash or old.get("svg_sha256") != svg_hash:
                            raise MigrationError("Existing SVG is stale or has no verified source association. Preserve/review it and remove only the known generated SVG before rerendering.")
                        validate_svg(data)
                        record.update(svg_sha256=svg_hash, status="verified existing; visual review pending")
                    else:
                        if check_only:
                            raise MigrationError("Expected SVG is missing; rendering was not requested.")
                        with tempfile.TemporaryDirectory(prefix="confluence-drawio-") as tmp:
                            work = Path(tmp) / "work"
                            work.mkdir(mode=0o700)
                            (work / "input.drawio").write_bytes(page)
                            # Keep client settings outside the writable container mount,
                            # including while the host client performs cleanup afterwards.
                            cmd = render_command(command, image, work, docker_config=Path(tmp) / "docker-config")
                            result = run_renderer(cmd, work, container=bool(image), timeout=timeout)
                            if result.returncode or not (work / "output.svg").is_file():
                                raise MigrationError("Offline renderer failed. Check Draw.io/Xvfb installation and sandbox support; no online fallback was attempted.")
                            data = normalize_renderer_svg((work / "output.svg").read_bytes())
                            validate_svg(data)
                            write_file(root, destination, data)
                        record.update(svg_sha256=hashlib.sha256(data).hexdigest(), status="rendered; visual review pending")
                    record["links_to_review"] = [{"page": item.get("page", "unknown"), "target": target,
                        "reason": f"Diagram reference in {destination}; not fetched or resolved"}
                        for target in diagram_links(data, "svg")]
                except ContainerCleanupError as exc:
                    record.update(status="failed", reason=str(exc))
                    record["diagnostics"] = getattr(exc, "diagnostics", {})
                    results.append(record)
                    # Keep earlier verification evidence for outputs this aborted run
                    # never reached. A retry still checks both source and SVG hashes.
                    visited = {(r.get("source"), r.get("svg")) for r in results}
                    aborted = results + [row for key, row in cache.items() if key not in visited]
                    report.write_text(json.dumps(aborted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    raise
                except (MigrationError, OSError, subprocess.TimeoutExpired) as exc:
                    record.update(status="failed", reason=str(exc))
                    if hasattr(exc, "diagnostics"):
                        record["diagnostics"] = exc.diagnostics
                results.append(record)
                if stop_on_failure and record["status"] == "failed":
                    report.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    return results
        except ContainerCleanupError:
            raise
        except (MigrationError, OSError) as exc:
            results.append({"source": relative, "status": "failed", "reason": str(exc)})
    # A damaged/incomplete inventory must not silently turn existing sources into a no-op.
    actual = {p.relative_to(root).as_posix() for p in (root / "assets").glob("*.drawio")}
    attachments = manifest.get("attachments", [])
    if not isinstance(attachments, list) or any(not isinstance(p, str) for p in attachments):
        raise MigrationError("Invalid attachment inventory in migration.json.")
    actual.update(p for p in attachments if p.endswith(".drawio"))
    for relative in sorted(actual - seen):
        results.append({"source": relative, "status": "failed", "reason": "Source is absent from migration.json; review the export inventory."})
    if not check_only:
        report.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return results


def smoke_test(command, image=None):
    """Exercise the real sandbox with synthetic content before accessing Confluence."""
    labels = ("First diagram", "Second diagram")
    tabs = [f'''<diagram name="{label}"><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="2" value="{label}" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
<mxGeometry x="20" y="20" width="160" height="60" as="geometry"/>
</mxCell></root></mxGraphModel></diagram>''' for label in labels]
    source = ("<mxfile>" + "".join(tabs) + "</mxfile>").encode()
    root = Path(tempfile.mkdtemp(prefix="drawio-smoke-"))
    print(f"Renderer smoke test: up to 30s per tab; stops at first failure. Diagnostics: {root / 'diagram-report.json'}", file=sys.stderr, flush=True)
    try:
        relative = "assets/example.drawio"
        destinations = svg_paths(relative, len(labels))
        write_file(root, relative, source)
        write_file(root, "migration.json", json.dumps({"drawio": [
            {"page": "smoke", "source": relative, "svgs": destinations}]}))
        results = render(root, command, image, timeout=30, stop_on_failure=True)
        failures = [r["reason"] for r in results if r["status"] == "failed"]
        if failures:
            raise MigrationError("Renderer smoke test failed: " + "; ".join(failures))
        for label, destination in zip(labels, destinations):
            if label not in "".join(ET.fromstring((root / destination).read_bytes()).itertext()):
                raise MigrationError("Renderer smoke test failed: expected diagram text missing.")
        if (root / relative).read_bytes() != source:
            raise MigrationError("Renderer changed the Draw.io source.")
        if any(r["status"] == "failed" for r in render(root, command, image, check_only=True)):
            raise MigrationError("Renderer smoke test failed: SVG verification failed.")
    except (MigrationError, OSError, ValueError) as exc:
        report = root / "diagram-report.json"
        records = read_json(report) if report.exists() else []
        visited = {row.get("svg") for row in records}
        records += [{"source": relative, "svg": svg, "status": "not attempted", "reason": "Smoke test stopped after failure"}
                    for svg in destinations if svg not in visited]
        if not any(row.get("status") == "failed" for row in records):
            records.append({"source": relative, "status": "failed", "reason": str(exc)})
        for row in records:
            if row.get("status") == "failed":
                details = row.setdefault("diagnostics", {})
                details.setdefault("image", image)
                details.setdefault("timeout_seconds", 30)
        report.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise MigrationError(f"{exc} Smoke-test evidence retained at {root}.") from None
    else:
        shutil.rmtree(root)


def check_pairs(root, records):
    """Check explicitly mapped repository paths; do not assume a docs/ layout."""
    root = root.resolve()
    if not isinstance(records, list) or not records:
        raise MigrationError("Pair inventory must be a nonempty JSON list.")
    seen = set()
    drawio = {}
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get("source"), str):
            raise MigrationError("Invalid diagram pair.")
        source = local_path(root, row["source"])
        data = source.read_bytes()
        if source.suffix == ".drawio":
            target = local_path(root, row.get("svg"))
            key = (source, target)
            if target.suffix != ".svg" or key in seen:
                raise MigrationError("Invalid or duplicate Draw.io/SVG pair.")
            seen.add(key)
            svg = target.read_bytes()
            if row.get("source_sha256") != hashlib.sha256(data).hexdigest() or row.get("svg_sha256") != hashlib.sha256(svg).hexdigest():
                raise MigrationError(f"Stale or unverified diagram pair: {row['source']} / {row['svg']}")
            validate_svg(svg)
            drawio.setdefault(source, set()).add(target)
        elif source.suffix == ".puml":
            target = local_path(root, row.get("document"))
            index = row.get("fence", 1)
            if type(index) is not int or index < 1:
                raise MigrationError("PlantUML fence is a positive, one-based index.")
            # Scan top-level CommonMark fences; examples nested in another fence
            # are not live PlantUML blocks. Preserve body bytes except line endings.
            blocks, fence, language, body = [], None, None, []
            for line in target.read_text(encoding="utf-8").splitlines(keepends=True):
                if fence is None:
                    match = re.fullmatch(r" {0,3}(`{3,}|~{3,})([^\n]*)\n?", line)
                    if match:
                        fence, language = match[1], match[2].strip().lower()
                        body = []
                elif re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*\n?", line):
                    if language in {"plantuml", "puml"}:
                        blocks.append("".join(body))
                    fence = None
                else:
                    body.append(line)
            if index > len(blocks) or blocks[index - 1].rstrip("\n") != data.decode("utf-8").replace("\r\n", "\n").rstrip("\n"):
                raise MigrationError(f"PlantUML source differs from fence {index}: {row['source']} / {row['document']}")
        else:
            raise MigrationError("Pair source must be .drawio or .puml.")
    for source, targets in drawio.items():
        if len(targets) != len(drawio_pages(source.read_bytes())):
            raise MigrationError(f"SVG pair inventory does not cover every tab: {source.relative_to(root)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate-svg", action="append", metavar="FILE", help="Validate SVG resources without rendering; repeat for multiple files")
    mode.add_argument("--check", action="store_true", help="Check working-export source/SVG hashes without rendering or changing reports")
    mode.add_argument("--smoke-test", action="store_true", help="Render two synthetic tabs to check the installation before exporting")
    mode.add_argument("--check-pairs", metavar="JSON", help="Check mapped Draw.io hashes and PlantUML fences in a target repository")
    parser.add_argument("--root", help="Repository root for --check-pairs")
    parser.add_argument("--work-dir", "--output", dest="work_dir",
                        help="Working export produced by confluence.py; --output is a compatibility alias")
    parser.add_argument("--command", default='["drawio"]', help='JSON argv for native renderer, e.g. ["xvfb-run", "-a", "drawio"]')
    parser.add_argument("--container-image", help="Already installed local image with xvfb-run and drawio; Docker networking is disabled")
    args = parser.parse_args(argv)
    if not (args.validate_svg or args.smoke_test or args.check_pairs or args.work_dir):
        parser.error("--work-dir is required for rendering or --check")
    if (args.validate_svg or args.smoke_test or args.check_pairs) and args.work_dir:
        parser.error("--work-dir is not used with --validate-svg, --smoke-test or --check-pairs")
    if bool(args.check_pairs) != bool(args.root):
        parser.error("--check-pairs and --root must be supplied together")
    try:
        if args.check_pairs:
            check_pairs(Path(args.root), read_json(Path(args.check_pairs)))
            print("Diagram pairs verified; no files changed. Workflow triggers and visual fidelity require separate review.")
            return 0
        if args.validate_svg:
            for filename in args.validate_svg:
                validate_svg(Path(filename).read_bytes())
            print(f"Validated {len(args.validate_svg)} SVGs; visual fidelity and source association still require review.")
            return 0
        command = json.loads(args.command)
        if not isinstance(command, list) or not command or any(not isinstance(c, str) for c in command):
            raise MigrationError("--command must be a nonempty JSON array of strings.")
        if args.smoke_test:
            smoke_test(command, args.container_image)
            print("Renderer smoke test passed: two tabs exported and verified offline.")
            return 0
        results = render(Path(args.work_dir), command, args.container_image, check_only=args.check)
    except (MigrationError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    failures = sum(r["status"] == "failed" for r in results)
    if args.check:
        for record in results:
            if record["status"] == "failed":
                print(f"{record.get('svg', record['source'])}: {record['reason']}", file=sys.stderr)
        print(f"SVG check: {len(results) - failures} verified, {failures} failed; no files changed.")
        return 2 if failures else 0
    print(f"SVGs: {len(results) - failures} available, {failures} failed. See diagram-report.json; visually review every diagram.")
    return 2 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
