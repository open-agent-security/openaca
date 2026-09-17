import json
from pathlib import Path

import pytest

from tools.parsers.pi_trust import project_trust, read_trust_store


def test_saved_allow_wins_over_default_never():
    assert project_trust({"/repo": True}, "never", Path("/repo")) == "trusted"


def test_saved_deny_wins_over_default_always():
    assert project_trust({"/repo": False}, "always", Path("/repo")) == "untrusted"


def test_nearest_ancestor_boolean_wins_at_any_depth():
    trust = {"/repo": True, "/repo/team": False}

    assert project_trust(trust, "always", Path("/repo/team/project/src")) == "untrusted"


def test_null_entry_falls_through_to_an_ancestor_boolean():
    trust = {"/repo": True, "/repo/team/project": None}

    assert project_trust(trust, "never", Path("/repo/team/project")) == "trusted"


@pytest.mark.parametrize(
    ("default", "expected"),
    [
        ("always", "trusted"),
        ("never", "untrusted"),
        ("ask", "unresolved"),
        ("unexpected", "unresolved"),
        (None, "unresolved"),
    ],
)
def test_default_applies_without_a_saved_decision(default, expected):
    assert project_trust({}, default, Path("/repo")) == expected


def test_project_path_is_canonicalized_before_lookup(tmp_path):
    real_project = tmp_path / "real" / "project"
    real_project.mkdir(parents=True)
    link = tmp_path / "linked-project"
    link.symlink_to(real_project, target_is_directory=True)

    assert project_trust({str(real_project): True}, "never", link) == "trusted"


def test_failed_whole_path_canonicalization_keeps_the_lexical_path(tmp_path):
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real_parent, target_is_directory=True)

    assert project_trust({str(real_parent): True}, "never", link / "missing") == "untrusted"


@pytest.mark.parametrize(
    "trust",
    [
        {"/repo": "trusted"},
        {"/repo": 1},
        {"/other": True, "/bad": []},
        [],
    ],
)
def test_malformed_store_data_never_enables_default_always(trust):
    assert project_trust(trust, "always", Path("/repo")) == "unresolved"


def test_read_trust_store_distinguishes_missing_from_valid_empty(tmp_path):
    missing = read_trust_store(tmp_path / "missing.json")
    path = tmp_path / "trust.json"
    path.write_text("{}")
    empty = read_trust_store(path)

    assert (missing.status, missing.data, missing.error) == ("missing", {}, None)
    assert (empty.status, empty.data, empty.error) == ("valid", {}, None)


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        json.dumps({"/repo": "trusted"}),
        json.dumps({"/repo": 1}),
    ],
)
def test_read_trust_store_reports_invalid_without_returning_empty_data(tmp_path, raw):
    path = tmp_path / "trust.json"
    path.write_text(raw)

    result = read_trust_store(path)

    assert result.status == "invalid"
    assert result.data is None
    assert result.error


def test_read_trust_store_accepts_boolean_and_null_values(tmp_path):
    path = tmp_path / "trust.json"
    path.write_text(json.dumps({"/allow": True, "/deny": False, "/unset": None}))

    result = read_trust_store(path)

    assert result.status == "valid"
    assert result.data == {"/allow": True, "/deny": False, "/unset": None}
    assert result.error is None
