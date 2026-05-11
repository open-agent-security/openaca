import json
from pathlib import Path

from tools.parsers.package_lock_json import parse


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "package-lock.json"
    p.write_text(json.dumps(data))
    return p


def test_emits_one_ref_per_transitive_package(tmp_path):
    path = _write(
        tmp_path,
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "host", "version": "1.0.0"},
                "node_modules/lodash": {"version": "4.17.20"},
                "node_modules/@scope/pkg": {"version": "2.0.0"},
            },
        },
    )
    refs = parse(path)
    by_name = {r.name: r for r in refs}
    assert set(by_name) == {"lodash", "@scope/pkg"}
    assert by_name["lodash"].ecosystem == "npm"
    assert by_name["lodash"].version == "4.17.20"
    assert by_name["lodash"].extra["transitive"] is True
    assert by_name["@scope/pkg"].version == "2.0.0"


def test_skips_dev_dependencies(tmp_path):
    path = _write(
        tmp_path,
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "host", "version": "1.0.0"},
                "node_modules/runtime-pkg": {"version": "1.0.0"},
                "node_modules/dev-pkg": {"version": "2.0.0", "dev": True},
            },
        },
    )
    refs = parse(path)
    assert {r.name for r in refs} == {"runtime-pkg"}


def test_skips_host_package(tmp_path):
    """The `""` key holds the host package — never emit a ref for it."""
    path = _write(
        tmp_path,
        {"lockfileVersion": 3, "packages": {"": {"name": "host", "version": "1.0.0"}}},
    )
    assert parse(path) == []


def test_handles_nested_node_modules(tmp_path):
    """Transitive deps of transitive deps: take the segment after the LAST
    `node_modules/` marker."""
    path = _write(
        tmp_path,
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "host", "version": "1.0.0"},
                "node_modules/parent/node_modules/child": {"version": "3.0.0"},
            },
        },
    )
    refs = parse(path)
    assert len(refs) == 1
    assert refs[0].name == "child"
    assert refs[0].version == "3.0.0"


def test_skips_entries_without_version(tmp_path):
    """A package entry without a string `version` is malformed; skip silently."""
    path = _write(
        tmp_path,
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "host"},
                "node_modules/no-version": {},
                "node_modules/null-version": {"version": None},
                "node_modules/numeric-version": {"version": 1},
            },
        },
    )
    assert parse(path) == []


def test_returns_empty_on_malformed_json(tmp_path):
    path = tmp_path / "package-lock.json"
    path.write_text("{not json")
    assert parse(path) == []


def test_returns_empty_on_non_object_top_level(tmp_path):
    path = tmp_path / "package-lock.json"
    path.write_text("[]")
    assert parse(path) == []


def test_returns_empty_when_packages_missing(tmp_path):
    """A lockfile without a `packages` map (e.g., v1 / v2 shape) returns []."""
    path = _write(tmp_path, {"lockfileVersion": 1, "dependencies": {}})
    assert parse(path) == []


def test_returns_empty_on_unreadable_file(tmp_path):
    """Directory at the lockfile path → IsADirectoryError; degrade silently."""
    p = tmp_path / "package-lock.json"
    p.mkdir()
    assert parse(p) == []


def test_skips_non_dict_entries(tmp_path):
    """A package entry that's a string/list/null should be skipped."""
    path = _write(
        tmp_path,
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "host", "version": "1.0.0"},
                "node_modules/bad-string": "1.0.0",
                "node_modules/bad-list": ["1.0.0"],
                "node_modules/good-pkg": {"version": "1.0.0"},
            },
        },
    )
    refs = parse(path)
    assert {r.name for r in refs} == {"good-pkg"}
