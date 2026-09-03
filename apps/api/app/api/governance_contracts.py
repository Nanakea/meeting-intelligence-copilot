"""Authenticated wire requests for the local governance workspace."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _WireContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GovernanceCandidateReviewRequest(_WireContract):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    state_version: int = Field(ge=0)
    action: Literal["confirm", "dismiss"]
    edits: dict[str, object] = Field(default_factory=dict, max_length=32)


class GovernanceRecordEventRequest(_WireContract):
    expected_revision: int = Field(ge=1)
    action: Literal["update", "status", "supersede", "link", "unlink"]
    changes: dict[str, object] = Field(default_factory=dict, max_length=32)


class SolutionBriefRequest(_WireContract):
    language: Literal["ja", "en", "ko"]
    entity_ids: list[str] = Field(default_factory=list, max_length=40)
    connector_ids: list[str] = Field(default_factory=list, max_length=32)
    limit: int = Field(default=30, ge=1, le=100)


class ImpactAnalysisRequest(_WireContract):
    root_entity_id: str = Field(min_length=1, max_length=64)
    connector_ids: list[str] = Field(default_factory=list, max_length=32)
