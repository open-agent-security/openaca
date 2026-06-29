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
    # PyPI pins / extras / PEP 440 operators
    assert strip_launch_version("my-mcp==1.2.3") == "my-mcp"
    assert strip_launch_version("my-mcp[extra]==1.2.3") == "my-mcp"
    assert strip_launch_version("my-mcp[extra]>=1,<2") == "my-mcp"
    assert strip_launch_version("my-mcp>=1") == "my-mcp"
    assert strip_launch_version("my-mcp[server]") == "my-mcp"


# ── Name-match (the one resolution path) ──────────────────────────────────────


def test_resolve_npx_name_match(tmp_path):
    idx = {("npm", "@wonderwhy-er/desktop-commander"): tmp_path}
    ref = _mcp_ref("npx -y @wonderwhy-er/desktop-commander@latest")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path


def test_resolve_uvx_name_match(tmp_path):
    idx = {("PyPI", "mcp-search-console"): tmp_path}
    ref = _mcp_ref("uvx mcp-search-console")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path


def test_resolve_uv_tool_run_name_match(tmp_path):
    # `uv tool run <pkg>` dispatches as uvx.
    idx = {("PyPI", "my-mcp"): tmp_path}
    ref = _mcp_ref("uv tool run my-mcp")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path


def test_resolve_uvx_pypi_pin_name_match(tmp_path):
    # `uvx my-mcp==1.2.3` → strip the pin → match.
    idx = {("PyPI", "my-mcp"): tmp_path}
    ref = _mcp_ref("uvx my-mcp==1.2.3")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path


def test_resolve_uvx_name_match_normalizes_equivalent_names(tmp_path):
    # PyPI names compare normalized (case + runs of -/_/. collapsed).
    idx = {("PyPI", "my-mcp"): tmp_path}
    ref = _mcp_ref("uvx My_MCP")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path


def test_resolve_npx_full_path_launcher_name_match(tmp_path):
    # A full-path launcher (`/usr/local/bin/npx`) is normalized to its basename.
    idx = {("npm", "@acme/dc"): tmp_path}
    ref = _mcp_ref("/usr/local/bin/npx -y @acme/dc@latest")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path


def test_resolve_cross_ecosystem_name_not_matched(tmp_path):
    # `npx foo` must not match a PyPI `foo`, and `uvx foo` must not match an npm `foo`.
    idx = {("PyPI", "shared-name"): tmp_path, ("npm", "other"): tmp_path}
    npx_ref = _mcp_ref("npx shared-name")
    uvx_ref = _mcp_ref("uvx other")
    assert resolve_mcp_launch_dir(npx_ref, scan_root=tmp_path, name_index=idx) is None
    assert resolve_mcp_launch_dir(uvx_ref, scan_root=tmp_path, name_index=idx) is None


def test_resolve_name_match_outside_scan_root_is_none(tmp_path):
    # A name hit OUTSIDE the effective scan_root must not be returned (endpoint
    # mode merges install_root + project_root entries into one index).
    outside = (tmp_path.parent / "install_outside").resolve()
    outside.mkdir(exist_ok=True)
    idx = {("npm", "@acme/server"): outside}
    ref = _mcp_ref("npx @acme/server")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) is None


def test_resolve_name_match_relative_scan_root(tmp_path):
    # `openaca scan repo --target .` passes a relative scan_root; containment
    # must compare resolved absolute paths.
    import os

    idx = {("npm", "@acme/pkg"): tmp_path.resolve()}
    ref = _mcp_ref("npx @acme/pkg")
    rel = Path(os.path.relpath(tmp_path))
    assert resolve_mcp_launch_dir(ref, scan_root=rel, name_index=idx) == tmp_path.resolve()


def test_resolve_bunx_name_match(tmp_path):
    idx = {("npm", "@acme/dc"): tmp_path}
    ref = _mcp_ref("bunx @acme/dc@latest")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path


def test_resolve_bunx_package_flag_name_match(tmp_path):
    # `bunx --package <pkg> <bin>`: the --package flag names the package, not the bin.
    idx = {("npm", "@acme/dc"): tmp_path}
    ref = _mcp_ref("bunx --package @acme/dc dc-server")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path


def test_resolve_quoted_launcher_path_name_match(tmp_path):
    # A quoted launcher path with spaces still resolves (shlex tokenization).
    idx = {("npm", "@acme/dc"): tmp_path}
    ref = _mcp_ref('"/Program Files/nodejs/npx" -y @acme/dc@latest')
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path


# ── Phase-1 boundary: everything else declines to None ────────────────────────
# (Local-path / module / env-wrapped / exotic launchers are NOT parsed in Phase 1;
#  their deps are Phase 2, on-disk cache resolution. Declining beats guessing —
#  a wrong guess attaches unrelated repo deps to the MCP as a false advisory.)


def test_resolve_remote_url_is_none(tmp_path):
    ref = _mcp_ref("https://mcp.example.com/mcp")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) is None


def test_resolve_external_npx_is_none(tmp_path):
    # npx of a package not present locally → external → None (Phase 2 closes it).
    ref = _mcp_ref("npx -y @playwright/mcp@latest")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) is None


def test_resolve_local_path_node_is_none(tmp_path):
    # `node ./dist/server.js` is NOT resolved in Phase 1, even if the path exists.
    (tmp_path / "package.json").write_text('{"name":"x"}')
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "server.js").write_text("//")
    ref = _mcp_ref("node ./dist/server.js", source_manifest=str(tmp_path / ".mcp.json"))
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) is None


def test_resolve_python_module_is_none(tmp_path):
    ref = _mcp_ref("python -m aiteam.mcp.server", source_manifest=str(tmp_path / ".mcp.json"))
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) is None


def test_resolve_env_wrapped_is_none(tmp_path):
    # `/usr/bin/env node ./server.js` — env-wrapped launchers are not parsed.
    (tmp_path / "package.json").write_text('{"name":"x"}')
    (tmp_path / "server.js").write_text("//")
    ref = _mcp_ref("/usr/bin/env node ./server.js", source_manifest=str(tmp_path / ".mcp.json"))
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) is None


def test_resolve_node_eval_is_none(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"x"}')
    ref = _mcp_ref("node -e 'require(\"./server\")()'", source_manifest=str(tmp_path / ".mcp.json"))
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) is None
