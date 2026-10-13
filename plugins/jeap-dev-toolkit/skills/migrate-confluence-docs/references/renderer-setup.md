# Set up a local Draw.io renderer

Do this before a migration with diagrams. Export and review need only Python; SVG generation additionally
needs either the native tools or a local container image. No approved image is bundled or implied.
Choose the team's permitted package source and versions; installing software is separate from reading
the selected Confluence pages. Do not put source documents or credentials into a build context.

## Use an existing installation

On Linux the native mode needs system-installed Draw.io Desktop, `bwrap`, `xvfb-run` and `xauth`, with
namespace support. Merely finding bubblewrap is insufficient. Check the complete runtime:

```bash
python3 /path/to/skill/scripts/render_drawio.py --smoke-test
```

For an existing local image containing `drawio` and `xvfb-run` on PATH:

```bash
python3 /path/to/skill/scripts/render_drawio.py --smoke-test --container-image local-drawio:VERSION
```

Both tests render two synthetic tabs under the same isolation used for the migration. They need no PAT,
Confluence data or working export. A pass checks exports, expected labels and source/output hashes;
it does not validate the visual fidelity of the actual architecture diagrams.
The smoke test allows 30 seconds per tab and stops at the first failure; it does not retry. Failure retains
the synthetic sources, expected outputs and `diagram-report.json` at the printed temporary path. Inspect
its captured output and container diagnostics before changing the runtime; do not repeat an unchanged
failed setup or proceed to the full export's diagrams. Ordinary rendering retains its 120-second limit.

## Build an image locally

The version-controlled [Dockerfile](../assets/renderer.Dockerfile) provides a build recipe, not an
organisationally approved image. It targets a Debian/Ubuntu base with apt and the matching Draw.io `.deb`
architecture. Obtain a specific package from the team's repository or the
[Draw.io Desktop releases](https://github.com/jgraph/drawio-desktop/releases), verify its SHA-256 against a
trusted release record, and choose a compatible base pinned by digest. Record the package version/hash,
base digest and resulting local image ID. Dependency repositories follow the selected base; apt package
versions are not pinned, so this recipe alone does not promise a byte-reproducible image.

From a shell with `DRAWIO_DEB` (package path), `DRAWIO_SHA256` (trusted digest), `DRAWIO_BASE_IMAGE`
(image@sha256:digest) and `DRAWIO_LOCAL_IMAGE` (local versioned tag) explicitly configured:

```bash
renderer_build_dir=$(mktemp -d)
cp /path/to/skill/assets/renderer.Dockerfile "$renderer_build_dir/Dockerfile"
cp "$DRAWIO_DEB" "$renderer_build_dir/drawio.deb"
docker build --build-arg BASE_IMAGE="$DRAWIO_BASE_IMAGE" \
  --build-arg DRAWIO_SHA256="$DRAWIO_SHA256" \
  --tag "$DRAWIO_LOCAL_IMAGE" "$renderer_build_dir"
python3 /path/to/skill/scripts/render_drawio.py --smoke-test --container-image "$DRAWIO_LOCAL_IMAGE"
```

The build context contains only the recipe and package. Setup requires the base/package repositories to
be reachable; rendering afterwards uses `--pull never --network none`. Never pass the Confluence PAT,
project folders or personal Docker settings into the build. If DNS/proxy/mirror access blocks setup,
report that concrete prerequisite and use the team's supported network/package configuration or transfer
an already built approved image. Do not retry with broader renderer privileges or online rendering.
Delete only this run's build directory after diagnosing the result. Do not claim renderer availability
until the smoke test passes. The recipe still requires a real build/smoke test in the intended environment.
