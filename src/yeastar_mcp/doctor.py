"""Configuration and live read-only PBX diagnostics."""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from yeastar_mcp.client import YeastarClient
from yeastar_mcp.models import Period
from yeastar_mcp.services import YeastarService
from yeastar_mcp.settings import Settings


class DoctorCheck(BaseModel):
    name: str
    status: Literal["PASS", "FAIL", "SKIP"]
    detail: str


class DoctorReport(BaseModel):
    ok: bool
    checks: list[DoctorCheck]


async def run_doctor(settings: Settings) -> DoctorReport:
    """Run local checks and optional live probes; never mutate PBX state."""
    checks = [
        DoctorCheck(
            name="configuration",
            status="PASS" if settings.base_url else "SKIP",
            detail=("base URL configured" if settings.base_url else "YEASTAR_BASE_URL is not set"),
        )
    ]
    pbx_names = (
        "pbx_connection",
        "pbx_authentication",
        "pbx_version",
        "pbx_call_reports",
        "pbx_cdr",
        "pbx_queues",
        "pbx_capabilities",
    )
    if not settings.credentials_configured:
        checks.extend(
            DoctorCheck(name=name, status="SKIP", detail="Yeastar credentials not configured")
            for name in pbx_names
        )
        return DoctorReport(ok=True, checks=checks)

    async with YeastarClient(settings) as client:
        service = YeastarService(client)
        try:
            info = await service.get_pbx_info()
        except Exception as exc:
            checks.extend(
                DoctorCheck(name=name, status="FAIL", detail=str(exc)) for name in pbx_names[:3]
            )
            checks.extend(
                DoctorCheck(name=name, status="SKIP", detail="connection/authentication failed")
                for name in pbx_names[3:]
            )
            return DoctorReport(ok=False, checks=checks)

        checks.extend(
            [
                DoctorCheck(name="pbx_connection", status="PASS", detail="PBX responded"),
                DoctorCheck(
                    name="pbx_authentication", status="PASS", detail="access token acquired"
                ),
                DoctorCheck(name="pbx_version", status="PASS", detail=info.firmware_version),
            ]
        )
        end = datetime.now().replace(microsecond=0)
        period = Period(start=end - timedelta(days=1), end=end)
        report_version = (
            "v2.0"
            if service._supports_v2_call_reports(info.firmware_version, info.model)
            else "v1.0"
        )
        cdr_version = (
            "v2.0" if service._supports_v2_cdr(info.firmware_version, info.model) else "v1.0"
        )
        probes = [
            (
                "pbx_call_reports",
                lambda: client.get(
                    "call_report/list",
                    api_version=report_version,
                    params={
                        "type": "unreturnmisscall",
                        "start_time": service._format_for_pbx(period.start, info),
                        "end_time": service._format_for_pbx(period.end, info),
                    },
                ),
            ),
            (
                "pbx_cdr",
                lambda: client.get(
                    "cdr/list",
                    api_version=cdr_version,
                    params={"page": 1, "page_size": 1},
                ),
            ),
            ("pbx_queues", service.list_queues),
            ("pbx_capabilities", service.get_capabilities),
        ]
        for name, probe in probes:
            try:
                await probe()
                checks.append(DoctorCheck(name=name, status="PASS", detail="read probe succeeded"))
            except Exception as exc:
                checks.append(DoctorCheck(name=name, status="FAIL", detail=str(exc)))
    return DoctorReport(ok=all(check.status != "FAIL" for check in checks), checks=checks)
