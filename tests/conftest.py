from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "asve.schema.json"
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


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def schema_path() -> Path:
    return SCHEMA_PATH


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
