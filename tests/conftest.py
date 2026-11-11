from pathlib import Path

import pytest

from applypilot.config import Settings, load_profile, load_settings
from applypilot.models import Profile
from applypilot.store import Store

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def profile() -> Profile:
    return load_profile(EXAMPLES / "profile.example.yaml")


@pytest.fixture
def settings() -> Settings:
    return load_settings(EXAMPLES / "settings.example.yaml")


@pytest.fixture
def store() -> Store:
    s = Store(":memory:")
    yield s
    s.close()
