import json
from pathlib import Path

import pytest

from tools.parsers import settings_layers

REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "openaca.schema.json"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_github_actions_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear `GITHUB_ACTIONS` for every test by default.

    `tools/scan.py` auto-promotes the default `--format` from `text` to
    `github` whenever `GITHUB_ACTIONS=true` is in the environment. CI runs
    naturally have this set, so any test asserting on `text`-format-specific
    output (e.g., the ACA framing footer) silently sees github annotations
    instead. We hit that failure once already (PR #32 build #75653518460).

    Tests that *want* the auto-promotion path can re-set the var in-test
    via `monkeypatch.setenv("GITHUB_ACTIONS", "true")` — see
    `test_scan_auto_promotes_format_to_github_under_actions`.
    """
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


@pytest.fixture(autouse=True)
def _offline_osv_for_scan_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep CLI tests offline while preserving OSV-backed scan semantics."""

    def fake_augment(refs, base_corpus, *, progress=None):
        records = list(base_corpus)
        seen = {record.get("id") for record in records if isinstance(record, dict)}
        for ref in refs:
            fixture = _osv_fixture_for_ref(ref)
            if fixture is None or fixture["id"] in seen:
                continue
            records.append(fixture)
            seen.add(fixture["id"])
        return records, []

    monkeypatch.setattr("tools.scan.augment_corpus", fake_augment)


@pytest.fixture(autouse=True)
def _isolate_managed_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """Point the managed-settings root at an empty per-test directory.

    The machine running the suite may itself be a managed Claude Code
    endpoint, and OpenACA's own `policy compile --host claude` installs a
    `managed-settings.d/50-openaca-policy.json` drop-in. Without this,
    `settings_layers.load()` merges whatever policy the host has into layers
    the test built from `tmp_path`, so eight tests failed locally while CI
    (no managed settings) stayed green.

    `settings_layers.default_managed_dir` is the one seam every caller routes
    through, so patching it here covers reads (`load`, `load_managed`),
    provenance paths (`graph_build`), and the compile target (`policy_cli`)
    at once. Callers passing an explicit directory are unaffected; a test that
    wants the real platform default can patch the seam back.
    """
    root = tmp_path_factory.mktemp("managed-settings-root")
    monkeypatch.setattr(settings_layers, "default_managed_dir", lambda: root)
    return root


def _osv_fixture_for_ref(ref):
    fixture_by_package = {
        ("npm", "@cyanheads/git-mcp-server"): "ghsa-3q26-f695-pp76.json",
        ("npm", "mcp-remote"): "ghsa-6xpm-ggf7-wc3p.json",
        ("npm", "@akoskm/create-mcp-server-stdio"): "ghsa-3ch2-jxxc-v4xf.json",
        ("PyPI", "aws-mcp-server"): "ghsa-m4qw-j7mx-qv6h.json",
        ("npm", "@serverless/mcp-server"): "ghsa-rwc2-f344-q6w6.json",
    }
    filename = fixture_by_package.get((ref.ecosystem, ref.name))
    if filename is None:
        return None
    return json.loads((FIXTURES / "osv" / filename).read_text(encoding="utf-8"))


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def schema_path() -> Path:
    return SCHEMA_PATH


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
