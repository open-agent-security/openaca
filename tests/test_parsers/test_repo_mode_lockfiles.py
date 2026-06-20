"""Plan 009 Task 3: lockfile patterns fire in repo mode via parse_repo.

Refs emitted from host-repo lockfiles have no plugin parent — the
host repo declares these deps directly, not via a plugin.
"""

from pathlib import Path

from tools.parsers import parse_repo

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_repo_mode_emits_npm_lockfile_refs():
    refs = parse_repo(REPOS / "sample-lockfile-npm")
    npm_refs = [r for r in refs if r.ecosystem == "npm" and r.extra.get("transitive") is True]
    assert len(npm_refs) == 1
    assert npm_refs[0].name == "lodash"
    assert npm_refs[0].version == "4.17.20"


def test_repo_mode_emits_uv_lock_refs():
    refs = parse_repo(REPOS / "sample-lockfile-uv")
    pypi_refs = [r for r in refs if r.ecosystem == "PyPI" and r.extra.get("transitive") is True]
    assert len(pypi_refs) == 1
    assert pypi_refs[0].name == "requests"
