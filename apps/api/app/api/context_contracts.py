"""Loopback wire contracts for connector management and local action drafts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.domain.context import ConnectorDefinition, ConnectorKind


class _WireContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectorUpsertRequest(_WireContract):
    definition: ConnectorDefinition
    credential: SecretStr | None = None


class ConnectorSyncResponse(_WireContract):
    connector_id: str
    indexed_documents: int = Field(ge=0)


class MicrosoftDeviceCodeResponse(_WireContract):
    verification_uri: str
    user_code: str
    expires_at: datetime
    interval_seconds: int = Field(ge=5, le=300)


class MicrosoftOAuthPollResponse(_WireContract):
    status: Literal["pending", "complete", "declined", "expired", "not_started"]


class ContextCitationFeedbackRequest(_WireContract):
    connector_kind: ConnectorKind
    entity_type: str = Field(min_length=1, max_length=64)
    rank: int = Field(ge=1, le=20)
    action: Literal["relevant", "not_relevant"]
    state_version: int = Field(ge=0)
    response_ms: int = Field(ge=0, le=3_600_000)


class StructuredIssueDraftRequest(_WireContract):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    pain_id: str = Field(min_length=1, max_length=512)
    state_version: int = Field(ge=0)
    connector_ids: list[str] = Field(default_factory=list, max_length=32)
    include_context: bool = True


class StructuredIssueDraftBatchRequest(_WireContract):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    pain_ids: list[str] = Field(min_length=1, max_length=50)
    state_version: int = Field(ge=0)
    connector_ids: list[str] = Field(default_factory=list, max_length=32)
    include_context: bool = False


class FinalIssueDraftBatchRequest(_WireContract):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


class ProblemIdentityDecisionRequest(_WireContract):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    state_version: int = Field(ge=0)
    action: Literal["merge", "keep_separate"]
    pain_ids: list[str] = Field(min_length=2, max_length=2)
    survivor_pain_id: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def selected_problems_are_valid(self) -> ProblemIdentityDecisionRequest:
        if len(set(self.pain_ids)) != 2:
            raise ValueError("identity decisions require two distinct pain ids")
        if self.action == "keep_separate" and self.survivor_pain_id is not None:
            raise ValueError("keep-separate decisions do not have a survivor")
        if (
            self.survivor_pain_id is not None
            and self.survivor_pain_id not in self.pain_ids
        ):
            raise ValueError("identity decision survivor must be selected")
        return self
