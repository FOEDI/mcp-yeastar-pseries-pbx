"""Auditable allowlist of documented Yeastar GET endpoints used by the server."""

from dataclasses import dataclass
from typing import Literal

APIVersion = Literal["v1.0", "v2.0"]


@dataclass(frozen=True)
class Endpoint:
    path: str
    version: APIVersion
    method: Literal["GET"] = "GET"


PBX_INFO = Endpoint("system/information", "v1.0")
QUEUES = Endpoint("queue/list", "v1.0")
CDR_DETAIL_V2 = Endpoint("cdr/detail", "v2.0")

READ_ENDPOINTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("v1.0", "system/information"),
        ("v1.0", "queue/list"),
        ("v1.0", "call_report/list"),
        ("v2.0", "call_report/list"),
        ("v1.0", "cdr/search"),
        ("v2.0", "cdr/search"),
        ("v1.0", "cdr/list"),
        ("v2.0", "cdr/list"),
        ("v2.0", "cdr/detail"),
    }
)
