"""Secret-safe transport contracts for v9 integrations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.domain.context import ExternalSpaceSelection
from app.domain.integrations import DocumentRoutingDestination, NotificationPolicy


class _ApiContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CertificateCredentialRequest(_ApiContract):
    private_key_pem: SecretStr


class ExternalSpaceEnrollmentRequest(_ApiContract):
    selection: ExternalSpaceSelection
    external_id: SecretStr


class SlackAuthorizationStartRequest(_ApiContract):
    redirect_uri: str = Field(pattern=r"^meeting-intelligence://oauth/slack$")


class SlackAuthorizationStartResponse(_ApiContract):
    authorization_url: str
    state: str
    expires_at: datetime


class SlackAuthorizationCallbackRequest(_ApiContract):
    code: SecretStr
    state: SecretStr
    redirect_uri: str = Field(pattern=r"^meeting-intelligence://oauth/slack$")


class NotificationPolicyUpsertRequest(_ApiContract):
    policy: NotificationPolicy
    expected_revision: int | None = Field(default=None, ge=0)


class RoutingDestinationUpsertRequest(_ApiContract):
    destination: DocumentRoutingDestination
    root_path: SecretStr
