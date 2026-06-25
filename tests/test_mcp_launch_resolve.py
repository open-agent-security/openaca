import os
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.mcp_launch_resolve import resolve_mcp_launch_dir, strip_launch_version


def _mcp_ref(install_source: str, source_manifest: str = "") -> ComponentRef:
    return ComponentRef(
        component_identity="mcp-server/x",
        source_manifest=source_manifest,
        extra={"component_type": "mcp_server", "install_source": install_source},
    )


def test_strip_launch_version():
    assert strip_launch_version("@scope/name@1.0.0") == "@scope/name"
    assert strip_launch_version("@scope/name") == "@scope/name"
    assert strip_launch_version("name@latest") == "name"
    assert strip_launch_version("name") == "name"
    # PyPI == pins
    assert strip_launch_version("my-mcp==1.2.3") == "my-mcp"
    assert strip_launch_version("my-mcp[extra]==1.2.3") == "my-mcp[extra]"
    assert strip_launch_version("my-mcp") == "my-mcp"


def test_resolve_npx_name_match(tmp_path):
    idx = {("npm", "@wonderwhy-er/desktop-commander"): tmp_path}
    ref = _mcp_ref("npx -y @wonderwhy-er/desktop-commander@latest")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path


def test_resolve_uvx_name_match(tmp_path):
    idx = {("PyPI", "mcp-search-console"): tmp_path}
    ref = _mcp_ref("uvx mcp-search-console")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path


def test_resolve_external_npx_is_none(tmp_path):
    ref = _mcp_ref("npx -y @playwright/mcp@latest")  # not in index
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) is None


def test_resolve_remote_url_is_none(tmp_path):
    ref = _mcp_ref("https://mcp.example.com/mcp")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) is None


def test_resolve_local_path(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"x"}')
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "server.js").write_text("//")
    ref = _mcp_ref("node ./dist/server.js", source_manifest=str(tmp_path / ".mcp.json"))
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) == tmp_path


def test_resolve_python_module_is_none(tmp_path):
    # `python -m <module>` is not a path → Phase-1 limitation, returns None.
    ref = _mcp_ref("python -m aiteam.mcp.server", source_manifest=str(tmp_path / ".mcp.json"))
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) is None


def test_resolve_npx_full_path_launcher_name_match(tmp_path):
    # Fix 1: a full-path launcher (`/usr/local/bin/npx`) still matches.
    idx = {("npm", "@acme/dc"): tmp_path}
    ref = _mcp_ref("/usr/local/bin/npx -y @acme/dc@latest")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path


def test_resolve_plugin_json_local_path_uses_plugin_root(tmp_path):
    # Fix 2: an inline-plugin MCP local path anchors at the plugin root, not
    # `.claude-plugin/`.
    (tmp_path / "package.json").write_text('{"name":"x"}')
    server = tmp_path / "server"
    server.mkdir()
    (server / "index.js").write_text("//")
    plugin_json = tmp_path / ".claude-plugin" / "plugin.json"
    plugin_json.parent.mkdir()
    plugin_json.write_text("{}")
    ref = _mcp_ref("node ./server/index.js", source_manifest=str(plugin_json))
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) == tmp_path


def test_resolve_local_path_outside_scan_root_is_none(tmp_path):
    outside = tmp_path.parent / "outside-server.js"
    ref = _mcp_ref(f"node {outside}", source_manifest=str(tmp_path / ".mcp.json"))
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) is None


def test_resolve_command_is_local_path(tmp_path):
    # Finding 1: `{"command":"./server.js"}` with no args — the executable IS
    # the path; tokens[0] must be tried, not skipped.
    (tmp_path / "package.json").write_text('{"name":"x"}')
    (tmp_path / "server.js").write_text("//")
    ref = _mcp_ref("./server.js", source_manifest=str(tmp_path / ".mcp.json"))
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) == tmp_path


def test_resolve_uvx_pypi_pin_name_match(tmp_path):
    # Finding 2: `uvx my-mcp==1.2.3` should strip the == pin and match.
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "my-mcp"\n')
    idx = {("PyPI", "my-mcp"): tmp_path}
    ref = _mcp_ref("uvx my-mcp==1.2.3")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path


def test_resolve_name_match_outside_scan_root_is_none(tmp_path):
    # Codex Finding 1: in endpoint mode the name_index merges install_root and
    # project_root. A name hit outside the effective scan_root must not be
    # returned (it would attach install-root deps to a project-scoped MCP).
    outside = tmp_path.parent / "install_outside"
    outside.mkdir(exist_ok=True)
    idx = {("npm", "@acme/server"): outside.resolve()}  # path NOT under scan_root
    ref = _mcp_ref("npx @acme/server")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) is None


def test_resolve_node_preload_flag_skips_to_server(tmp_path):
    # Codex Finding 2: `node -r ./bootstrap.js ./packages/mcp/server.js` must
    # skip the -r preload argument and resolve to the server's manifest dir.
    (tmp_path / "package.json").write_text('{"name":"root"}')
    (tmp_path / "bootstrap.js").write_text("//")
    mcp_dir = tmp_path / "packages" / "mcp"
    mcp_dir.mkdir(parents=True)
    (mcp_dir / "server.js").write_text("//")
    (mcp_dir / "package.json").write_text('{"name":"mcp-server"}')
    ref = _mcp_ref(
        "node -r ./bootstrap.js ./packages/mcp/server.js",
        source_manifest=str(tmp_path / ".mcp.json"),
    )
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) == mcp_dir.resolve()


def test_resolve_uv_global_flag_dispatches_as_uvx(tmp_path):
    # Codex Finding 3: `uv --offline tool run my-mcp` must dispatch as uvx and
    # match by name, not fall back to Strategy 2 or return None.
    idx = {("PyPI", "my-mcp"): tmp_path.resolve()}
    ref = _mcp_ref("uv --offline tool run my-mcp")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path.resolve()


def test_resolve_name_match_relative_scan_root(tmp_path):
    # P1: when scan_root is a relative path (e.g. `openaca scan repo --target .`),
    # the name_index stores resolved absolute dirs. _within() must compare resolved
    # paths; without scan_root.resolve() the containment check always fails and valid
    # self-launch matches are silently dropped.
    idx = {("npm", "@acme/pkg"): tmp_path.resolve()}
    ref = _mcp_ref("npx @acme/pkg")
    rel_scan_root = Path(os.path.relpath(tmp_path))
    resolved = resolve_mcp_launch_dir(ref, scan_root=rel_scan_root, name_index=idx)
    assert resolved == tmp_path.resolve()


def test_resolve_absolute_launcher_continues_to_server_path(tmp_path):
    # Finding 2: `{"command":"/usr/bin/env","args":["node","./dist/server.js"]}`
    # The absolute launcher /usr/bin/env exists but is outside scan_root.
    # Previously the resolver returned None immediately after _nearest_dep_manifest_dir
    # returned None for /usr/bin/env; the ./dist/server.js arg was never inspected.
    (tmp_path / "package.json").write_text('{"name":"x"}')
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "server.js").write_text("//")
    # /usr/bin/env is a universally available absolute path on POSIX; it exists
    # on the runner but is outside scan_root (tmp_path), so it triggers the bug.
    abs_launcher = "/usr/bin/env"
    ref = _mcp_ref(
        f"{abs_launcher} node ./dist/server.js",
        source_manifest=str(tmp_path / ".mcp.json"),
    )
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) == tmp_path


def test_resolve_cross_ecosystem_name_not_matched(tmp_path):
    # P2 (ecosystem key): `npx foo` must not match a PyPI `pyproject.toml` named
    # `foo`, and `uvx foo` must not match an npm `package.json` named `foo`.
    # Both scenarios previously resolved incorrectly because the index was keyed
    # by bare name with no ecosystem discrimination.
    npm_dir = tmp_path / "npm-pkg"
    npm_dir.mkdir()
    (npm_dir / "package.json").write_text('{"name": "shared-name"}')
    pypi_dir = tmp_path / "pypi-pkg"
    pypi_dir.mkdir()
    (pypi_dir / "pyproject.toml").write_text('[project]\nname = "shared-name"\n')

    # Index built by build_manifest_name_index (keyed by ecosystem).
    from tools.graph_build import build_manifest_name_index

    idx = build_manifest_name_index(tmp_path)

    # npx shared-name → npm ecosystem → must resolve to npm_dir, NOT pypi_dir.
    ref_npx = _mcp_ref("npx shared-name")
    assert resolve_mcp_launch_dir(ref_npx, scan_root=tmp_path, name_index=idx) == npm_dir.resolve()

    # uvx shared-name → PyPI ecosystem → must resolve to pypi_dir, NOT npm_dir.
    ref_uvx = _mcp_ref("uvx shared-name")
    assert (
        resolve_mcp_launch_dir(ref_uvx, scan_root=tmp_path, name_index=idx) == pypi_dir.resolve()
    )
