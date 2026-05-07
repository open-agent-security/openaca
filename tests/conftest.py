from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "asve.schema.json"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def schema_path() -> Path:
    return SCHEMA_PATH


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
