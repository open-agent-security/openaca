def test_parse_repo_grouped_reads_only_the_given_registry(tmp_path):
    """The `registry` keyword is what makes "the flat manifest registry
    splits per kind, reached through a surface" true — a caller can walk the
    same tree against a subset of REGISTRY without REGISTRY itself changing."""
    from tools.parsers import parse_repo_grouped
    from tools.parsers.mcp_json import parse as parse_mcp
    from tools.parsers.package_json import parse as parse_package

    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"gh": {"command": "npx", "args": ["-y", "@x/gh@1.0.0"]}}}',
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text('{"name": "x", "version": "1.0.0"}', encoding="utf-8")

    mcp_only, n_mcp = parse_repo_grouped(tmp_path, registry=((".mcp.json", parse_mcp),))
    pkg_only, n_pkg = parse_repo_grouped(tmp_path, registry=(("package.json", parse_package),))

    assert [p.name for p, _ in mcp_only] == [".mcp.json"] and n_mcp == 1
    assert [p.name for p, _ in pkg_only] == ["package.json"] and n_pkg == 1


def test_parse_repo_grouped_default_registry_is_unchanged(tmp_path):
    """No `registry` argument still walks the full global registry — every
    existing caller (the legacy `repo` command, `bom repo` before this task,
    `tools/graph_build.py`) is byte-identical."""
    from tools.parsers import REGISTRY, parse_repo_grouped

    (tmp_path / "package.json").write_text('{"name": "x", "version": "1.0.0"}', encoding="utf-8")

    grouped, n_found = parse_repo_grouped(tmp_path)
    default_grouped, default_n_found = parse_repo_grouped(tmp_path, registry=REGISTRY)

    assert grouped == default_grouped and n_found == default_n_found
