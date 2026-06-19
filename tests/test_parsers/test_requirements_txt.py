from pathlib import Path

from tools.parsers.requirements_txt import parse

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"
SAMPLE = REPOS / "sample-requirements" / "requirements.txt"


def _refs():
    return parse(SAMPLE)


def test_pinned_dep_emits_version_and_purl():
    refs = _refs()
    pinned = next(r for r in refs if r.name == "aws-mcp-server" and r.version == "0.3.0")
    assert pinned.purl == "pkg:pypi/aws-mcp-server@0.3.0"


def test_range_spec_emits_no_concrete_version():
    refs = _refs()
    requests = next(r for r in refs if r.name == "requests")
    assert requests.version is None
    assert requests.purl == "pkg:pypi/requests"


def test_pep508_with_extras_and_marker_uses_canonical_name():
    refs = _refs()
    langchain = next(r for r in refs if r.name == "langchain")
    assert langchain.version is None


def test_canonical_name_normalization():
    """PEP 503: AWS_MCP_Server and aws-mcp-server both canonicalize to aws-mcp-server."""
    refs = _refs()
    names = [r.name for r in refs]
    assert names.count("aws-mcp-server") == 2


def test_url_based_deps_are_skipped():
    refs = _refs()
    names = {r.name for r in refs}
    assert "vcs-dep" not in names


def test_option_lines_are_skipped():
    """Lines starting with '-' (flags like -r, -c, -i) must not be parsed as deps."""
    refs = _refs()
    assert all(r.name is None or not r.name.startswith("-") for r in refs)


def test_source_locator_is_line_number():
    refs = _refs()
    locators = {r.source_locator for r in refs}
    assert all(loc.startswith("line:") for loc in locators)


def test_empty_file_is_safe(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("")
    assert parse(f) == []


def test_comment_only_file_is_safe(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("# just a comment\n# another comment\n")
    assert parse(f) == []


def test_invalid_spec_is_skipped(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("valid-pkg==1.0\n!!!invalid!!!\nanother-pkg==2.0\n")
    refs = parse(f)
    names = {r.name for r in refs}
    assert "valid-pkg" in names
    assert "another-pkg" in names
    assert len(refs) == 2


def test_wildcard_pin_emits_no_version(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("foo==1.*\n")
    refs = parse(f)
    assert len(refs) == 1
    assert refs[0].name == "foo"
    assert refs[0].version is None


def test_inline_comment_is_stripped():
    """pip-compile annotated lines (e.g. '# via ...') must still be parsed."""
    refs = _refs()
    boto3 = next((r for r in refs if r.name == "boto3"), None)
    assert boto3 is not None, "boto3 line with inline comment was silently dropped"
    assert boto3.version == "1.26.0"


def test_hash_locked_dep_is_parsed():
    """Hash-locked lines (--hash=sha256:...) must be parsed; hashes stripped."""
    refs = _refs()
    urllib3 = next((r for r in refs if r.name == "urllib3"), None)
    assert urllib3 is not None, "urllib3 hash-locked line was silently dropped"
    assert urllib3.version == "1.26.5"


def test_inline_comment_strip_via_tmp(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("urllib3==1.26.5  # via botocore\n")
    refs = parse(f)
    assert len(refs) == 1
    assert refs[0].name == "urllib3"
    assert refs[0].version == "1.26.5"


def test_hash_locked_strip_via_tmp(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("urllib3==1.26.5 --hash=sha256:abc123 --hash=sha256:def456\n")
    refs = parse(f)
    assert len(refs) == 1
    assert refs[0].name == "urllib3"
    assert refs[0].version == "1.26.5"
