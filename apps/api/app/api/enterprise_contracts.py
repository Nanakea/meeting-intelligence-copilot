"""Loopback API v13 contracts for enterprise sources and reviewed handoff."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.domain.enterprise import SourceSelection


class _WireContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnterpriseSourceEnrollmentRequest(_WireContract):
    selection: SourceSelection
    external_id: SecretStr


class EnterpriseAuthorizationStart(_WireContract):
    authorization_url: str = Field(min_length=1, max_length=4_096)
    state: str = Field(min_length=32, max_length=512)
    expires_at: datetime


class EnterpriseAuthorizationCallback(_WireContract):
    code: SecretStr
    state: str = Field(min_length=32, max_length=512)


class ReviewedExternalActionExportRequest(_WireContract):
    format: Literal[
        "markdown",
        "canonical_json",
        "jira_csv",
        "azure_boards_csv",
        "github_issue_markdown",
        "gitlab_issue_markdown",
        "servicenow_csv",
        "netsuite_review_json",
        "combined_zip",
    ]
    include_connected_excerpts: bool = False


class ReviewedExternalActionExport(_WireContract):
    filename: str = Field(min_length=1, max_length=240)
    media_type: str = Field(min_length=1, max_length=100)
    payload_base64: str = Field(min_length=1)
    direct_submission_enabled: Literal[False] = False
