"""Authenticated wire wrappers for v11 supervised improvement."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _WireContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImprovementSettingsUpdate(_WireContract):
    expected_revision: int = Field(ge=0)
    paused: bool
    excerpt_retention_days: Literal[30, 90, 365, -1] = 90


class ImprovementPackExportResponse(_WireContract):
    filename: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,120}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_base64: str = Field(min_length=1, max_length=6_000_000)


class ImprovementPackImportRequest(_WireContract):
    content_base64: str = Field(min_length=1, max_length=6_000_000)


class ImprovementDataDeleteResponse(_WireContract):
    status: Literal["deleted"] = "deleted"


class AskNowFeedbackRequest(_WireContract):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    suggestion_id: str = Field(min_length=1, max_length=256)
    state_version: int = Field(ge=0)
    reopen_count: int = Field(ge=0, le=10_000)
    action: Literal["useful", "dismissed"]


class NetSuiteMappingValidationRequest(_WireContract):
    connector_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")


class DocumentScanRequest(_WireContract):
    connector_ids: list[str] = Field(default_factory=list, max_length=100)


class DocumentDispositionExecuteRequest(_WireContract):
    expected_revision: int = Field(ge=1)
    destination_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    routing_action: Literal["copy", "move"] = "copy"


class DocumentClassificationBatchItem(_WireContract):
    classification_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_revision: int = Field(ge=1)
    action: Literal["approve", "archive"]


class DocumentClassificationBatchRequest(_WireContract):
    items: list[DocumentClassificationBatchItem] = Field(min_length=1, max_length=50)


class DocumentClassificationBatchResponse(_WireContract):
    reviewed: int = Field(ge=0, le=50)
