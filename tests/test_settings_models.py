from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from yeastar_mcp.models import Period
from yeastar_mcp.settings import Settings


def test_empty_environment_means_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("YEASTAR_BASE_URL", "YEASTAR_CLIENT_ID", "YEASTAR_CLIENT_SECRET"):
        monkeypatch.setenv(name, "")
    settings = Settings()
    assert settings.credentials_configured is False


def test_period_rejects_reverse_range() -> None:
    with pytest.raises(ValidationError):
        Period(start=datetime(2026, 9, 2), end=datetime(2026, 9, 1))


def test_period_accepts_clear_iso_boundaries() -> None:
    period = Period.model_validate({"start": "2026-09-01T00:00:00", "end": "2026-09-30T23:59:59"})
    assert period.start < period.end


def test_period_rejects_timezone_aware_boundaries() -> None:
    with pytest.raises(ValidationError, match="naive PBX-local"):
        Period(start=datetime(2026, 9, 1, tzinfo=UTC), end=datetime(2026, 9, 2, tzinfo=UTC))
