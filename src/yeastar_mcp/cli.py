"""Command-line entry points for stdio serving and diagnostics."""

import argparse
import asyncio
import json
import logging

from yeastar_mcp.client import YeastarClient
from yeastar_mcp.doctor import run_doctor
from yeastar_mcp.server import create_server
from yeastar_mcp.services import YeastarService
from yeastar_mcp.settings import Settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yeastar-mcp")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="run the local stdio MCP server")
    doctor = subparsers.add_parser("doctor", help="check configuration and read-only PBX access")
    doctor.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _suppress_sensitive_http_logging() -> None:
    """Prevent query-string access tokens from appearing in request URL logs."""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _serve() -> None:
    _suppress_sensitive_http_logging()
    settings = Settings()
    client = YeastarClient(settings)
    create_server(YeastarService(client, allow_raw_numbers=settings.allow_raw_numbers)).run(
        transport="stdio"
    )


def main() -> None:
    args = _parser().parse_args()
    if args.command == "doctor":
        report = asyncio.run(run_doctor(Settings()))
        if args.as_json:
            print(json.dumps(report.model_dump(), indent=2))
        else:
            for check in report.checks:
                print(f"{check.status:4} {check.name}: {check.detail}")
        raise SystemExit(0 if report.ok else 1)
    _serve()


if __name__ == "__main__":
    main()
