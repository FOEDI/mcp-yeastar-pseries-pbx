import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp import Client, StdioServerParameters

from yeastar_mcp.doctor import run_doctor
from yeastar_mcp.server import TOOL_NAMES, create_server
from yeastar_mcp.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.anyio
async def test_mcp_exposes_exact_read_only_tool_surface() -> None:
    server = create_server(AsyncMock())
    async with Client(server, raise_exceptions=True) as client:
        listed = await client.list_tools()
    names = {tool.name for tool in listed.tools}
    assert names == TOOL_NAMES
    assert all(not name.startswith(("create_", "update_", "delete_", "set_")) for name in names)
    assert all(tool.annotations and tool.annotations.read_only_hint for tool in listed.tools)
    assert all(tool.annotations and not tool.annotations.destructive_hint for tool in listed.tools)


@pytest.mark.asyncio
async def test_doctor_skips_pbx_checks_without_credentials() -> None:
    report = await run_doctor(Settings(base_url="https://pbx.example.test:8088"))
    assert report.ok is True
    assert {check.status for check in report.checks if check.name.startswith("pbx_")} == {"SKIP"}


@pytest.mark.asyncio
async def test_real_stdio_entrypoint_lists_only_expected_tools() -> None:
    executable = Path(sys.executable).parent / "yeastar-mcp"
    params = StdioServerParameters(
        command=str(executable),
        cwd=PROJECT_ROOT,
    )
    async with Client(params) as client:
        tools = await client.list_tools()

    assert {tool.name for tool in tools.tools} == set(TOOL_NAMES)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "firmware", "report_version", "cdr_version"),
    [
        ("P-Series Software Edition", "83.21.0.117", "v2.0", "v1.0"),
        ("P-Series Appliance Edition", "37.23.0.123", "v2.0", "v2.0"),
        ("P-Series Cloud Edition", "84.21.0.116", "v1.0", "v1.0"),
    ],
)
async def test_doctor_routes_report_and_cdr_probes_independently(
    monkeypatch, model: str, firmware: str, report_version: str, cdr_version: str
) -> None:
    requests = []

    class FakeClient:
        def __init__(self, settings) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, endpoint, *, api_version="v1.0", params=None):
            requests.append((endpoint, api_version, params))
            if endpoint == "system/information":
                return {
                    "data": {
                        "model_name": model,
                        "firmware_version": firmware,
                        "system_date_format": "YYYY/MM/DD",
                    }
                }
            if endpoint == "queue/list":
                return {"queue_list": []}
            return {}

    monkeypatch.setattr("yeastar_mcp.doctor.YeastarClient", FakeClient)
    settings = Settings(base_url="https://pbx.example.test", client_id="id", client_secret="secret")
    assert (await run_doctor(settings)).ok
    report_probe = next(item for item in requests if item[0] == "call_report/list")
    cdr_probe = next(item for item in requests if item[0] == "cdr/list")
    assert report_probe[1] == report_version
    assert "page" not in report_probe[2] and "page_size" not in report_probe[2]
    assert cdr_probe[1] == cdr_version
