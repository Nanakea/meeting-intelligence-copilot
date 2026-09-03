"""Standalone loopback entry point for the Windows internal-pilot sidecar."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Mapping

import uvicorn

from app.api.main import app, load_default_assurance_workspace, load_default_context_service
from app.domain.assurance import AssuranceRunRequest

BACKEND_PORT_ENV = "MEETING_INTELLIGENCE_BACKEND_PORT"
DEFAULT_BACKEND_PORT = 8000


def backend_port(environ: Mapping[str, str] | None = None) -> int:
    values = os.environ if environ is None else environ
    raw = values.get(BACKEND_PORT_ENV, str(DEFAULT_BACKEND_PORT))
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"{BACKEND_PORT_ENV} must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{BACKEND_PORT_ENV} must be between 1 and 65535")
    return port


def run_scheduled_assurance(schedule_id: str) -> int:
    """Run one user-approved schedule without opening a network listener."""

    context_service = load_default_context_service()
    workspace = load_default_assurance_workspace(context_service)
    if context_service is None or workspace is None:
        return 2
    schedule = next(
        (
            value
            for value in workspace.schedules()
            if value.schedule_id == schedule_id and value.enabled
        ),
        None,
    )
    if schedule is None:
        return 3

    async def execute() -> int:
        degraded = False
        try:
            remote_documents, unavailable = await context_service.prepare_assurance_sources(
                schedule.connector_ids
            )
            degraded = degraded or bool(unavailable)
            run = await workspace.run(
                AssuranceRunRequest(
                    connector_ids=schedule.connector_ids,
                    rule_pack_ids=schedule.rule_pack_ids,
                    changed_only=True,
                ),
                transient_documents=remote_documents,
                unavailable_connector_ids=unavailable,
            )
            return 0 if run.status.value == "completed" and not degraded else 4
        finally:
            await context_service.close()

    return asyncio.run(execute())


def main(
    environ: Mapping[str, str] | None = None,
    argv: list[str] | None = None,
) -> int:
    """Run the existing FastAPI app on a fixed loopback host."""

    arguments = argv if argv is not None else ([] if environ is not None else sys.argv[1:])
    if arguments:
        if len(arguments) == 2 and arguments[0] == "--run-scheduled-assurance":
            schedule_id = arguments[1]
            if not schedule_id.replace("-", "").replace("_", "").isalnum():
                return 5
            return run_scheduled_assurance(schedule_id)
        return 5

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=backend_port(environ),
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
