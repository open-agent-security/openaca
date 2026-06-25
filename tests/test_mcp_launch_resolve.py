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
    idx = {"@wonderwhy-er/desktop-commander": tmp_path}
    ref = _mcp_ref("npx -y @wonderwhy-er/desktop-commander@latest")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path


def test_resolve_uvx_name_match(tmp_path):
    idx = {"mcp-search-console": tmp_path}
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
    idx = {"@acme/dc": tmp_path}
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
    idx = {"my-mcp": tmp_path}
    ref = _mcp_ref("uvx my-mcp==1.2.3")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path
