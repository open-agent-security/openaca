"""Tests for the SKILL.md parser (generic skill component type).

Per the canonical Agent Skills spec at agentskills.io/specification: six
top-level frontmatter fields (`name`, `description`, `license`,
`compatibility`, `metadata`, `allowed-tools`). No top-level `version` —
versioning is convention via `metadata.version`.
"""

from pathlib import Path

from tools.parsers.claude_skill import parse


def _write_skill(tmp_path: Path, skill_name: str, frontmatter: str, body: str = "") -> Path:
    """Write a fixture SKILL.md under <tmp_path>/<skill_name>/SKILL.md."""
    skill_dir = tmp_path / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    text = f"---\n{frontmatter}\n---\n{body}".rstrip() + "\n"
    path.write_text(text)
    return path


def test_parse_emits_ref_with_name_and_metadata_version(tmp_path):
    path = _write_skill(
        tmp_path,
        "bootstrap-project",
        "name: bootstrap-project\ndescription: scaffolds a new repo\nmetadata:\n  version: 1.2.3\n",
    )
    refs = parse(path)
    assert len(refs) == 1
    ref = refs[0]
    assert ref.ecosystem is None
    assert ref.extra["component_type"] == "skill"
    assert ref.name == "bootstrap-project"
    assert ref.version == "1.2.3"
    assert ref.component_identity == "skill/bootstrap-project@1.2.3"
    assert ref.source_manifest == str(path)
    assert ref.source_locator == "$.frontmatter"
    assert ref.attributed_to is None


def test_parse_emits_ref_without_version_when_metadata_absent(tmp_path):
    path = _write_skill(tmp_path, "linter", "name: linter\ndescription: runs lint\n")
    refs = parse(path)
    assert len(refs) == 1
    assert refs[0].version is None
    assert refs[0].component_identity == "skill/linter"


def test_parse_falls_back_to_directory_name_when_name_missing(tmp_path):
    """If frontmatter has no `name`, use the parent directory name."""
    path = _write_skill(tmp_path, "fallback-skill", "description: no name field here\n")
    refs = parse(path)
    assert len(refs) == 1
    assert refs[0].name == "fallback-skill"
    assert refs[0].component_identity == "skill/fallback-skill"


def test_parse_skips_when_no_frontmatter(tmp_path):
    """SKILL.md without `---` frontmatter is not a valid skill manifest."""
    skill_dir = tmp_path / "no-frontmatter"
    skill_dir.mkdir()
    path = skill_dir / "SKILL.md"
    path.write_text("just some markdown content\nno frontmatter\n")
    assert parse(path) == []


def test_parse_skips_when_frontmatter_malformed_yaml(tmp_path):
    skill_dir = tmp_path / "bad-yaml"
    skill_dir.mkdir()
    path = skill_dir / "SKILL.md"
    # `]` without matching `[` makes the YAML invalid.
    path.write_text("---\nname: x\nfield: ]\n---\nbody\n")
    assert parse(path) == []


def test_parse_skips_when_frontmatter_is_not_a_mapping(tmp_path):
    """Top-level array or scalar — not a valid skill manifest."""
    skill_dir = tmp_path / "list-frontmatter"
    skill_dir.mkdir()
    path = skill_dir / "SKILL.md"
    path.write_text("---\n- one\n- two\n---\nbody\n")
    assert parse(path) == []


def test_parse_skips_when_frontmatter_unterminated(tmp_path):
    """Opening `---` but no closing `---`."""
    skill_dir = tmp_path / "unterminated"
    skill_dir.mkdir()
    path = skill_dir / "SKILL.md"
    path.write_text("---\nname: x\ndescription: y\n")
    assert parse(path) == []


def test_parse_emits_when_name_differs_from_directory(tmp_path):
    """Spec says name `must match parent dir` but we don't enforce it —
    inventory scanners should emit what's declared, not what's correct.
    Mismatch is a finding-shaped problem, not a parser-level reject."""
    path = _write_skill(
        tmp_path,
        "dir-name",
        "name: declared-name\ndescription: mismatched\n",
    )
    refs = parse(path)
    assert len(refs) == 1
    assert refs[0].name == "declared-name"  # frontmatter wins


def test_parse_propagates_attributed_to(tmp_path):
    """Bundled skills are attributed to their parent plugin."""
    path = _write_skill(
        tmp_path,
        "bundled",
        "name: bundled\ndescription: ships inside a plugin\n",
    )
    refs = parse(path, attributed_to="plugin/superpowers@5.1.0")
    assert len(refs) == 1
    assert refs[0].attributed_to == "plugin/superpowers@5.1.0"


def test_parse_skips_non_string_metadata_version(tmp_path):
    """metadata.version should be a string — anything else is dropped (no version)."""
    path = _write_skill(
        tmp_path,
        "weird-version",
        "name: weird-version\ndescription: x\nmetadata:\n  version: 1.0\n",
    )
    refs = parse(path)
    # 1.0 parses as float in YAML; we want to be conservative and ignore it.
    assert len(refs) == 1
    assert refs[0].version is None
    assert refs[0].component_identity == "skill/weird-version"


def test_parse_skips_when_name_field_is_empty_string(tmp_path):
    """Empty `name: ''` should fall through to the directory name."""
    path = _write_skill(tmp_path, "dirname", "name: ''\ndescription: empty name\n")
    refs = parse(path)
    assert len(refs) == 1
    assert refs[0].name == "dirname"


def test_parse_returns_empty_on_unreadable_file(tmp_path):
    """A directory at the SKILL.md path raises IsADirectoryError; degrade gracefully."""
    fake = tmp_path / "weird"
    fake.mkdir()
    (fake / "SKILL.md").mkdir()  # Directory where a file is expected.
    assert parse(fake / "SKILL.md") == []
