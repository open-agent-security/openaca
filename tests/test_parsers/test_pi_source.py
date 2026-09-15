from pathlib import Path

import pytest

from tools.parsers.pi_source import parse_pi_source

SHA = "a" * 40


@pytest.mark.parametrize(
    ("raw", "kind", "identity", "ref", "pinned", "purl"),
    [
        ("npm:pkg@1.2.3", "npm", "npm:pkg", "1.2.3", True, "pkg:npm/pkg@1.2.3"),
        (
            "npm:@scope/pkg@1.2.3",
            "npm",
            "npm:@scope/pkg",
            "1.2.3",
            True,
            "pkg:npm/%40scope/pkg@1.2.3",
        ),
        ("pkg", "npm", "npm:pkg", None, False, None),
        ("@scope/pkg", "npm", "npm:@scope/pkg", None, False, None),
        ("pkg@^1.0.0", "npm", "npm:pkg", "^1.0.0", False, None),
        ("npm:pkg@next", "npm", "npm:pkg", "next", False, None),
        (
            f"git:github.com/user/repo@{SHA}",
            "git",
            "git:github.com/user/repo",
            SHA,
            True,
            None,
        ),
        (
            "git:github.com/user/repo@v1",
            "git",
            "git:github.com/user/repo",
            "v1",
            False,
            None,
        ),
        (
            "https://github.com/user/repo.git",
            "git",
            "git:github.com/user/repo",
            None,
            False,
            None,
        ),
        (
            f"ssh://git@github.com/user/repo.git@{SHA}",
            "git",
            "git:github.com/user/repo",
            SHA,
            True,
            None,
        ),
        (
            "git:git@code.example.com:team/repo.git@main",
            "git",
            "git:code.example.com/team/repo",
            "main",
            False,
            None,
        ),
        (
            "git:github:user/repo",
            "git",
            "git:github.com/user/repo",
            None,
            False,
            None,
        ),
        (
            "git:gitlab:user/repo@main",
            "git",
            "git:gitlab.com/user/repo",
            "main",
            False,
            None,
        ),
        (
            "git:bitbucket:user/repo",
            "git",
            "git:bitbucket.org/user/repo",
            None,
            False,
            None,
        ),
    ],
)
def test_parse_pi_source(raw, kind, identity, ref, pinned, purl):
    src = parse_pi_source(raw)

    assert src is not None
    assert (src.kind, src.identity, src.ref, src.pinned, src.purl) == (
        kind,
        identity,
        ref,
        pinned,
        purl,
    )


def test_local_identity_resolves_relative_to_settings_directory(tmp_path):
    settings_dir = tmp_path / "project" / ".pi"

    src = parse_pi_source("../extensions/review.ts", base_dir=settings_dir)

    assert src is not None
    assert src.kind == "local"
    assert src.identity == f"local:{tmp_path / 'project' / 'extensions' / 'review.ts'}"
    assert src.ref is None
    assert src.purl is None
    assert src.pinned is False


def test_local_identity_defaults_to_current_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    src = parse_pi_source("./local/path")

    assert src is not None
    assert src.identity == f"local:{tmp_path / 'local' / 'path'}"


def test_absolute_local_identity_is_preserved(tmp_path):
    path = tmp_path / "package"

    src = parse_pi_source(str(path), base_dir=Path("/ignored"))

    assert src is not None
    assert src.identity == f"local:{path}"


def test_unparseable_source_returns_none():
    assert parse_pi_source("") is None
    assert parse_pi_source("   ") is None
    assert parse_pi_source("npm:../../outside") is None
    assert parse_pi_source("git:github.com/team/../outside") is None
    assert parse_pi_source("git:exa%2fmple.com/team/repo") is None
    assert parse_pi_source("https://exa%2fmple.com/team/repo") is None
