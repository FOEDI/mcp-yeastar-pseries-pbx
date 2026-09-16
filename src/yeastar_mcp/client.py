"""Central async Yeastar OpenAPI client and token management."""

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from yeastar_mcp import __version__
from yeastar_mcp.endpoints import READ_ENDPOINTS
from yeastar_mcp.settings import Settings


class YeastarError(RuntimeError):
    """Base error safe to surface to MCP clients."""


class YeastarConfigurationError(YeastarError):
    """Required PBX connection settings are absent."""


class YeastarAPIError(YeastarError):
    """The PBX returned an HTTP or OpenAPI error."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _TokenState:
    access_token: str
    access_expires_at: float
    refresh_token: str | None = None
    refresh_expires_at: float = 0.0


class YeastarClient:
    """One async client for every Yeastar HTTP call.

    Business operations can only use :meth:`get`. POST is private and reserved
    for OAuth token acquisition/refresh, preserving the V1 read-only boundary.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._token: _TokenState | None = None
        self._token_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            base_url=settings.base_url or "http://127.0.0.1",
            verify=settings.verify_ssl,
            timeout=settings.timeout_seconds,
            transport=transport,
            headers={"User-Agent": f"yeastar-mcp/{__version__}", "Accept": "application/json"},
        )

    async def __aenter__(self) -> "YeastarClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        token = self._token.access_token if self._token is not None else None
        self._token = None
        try:
            if token:
                try:
                    payload = await self._send(
                        "GET",
                        self._url("v1.0", "del_token"),
                        params={"access_token": token},
                    )
                    self._validate_payload(payload)
                except Exception:
                    # Revocation is best-effort and must not mask the tool result.
                    pass
        finally:
            await self._http.aclose()

    async def get(
        self,
        endpoint: str,
        *,
        api_version: str = "v1.0",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform an authenticated, read-only OpenAPI request."""
        version = api_version.removeprefix("openapi/").strip("/")
        normalized_endpoint = endpoint.lstrip("/")
        if (version, normalized_endpoint) not in READ_ENDPOINTS:
            raise ValueError(f"{version}/{normalized_endpoint} is not an allowed read endpoint")
        token = await self._ensure_access_token()
        query = {key: value for key, value in (params or {}).items() if value is not None}
        query["access_token"] = token
        payload = await self._send("GET", self._url(api_version, endpoint), params=query)
        if self._token_expired(payload):
            await self._invalidate_access_token()
            query["access_token"] = await self._ensure_access_token()
            payload = await self._send("GET", self._url(api_version, endpoint), params=query)
        return self._validate_payload(payload)

    async def _ensure_access_token(self) -> str:
        now = time.monotonic()
        if self._token and now < self._token.access_expires_at:
            return self._token.access_token
        async with self._token_lock:
            now = time.monotonic()
            if self._token and now < self._token.access_expires_at:
                return self._token.access_token
            if self._token and self._token.refresh_token and now < self._token.refresh_expires_at:
                try:
                    payload = self._validate_payload(
                        await self._send(
                            "POST",
                            self._url("v1.0", "refresh_token"),
                            json={"refresh_token": self._token.refresh_token},
                        )
                    )
                except YeastarError:
                    self._token = None
                    payload = self._validate_payload(await self._authenticate())
            else:
                payload = self._validate_payload(await self._authenticate())
            self._set_token(payload)
            assert self._token is not None
            return self._token.access_token

    async def _authenticate(self) -> dict[str, Any]:
        if not self.settings.credentials_configured:
            raise YeastarConfigurationError(
                "YEASTAR_BASE_URL, YEASTAR_CLIENT_ID and YEASTAR_CLIENT_SECRET are required"
            )
        assert self.settings.client_secret is not None
        return await self._send(
            "POST",
            self._url("v1.0", "get_token"),
            json={
                "username": self.settings.client_id,
                "password": self.settings.client_secret.get_secret_value(),
            },
        )

    async def _send(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._http.request(method, url, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            path = exc.request.url.path
            raise YeastarAPIError(f"Yeastar HTTP {status} for {path}") from None
        except httpx.HTTPError:
            path = httpx.URL(url).path
            raise YeastarAPIError(f"Yeastar network request failed for {path}") from None
        except ValueError as exc:
            raise YeastarAPIError("Yeastar returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise YeastarAPIError("Yeastar returned an unexpected JSON payload")
        return payload

    def _validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        code = payload.get("errcode")
        if code not in (None, 0):
            message = self._redact(str(payload.get("errmsg") or "Yeastar API request failed"))
            raise YeastarAPIError(message, code=int(code) if isinstance(code, int) else None)
        return payload

    def _redact(self, message: str) -> str:
        redacted = re.sub(
            r"(?i)(access_token|refresh_token|client_secret|password)=([^&\s]+)",
            r"\1=[REDACTED]",
            message,
        )
        values = [self.settings.client_id]
        if self.settings.client_secret is not None:
            values.append(self.settings.client_secret.get_secret_value())
        if self._token is not None:
            values.extend([self._token.access_token, self._token.refresh_token])
        for value in values:
            if value:
                redacted = redacted.replace(str(value), "[REDACTED]")
        return redacted

    @staticmethod
    def _token_expired(payload: dict[str, Any]) -> bool:
        return "TOKEN EXPIRED" in str(payload.get("errmsg", "")).upper()

    async def _invalidate_access_token(self) -> None:
        async with self._token_lock:
            if self._token:
                self._token.access_expires_at = 0.0

    def _set_token(self, payload: dict[str, Any]) -> None:
        access = payload.get("access_token")
        if not access:
            raise YeastarAPIError("Yeastar token response did not include access_token")
        now = time.monotonic()
        access_ttl = max(1, int(payload.get("access_token_expire_time", 1800)) - 30)
        refresh_ttl = max(1, int(payload.get("refresh_token_expire_time", 0)) - 30)
        self._token = _TokenState(
            access_token=str(access),
            access_expires_at=now + access_ttl,
            refresh_token=(str(payload["refresh_token"]) if payload.get("refresh_token") else None),
            refresh_expires_at=now + refresh_ttl,
        )

    @staticmethod
    def _url(api_version: str, endpoint: str) -> str:
        version = api_version.removeprefix("openapi/").strip("/")
        if version not in {"v1.0", "v2.0"}:
            raise ValueError(f"unsupported Yeastar API version: {api_version}")
        return f"/openapi/{version}/{endpoint.lstrip('/')}"
