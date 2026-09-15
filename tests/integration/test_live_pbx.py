import pytest

from yeastar_mcp.doctor import run_doctor
from yeastar_mcp.settings import Settings

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_live_read_only_doctor() -> None:
    settings = Settings()
    if not settings.credentials_configured:
        pytest.skip("Set YEASTAR_BASE_URL/CLIENT_ID/CLIENT_SECRET for live integration")

    report = await run_doctor(settings)
    failures = [check.model_dump() for check in report.checks if check.status == "FAIL"]
    assert report.ok, failures
    assert not [check for check in report.checks if check.status == "SKIP"]
