"""Transport-only request wrappers for assurance administration."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _ApiContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrustedRuleKeyRequest(_ApiContract):
    key_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    label: str = Field(min_length=1, max_length=200)
    public_key_base64: str = Field(min_length=40, max_length=128)
