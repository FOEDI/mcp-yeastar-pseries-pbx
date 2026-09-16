import asyncio
import logging
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from yeastar_mcp.cli import _suppress_sensitive_http_logging
from yeastar_mcp.client import YeastarAPIError, YeastarClient
from yeastar_mcp.settings import Settings


def test_stdio_server_suppresses_http_request_url_logging() -> None:
    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore")
    previous = (httpx_logger.level, httpcore_logger.level)
    try:
        httpx_logger.setLevel(logging.INFO)
        httpcore_logger.setLevel(logging.INFO)
        _suppress_sensitive_http_logging()
        assert httpx_logger.level >= logging.WARNING
        assert httpcore_logger.level >= logging.WARNING
    finally:
        httpx_logger.setLevel(previous[0])
        httpcore_logger.setLevel(previous[1])


@pytest.fixture
def settings() -> Settings:
    return Settings(
        base_url="https://pbx.example.test:8088",
        client_id="client-id",
        client_secret="client-secret",
    )


@pytest.mark.asyncio
@respx.mock
async def test_client_revokes_access_token_when_closed(settings: Settings, fixture_json) -> None:
    respx.post("https://pbx.example.test:8088/openapi/v1.0/get_token").mock(
        return_value=httpx.Response(
            200,
            json={
                "errcode": 0,
                "errmsg": "SUCCESS",
                "access_token": "access",
                "access_token_expire_time": 1800,
            },
        )
    )
    respx.get("https://pbx.example.test:8088/openapi/v1.0/system/information").mock(
        return_value=httpx.Response(200, json=fixture_json("pbx_info.json"))
    )
    revoke = respx.get("https://pbx.example.test:8088/openapi/v1.0/del_token").mock(
        return_value=httpx.Response(200, json={"errcode": 0, "errmsg": "SUCCESS"})
    )
    async with YeastarClient(settings) as client:
        await client.get("system/information")
    assert revoke.called
    assert revoke.calls[0].request.url.params["access_token"] == "access"


@pytest.mark.asyncio
@respx.mock
async def test_client_closes_http_transport_when_revocation_is_cancelled(
    settings: Settings, fixture_json
) -> None:
    respx.post("https://pbx.example.test:8088/openapi/v1.0/get_token").mock(
        return_value=httpx.Response(
            200,
            json={"errcode": 0, "access_token": "access", "access_token_expire_time": 1800},
        )
    )
    respx.get("https://pbx.example.test:8088/openapi/v1.0/system/information").mock(
        return_value=httpx.Response(200, json=fixture_json("pbx_info.json"))
    )
    client = YeastarClient(settings)
    await client.get("system/information")
    client._send = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await client.aclose()

    assert client._http.is_closed is True


@pytest.mark.asyncio
@respx.mock
async def test_client_authenticates_then_uses_get_for_read_call(
    settings: Settings, fixture_json
) -> None:
    token = respx.post("https://pbx.example.test:8088/openapi/v1.0/get_token").mock(
        return_value=httpx.Response(
            200,
            json={
                "errcode": 0,
                "errmsg": "SUCCESS",
                "access_token": "access",
                "access_token_expire_time": 1800,
                "refresh_token": "refresh",
                "refresh_token_expire_time": 86400,
            },
        )
    )
    info = respx.get("https://pbx.example.test:8088/openapi/v1.0/system/information").mock(
        return_value=httpx.Response(200, json=fixture_json("pbx_info.json"))
    )
    async with YeastarClient(settings) as client:
        result = await client.get("system/information")
    assert result["data"]["model_name"] == "P-Series Software Edition"
    assert token.called and info.called
    assert info.calls[0].request.url.params["access_token"] == "access"
    assert info.calls[0].request.headers["user-agent"].startswith("yeastar-mcp/")


@pytest.mark.asyncio
@respx.mock
async def test_yeastar_error_is_not_returned_as_data(settings: Settings) -> None:
    respx.post("https://pbx.example.test:8088/openapi/v1.0/get_token").mock(
        return_value=httpx.Response(200, json={"errcode": 10003, "errmsg": "AUTHENTICATION FAILED"})
    )
    async with YeastarClient(settings) as client:
        with pytest.raises(YeastarAPIError, match="AUTHENTICATION FAILED"):
            await client.get("system/information")


@pytest.mark.asyncio
@respx.mock
async def test_http_errors_never_expose_access_token(settings: Settings) -> None:
    respx.post("https://pbx.example.test:8088/openapi/v1.0/get_token").mock(
        return_value=httpx.Response(
            200,
            json={
                "errcode": 0,
                "access_token": "SUPERSECRET-TOKEN",
                "refresh_token": "refresh-token",
                "expires_in": 1800,
            },
        )
    )
    respx.get("https://pbx.example.test:8088/openapi/v1.0/system/information").mock(
        return_value=httpx.Response(500, json={"errcode": 500, "errmsg": "failure"})
    )

    async with YeastarClient(settings) as client:
        with pytest.raises(YeastarAPIError) as exc:
            await client.get("system/information")

    assert "SUPERSECRET-TOKEN" not in str(exc.value)
    assert "access_token" not in str(exc.value)


@pytest.mark.asyncio
@respx.mock
async def test_network_errors_never_expose_access_token(settings: Settings) -> None:
    respx.post("https://pbx.example.test:8088/openapi/v1.0/get_token").mock(
        return_value=httpx.Response(200, json={"errcode": 0, "access_token": "SUPERSECRET-TOKEN"})
    )
    respx.get("https://pbx.example.test:8088/openapi/v1.0/system/information").mock(
        side_effect=httpx.ConnectError("failed for ?access_token=SUPERSECRET-TOKEN")
    )
    async with YeastarClient(settings) as client:
        with pytest.raises(YeastarAPIError) as exc:
            await client.get("system/information")
    assert "SUPERSECRET-TOKEN" not in str(exc.value)
    assert "access_token" not in str(exc.value)


@pytest.mark.asyncio
@respx.mock
async def test_application_errors_redact_tokens_and_credentials(settings: Settings) -> None:
    respx.post("https://pbx.example.test:8088/openapi/v1.0/get_token").mock(
        return_value=httpx.Response(
            200,
            json={
                "errcode": 0,
                "access_token": "SUPERSECRET-TOKEN",
                "refresh_token": "SUPERSECRET-REFRESH",
                "access_token_expire_time": 1800,
                "refresh_token_expire_time": 86400,
            },
        )
    )
    respx.get("https://pbx.example.test:8088/openapi/v1.0/system/information").mock(
        return_value=httpx.Response(
            200,
            json={
                "errcode": 10001,
                "errmsg": "bad ?access_token=SUPERSECRET-TOKEN client-secret SUPERSECRET-REFRESH",
            },
        )
    )
    async with YeastarClient(settings) as client:
        with pytest.raises(YeastarAPIError) as exc:
            await client.get("system/information")
    message = str(exc.value)
    assert "SUPERSECRET" not in message
    assert "client-secret" not in message


@pytest.mark.asyncio
@respx.mock
async def test_rejected_refresh_falls_back_to_client_credentials_once(settings: Settings) -> None:
    token = respx.post("https://pbx.example.test:8088/openapi/v1.0/get_token").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "access_token": "first-access",
                    "access_token_expire_time": 1,
                    "refresh_token": "rejected-refresh",
                    "refresh_token_expire_time": 86400,
                },
            ),
            httpx.Response(200, json={"errcode": 0, "access_token": "replacement-access"}),
        ]
    )
    refresh = respx.post("https://pbx.example.test:8088/openapi/v1.0/refresh_token").mock(
        return_value=httpx.Response(200, json={"errcode": 10003, "errmsg": "TOKEN INVALID"})
    )
    info = respx.get("https://pbx.example.test:8088/openapi/v1.0/system/information").mock(
        return_value=httpx.Response(200, json={"errcode": 0, "data": {}})
    )
    async with YeastarClient(settings) as client:
        await client.get("system/information")
        assert client._token is not None
        client._token.access_expires_at = 0
        await client.get("system/information")
    assert refresh.call_count == 1
    assert token.call_count == 2
    assert info.calls[-1].request.url.params["access_token"] == "replacement-access"


@pytest.mark.asyncio
async def test_client_rejects_get_outside_documented_allowlist(settings: Settings) -> None:
    async with YeastarClient(settings) as client:
        with pytest.raises(ValueError, match="not an allowed read endpoint"):
            await client.get("extension/getpassword")
