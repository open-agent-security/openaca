from pathlib import Path

from tools.parsers.pyproject_toml import parse

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"
SAMPLE = REPOS / "sample-pyproject" / "pyproject.toml"


def _refs():
    return parse(SAMPLE)


def test_parses_runtime_dependencies():
    refs = _refs()
    by_name = {r.name: r for r in refs if r.source_locator == "project.dependencies"}
    assert "aws-mcp-server" in by_name
    assert by_name["aws-mcp-server"].version == "0.3.0"
    assert by_name["aws-mcp-server"].purl == "pkg:pypi/aws-mcp-server@0.3.0"


def test_unpinned_runtime_dep_emits_no_version():
    refs = _refs()
    weather = next(r for r in refs if r.name == "weather-mcp")
    assert weather.version is None
    assert weather.purl == "pkg:pypi/weather-mcp"


def test_range_spec_emits_no_concrete_version():
    """`anthropic>=0.40.0` is a range, not a pin — version stays unset."""
    refs = _refs()
    anthropic = next(r for r in refs if r.name == "anthropic")
    assert anthropic.version is None


def test_pep508_with_extras_and_marker_uses_canonical_name():
    """`langchain[community]>=0.3,<0.4; python_version>='3.11'` parses cleanly;
    we record the canonical name without extras and leave version unset."""
    refs = _refs()
    langchain = next(r for r in refs if r.name == "langchain")
    assert langchain.version is None


def test_optional_dependencies_emit_separate_locator():
    refs = _refs()
    dev = [r for r in refs if r.source_locator == "project.optional-dependencies.dev"]
    names = {r.name for r in dev}
    assert "pytest" in names
    assert "ruff" in names
    ruff = next(r for r in dev if r.name == "ruff")
    assert ruff.version == "0.4.5"


def test_dependency_groups_emit_separate_locator():
    refs = _refs()
    agents = [r for r in refs if r.source_locator == "dependency-groups.agents"]
    names = {r.name for r in agents}
    assert "mcp-remote" in names
    assert next(r for r in agents if r.name == "mcp-remote").version == "0.4.0"


def test_invalid_pep508_specs_are_skipped():
    """`@cyanheads/git-mcp-server` is an npm name, not a valid PEP 508 spec.
    The parser must skip it, not crash, and not emit it as a PyPI ref."""
    refs = _refs()
    names = {r.name for r in refs}
    assert "@cyanheads/git-mcp-server" not in names


def test_wildcard_pin_emits_no_version(tmp_path):
    """`foo==1.*` is a PEP 440 prefix match, not an exact pin — version must be unset."""
    cfg = tmp_path / "pyproject.toml"
    cfg.write_text('[project]\nname = "x"\nversion = "0"\ndependencies = ["foo==1.*"]\n')
    refs = parse(cfg)
    assert len(refs) == 1
    assert refs[0].name == "foo"
    assert refs[0].version is None
    assert refs[0].purl == "pkg:pypi/foo"


def test_missing_project_table_returns_empty(tmp_path):
    cfg = tmp_path / "pyproject.toml"
    cfg.write_text('[build-system]\nrequires = ["hatchling"]\n')
    assert parse(cfg) == []


def test_non_string_dep_entries_are_skipped(tmp_path):
    """A misauthored dependency like `[ {foo = ...} ]` must not crash."""
    cfg = tmp_path / "pyproject.toml"
    cfg.write_text(
        '[project]\nname = "x"\nversion = "0"\n'
        'dependencies = ["good==1.0"]\n'
        "[project.optional-dependencies]\n"
        'dev = ["valid==2.0"]\n'
    )
    refs = parse(cfg)
    names = {r.name for r in refs}
    assert "good" in names
    assert "valid" in names


def test_url_based_deps_are_skipped(tmp_path):
    """VCS and file URL requirements are not PyPI packages — must not emit a purl."""
    cfg = tmp_path / "pyproject.toml"
    cfg.write_text(
        '[project]\nname = "x"\nversion = "0"\ndependencies = [\n'
        '    "good==1.0",\n'
        '    "vcs-dep @ git+https://github.com/example/vcs-dep.git",\n'
        '    "local-dep @ file:///path/to/local-dep",\n'
        "]\n"
    )
    refs = parse(cfg)
    names = {r.name for r in refs}
    assert "good" in names
    assert "vcs-dep" not in names
    assert "local-dep" not in names


def test_canonical_name_normalization(tmp_path):
    """PyPI names are case/separator-insensitive; we must emit PEP 503 canonical form.

    `AWS_MCP_Server` and `AWS-MCP-SERVER` both canonicalize to `aws-mcp-server`,
    which is the form advisories store — exact string equality in the matcher
    requires this to avoid false negatives.
    """
    cfg = tmp_path / "pyproject.toml"
    cfg.write_text(
        '[project]\nname = "x"\nversion = "0"\n'
        'dependencies = ["AWS_MCP_Server==0.3.0", "AWS-MCP-SERVER>=1.0"]\n'
    )
    refs = parse(cfg)
    names = [r.name for r in refs]
    assert names == ["aws-mcp-server", "aws-mcp-server"]
    assert refs[0].version == "0.3.0"
    assert refs[1].version is None


def test_empty_file_is_safe(tmp_path):
    cfg = tmp_path / "pyproject.toml"
    cfg.write_text("")
    assert parse(cfg) == []
