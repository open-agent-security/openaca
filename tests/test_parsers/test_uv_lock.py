from pathlib import Path

from tools.parsers.uv_lock import parse


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "uv.lock"
    p.write_text(content)
    return p


def test_emits_one_ref_per_package(tmp_path):
    path = _write(
        tmp_path,
        """\
version = 1

[[package]]
name = "requests"
version = "2.31.0"

[[package]]
name = "urllib3"
version = "2.0.4"
""",
    )
    refs = parse(path)
    by_name = {r.name: r for r in refs}
    assert set(by_name) == {"requests", "urllib3"}
    assert by_name["requests"].ecosystem == "PyPI"
    assert by_name["requests"].version == "2.31.0"
    assert by_name["requests"].extra["transitive"] is True


def test_returns_empty_on_malformed_toml(tmp_path):
    path = _write(tmp_path, "this is not = valid [[toml")
    assert parse(path) == []


def test_returns_empty_when_package_missing(tmp_path):
    path = _write(tmp_path, "version = 1\n")
    assert parse(path) == []


def test_skips_entries_without_required_fields(tmp_path):
    path = _write(
        tmp_path,
        """\
version = 1

[[package]]
name = "valid-pkg"
version = "1.0.0"

[[package]]
name = "no-version"

[[package]]
version = "no-name"
""",
    )
    refs = parse(path)
    assert {r.name for r in refs} == {"valid-pkg"}


def test_returns_empty_on_unreadable_file(tmp_path):
    p = tmp_path / "uv.lock"
    p.mkdir()
    assert parse(p) == []


def test_source_locator_preserves_original_index(tmp_path):
    """When earlier [[package]] entries are malformed, the source_locator
    for valid entries should still reference their original position."""
    path = _write(
        tmp_path,
        """\
version = 1

[[package]]
name = "no-version-first"

[[package]]
name = "second-valid"
version = "1.0.0"
""",
    )
    refs = parse(path)
    assert len(refs) == 1
    assert refs[0].source_locator == "$.package[1]"
