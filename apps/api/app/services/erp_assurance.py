"""Pure comparison of document field claims with live ERP metadata."""

from __future__ import annotations

import hashlib
import re

from app.domain.assurance import (
    AssuranceFinding,
    AssuranceSeverity,
    ERPMetadataSnapshot,
    MetadataValidationResult,
)
from app.domain.evidence import ArtifactSection, DocumentArtifact

_QUALIFIED_FIELD_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]{0,127})\.([A-Za-z][A-Za-z0-9_]{0,127})\b"
)
_TYPED_FIELD_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]{0,127})\.([A-Za-z][A-Za-z0-9_]{0,127})"
    r"\s*(?:type|型)?\s*[:=]\s*([A-Za-z][A-Za-z0-9_.]{0,119})\b",
    re.IGNORECASE,
)
_REQUIRED_FIELD_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]{0,127})\.([A-Za-z][A-Za-z0-9_]{0,127})"
    r"\s+(required|mandatory|optional|必須|任意)\b",
    re.IGNORECASE,
)
_KEY_FIELD_RE = re.compile(
    r"(?:business key|primary key|key|業務キー|主キー)\s*[:=]\s*"
    r"([A-Za-z][A-Za-z0-9_]{0,127})\.([A-Za-z][A-Za-z0-9_]{0,127})\b",
    re.IGNORECASE,
)
_CODE_VALUE_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]{0,127})\.([A-Za-z][A-Za-z0-9_]{0,127})"
    r"\s+(?:value|code|値)\s*[:=]\s*([A-Za-z0-9_.-]{1,128})\b",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"(?:API\s*)?version\s*[:=]\s*([A-Za-z0-9_.-]{1,120})", re.IGNORECASE)
_ENVIRONMENT_RE = re.compile(
    r"(?:environment|環境)\s*[:=]\s*([A-Za-z0-9_.-]{1,120})", re.IGNORECASE
)


def _metadata_finding(
    *,
    run_id: str,
    snapshot: ERPMetadataSnapshot,
    artifact: DocumentArtifact,
    section: ArtifactSection,
    claim_key: str,
    message: str,
) -> AssuranceFinding:
    rule_id = "builtin.erp-metadata"
    fingerprint = hashlib.sha256(
        f"{rule_id}\0{artifact.artifact_id}\0{claim_key}".encode()
    ).hexdigest()
    finding_id = hashlib.sha256(
        f"{fingerprint}\0{artifact.current_revision_id}\0{snapshot.metadata_hash}".encode()
    ).hexdigest()
    return AssuranceFinding(
        finding_id=finding_id,
        fingerprint=fingerprint,
        run_id=run_id,
        rule_id=rule_id,
        artifact_id=artifact.artifact_id,
        revision_id=artifact.current_revision_id,
        source_reference=artifact.source_reference,
        document_kind=artifact.document_kind,
        title="ERP metadata mismatch",
        description=message,
        severity=AssuranceSeverity.high,
        locator=section.locator,
        suggested_governance_kind="architecture_impact",
    )


def validate_erp_metadata(
    *,
    run_id: str,
    snapshot: ERPMetadataSnapshot,
    artifacts: list[DocumentArtifact],
    sections: list[ArtifactSection],
) -> MetadataValidationResult:
    entities = {entity.name.casefold(): entity for entity in snapshot.entities}
    sections_by_artifact: dict[str, list[ArtifactSection]] = {}
    for section in sections:
        sections_by_artifact.setdefault(section.artifact_id, []).append(section)
    findings: list[AssuranceFinding] = []
    for artifact in artifacts:
        for section in sections_by_artifact.get(artifact.artifact_id, []):
            for entity_name, field_name in _QUALIFIED_FIELD_RE.findall(section.content):
                entity = entities.get(entity_name.casefold())
                valid_fields = (
                    {field.name.casefold() for field in entity.fields} if entity else set()
                )
                if entity is not None and field_name.casefold() in valid_fields:
                    continue
                message = (
                    f"Entity {entity_name} is not present in the configured ERP metadata."
                    if entity is None
                    else f"Field {entity_name}.{field_name} is not present in ERP metadata."
                )
                findings.append(
                    _metadata_finding(
                        run_id=run_id,
                        snapshot=snapshot,
                        artifact=artifact,
                        section=section,
                        claim_key=f"field:{entity_name}.{field_name}",
                        message=message,
                    )
                )
            for entity_name, field_name, claimed_type in _TYPED_FIELD_RE.findall(
                section.content
            ):
                entity = entities.get(entity_name.casefold())
                field = next(
                    (
                        value
                        for value in entity.fields
                        if value.name.casefold() == field_name.casefold()
                    ),
                    None,
                ) if entity else None
                if field is not None and field.data_type.casefold() != claimed_type.casefold():
                    findings.append(
                        _metadata_finding(
                            run_id=run_id,
                            snapshot=snapshot,
                            artifact=artifact,
                            section=section,
                            claim_key=f"type:{entity_name}.{field_name}",
                            message=(
                                f"Field {entity_name}.{field_name} is {field.data_type} in ERP, "
                                f"not {claimed_type}."
                            ),
                        )
                    )
            for entity_name, field_name, requirement in _REQUIRED_FIELD_RE.findall(
                section.content
            ):
                entity = entities.get(entity_name.casefold())
                field = next(
                    (
                        value
                        for value in entity.fields
                        if value.name.casefold() == field_name.casefold()
                    ),
                    None,
                ) if entity else None
                claims_required = requirement.casefold() in {"required", "mandatory", "必須"}
                if field is not None and field.required != claims_required:
                    findings.append(
                        _metadata_finding(
                            run_id=run_id,
                            snapshot=snapshot,
                            artifact=artifact,
                            section=section,
                            claim_key=f"required:{entity_name}.{field_name}",
                            message=(
                                f"Required-field claim for {entity_name}.{field_name} "
                                "does not match ERP metadata."
                            ),
                        )
                    )
            for entity_name, field_name in _KEY_FIELD_RE.findall(section.content):
                entity = entities.get(entity_name.casefold())
                field = next(
                    (
                        value
                        for value in entity.fields
                        if value.name.casefold() == field_name.casefold()
                    ),
                    None,
                ) if entity else None
                if field is not None and not field.key:
                    findings.append(
                        _metadata_finding(
                            run_id=run_id,
                            snapshot=snapshot,
                            artifact=artifact,
                            section=section,
                            claim_key=f"key:{entity_name}.{field_name}",
                            message=f"Field {entity_name}.{field_name} is not an ERP key.",
                        )
                    )
            for entity_name, field_name, claimed_value in _CODE_VALUE_RE.findall(
                section.content
            ):
                entity = entities.get(entity_name.casefold())
                field = next(
                    (
                        value
                        for value in entity.fields
                        if value.name.casefold() == field_name.casefold()
                    ),
                    None,
                ) if entity else None
                if (
                    field is not None
                    and field.code_values
                    and claimed_value not in field.code_values
                ):
                    findings.append(
                        _metadata_finding(
                            run_id=run_id,
                            snapshot=snapshot,
                            artifact=artifact,
                            section=section,
                            claim_key=f"code:{entity_name}.{field_name}:{claimed_value}",
                            message=(
                                f"Value {claimed_value} is not valid for "
                                f"{entity_name}.{field_name}."
                            ),
                        )
                    )
            for claimed_version in _VERSION_RE.findall(section.content):
                if (
                    snapshot.api_version
                    and claimed_version.casefold() != snapshot.api_version.casefold()
                ):
                    findings.append(
                        _metadata_finding(
                            run_id=run_id,
                            snapshot=snapshot,
                            artifact=artifact,
                            section=section,
                            claim_key="api-version",
                            message="Document API version does not match live ERP metadata.",
                        )
                    )
            for claimed_environment in _ENVIRONMENT_RE.findall(section.content):
                if (
                    snapshot.environment
                    and claimed_environment.casefold() != snapshot.environment.casefold()
                ):
                    findings.append(
                        _metadata_finding(
                            run_id=run_id,
                            snapshot=snapshot,
                            artifact=artifact,
                            section=section,
                            claim_key="environment",
                            message=(
                                "Document environment does not match the configured ERP source."
                            ),
                        )
                    )
    return MetadataValidationResult(
        status="current",
        snapshot_id=snapshot.snapshot_id,
        findings=findings,
    )
