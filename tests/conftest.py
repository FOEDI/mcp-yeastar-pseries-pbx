import json
from pathlib import Path

import pytest


@pytest.fixture
def fixture_json():
    def load(name: str) -> dict:
        return json.loads((Path(__file__).parent / "fixtures" / name).read_text())

    return load
