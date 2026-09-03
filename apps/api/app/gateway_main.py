"""Entrypoint for the separately deployed enterprise connector gateway."""

from __future__ import annotations

import json
import os
import ssl
from pathlib import Path

import uvicorn

from app.gateway.main import OidcIntrospectionAuthenticator, create_gateway_app
from app.gateway.repository import ConfiguredGatewayRepository


def main() -> None:
    certificate = os.environ.get("MEETING_INTELLIGENCE_GATEWAY_SERVER_CERT")
    private_key = os.environ.get("MEETING_INTELLIGENCE_GATEWAY_SERVER_KEY")
    client_ca = os.environ.get("MEETING_INTELLIGENCE_GATEWAY_CLIENT_CA")
    if not certificate or not private_key or not client_ca:
        raise SystemExit("gateway mTLS certificate, private key, and client CA are required")
    try:
        authenticator = OidcIntrospectionAuthenticator.from_env()
    except ValueError as error:
        raise SystemExit(str(error)) from error
    repository_path = os.environ.get("MEETING_INTELLIGENCE_GATEWAY_CONNECTORS")
    if not repository_path:
        raise SystemExit("gateway connector configuration path is required")
    try:
        repository = ConfiguredGatewayRepository.from_file(Path(repository_path))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit("gateway connector configuration is invalid") from error
    uvicorn.run(
        create_gateway_app(authenticator=authenticator, repository=repository),
        host=os.environ.get("MEETING_INTELLIGENCE_GATEWAY_HOST", "127.0.0.1"),
        port=int(os.environ.get("MEETING_INTELLIGENCE_GATEWAY_PORT", "8443")),
        ssl_certfile=certificate,
        ssl_keyfile=private_key,
        ssl_ca_certs=client_ca,
        ssl_cert_reqs=ssl.CERT_REQUIRED,
        access_log=False,
    )


if __name__ == "__main__":
    main()
