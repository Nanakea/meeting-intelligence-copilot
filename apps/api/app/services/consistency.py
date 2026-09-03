"""Pure deterministic documentation-to-system extraction and comparison."""

from __future__ import annotations

import base64
import csv
import fnmatch
import hashlib
import io
import re
from collections import defaultdict
from datetime import UTC, datetime

from app.domain.assurance import AssuranceSeverity, ERPMetadataSnapshot
from app.domain.consistency import (
    ApprovedException,
    AuthorityPolicy,
    AuthoritySide,
    ClaimOrigin,
    ClaimStatus,
    ConsistencyAttribution,
    ConsistencyCitation,
    ConsistencyComparison,
    ConsistencyExport,
    ConsistencyExportRequest,
    ConsistencyFinding,
    ConsistencyFindingReviewRequest,
    ConsistencyFindingStatus,
    ConsistencyMismatchKind,
    DocumentClaim,
    ObservationPersistence,
    ObservedSystemFact,
)
from app.domain.context import ConnectorKind, EntityGlossaryEntry
from app.domain.evidence import ArtifactLocator, ArtifactSection, DocumentArtifact, DocumentKind

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
_CONFIG_RE = re.compile(
    r"(?im)^\s*(?:config(?:uration)?|設定)\.([A-Za-z][A-Za-z0-9_.-]{0,127})"
    r"\s*[:=]\s*([^\r\n#]{1,500})$"
)
_SYSTEM_RE = re.compile(
    r"(?im)^\s*(?:system|product|service|システム|製品|サービス)\s*[:=]\s*"
    r"([^\r\n#]{1,200})$"
)
_VERSION_RE = re.compile(
    r"(?im)^\s*(?:API\s*)?(?:version|バージョン)\s*[:=]\s*"
    r"([A-Za-z0-9_.-]{1,120})\s*$"
)
_SCHEMA_VERSION_RE = re.compile(
    r"(?im)^\s*(?:schema version|スキーマバージョン)\s*[:=]\s*"
    r"([A-Za-z0-9_.-]{1,120})\s*$"
)
_ENVIRONMENT_RE = re.compile(
    r"(?im)^\s*(?:environment|環境)\s*[:=]\s*([A-Za-z0-9_.-]{1,120})\s*$"
)
_DEPLOYMENT_RE = re.compile(
    r"(?im)^\s*(?:deployment revision|deployed commit|デプロイ版|デプロイコミット)"
    r"\s*[:=]\s*([A-Za-z0-9_.-]{1,160})\s*$"
)
_TEST_RE = re.compile(
    r"(?im)^\s*(?:test status|pipeline status|テスト状態|パイプライン状態)"
    r"\s*[:=]\s*([A-Za-z0-9_.-]{1,120})\s*$"
)
_OWNER_RE = re.compile(
    r"(?im)^\s*(?:owner|responsible team|所有者|担当チーム)\s*[:=]\s*"
    r"([^\r\n#]{1,160})$"
)
_CONTROL_RE = re.compile(
    r"(?im)^\s*(?:control|security control|統制|セキュリティ統制)\s*[:=]\s*"
    r"([^\r\n#]{1,300})$"
)
_COMPLETE_SCOPE_RE = re.compile(
    r"(?im)^\s*(?:complete schema|schema complete|complete inventory|"
    r"完全スキーマ|完全一覧)\s*[:=]\s*([A-Za-z][A-Za-z0-9_]{0,127})\s*$"
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _clean(value: str) -> str:
    return " ".join(value.strip().split())


def _alias_map(entries: list[EntityGlossaryEntry]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for entry in entries:
        canonical = _clean(entry.canonical_name).casefold()
        for value in (entry.canonical_name, *entry.aliases):
            aliases[_clean(value).casefold()] = canonical
    return aliases


def canonical_value(value: str, aliases: dict[str, str]) -> str:
    normalized = _clean(value).casefold()
    return aliases.get(normalized, normalized)


def _impact_flags(content: str) -> list[str]:
    normalized = content.casefold()
    markers = {
        "production": ("production", "prod", "本番"),
        "security": ("security", "vulnerability", "セキュリティ", "脆弱"),
        "regulatory": ("regulatory", "compliance", "規制", "コンプライアンス"),
        "customer": ("customer", "client", "顧客"),
        "financial_close": ("financial close", "month-end close", "決算", "月次締め"),
    }
    return [key for key, terms in markers.items() if any(term in normalized for term in terms)]


def _citation(
    artifact: DocumentArtifact,
    section: ArtifactSection,
    connector_labels: dict[str, str],
    *,
    environment: str | None,
) -> ConsistencyCitation:
    return ConsistencyCitation(
        connector_id=artifact.connector_id,
        source_kind=artifact.source_kind,
        source_label=connector_labels.get(artifact.connector_id, artifact.source_kind.value),
        source_reference=artifact.source_reference,
        source_revision=artifact.current_revision_id,
        locator=section.locator,
        environment=environment,
        retrieved_at=artifact.indexed_at,
        freshness="current",
    )


def _claim(
    *,
    subject: str,
    property_name: str,
    value: str,
    value_type: str | None,
    environment: str | None,
    version: str | None,
    language: str,
    document_kind: DocumentKind,
    citation: ConsistencyCitation,
    impact_flags: list[str],
) -> DocumentClaim:
    clean_value = _clean(value)
    fingerprint = _digest(
        "\0".join(
            (
                subject,
                property_name,
                clean_value.casefold(),
                environment or "",
                citation.source_reference,
                str(citation.locator.model_dump(mode="json")),
            )
        )
    )
    return DocumentClaim(
        claim_id=_digest(f"{fingerprint}\0{citation.source_revision}"),
        fingerprint=fingerprint,
        canonical_subject=subject,
        canonical_property=property_name,
        expected_value=clean_value,
        expected_value_sha256=_digest(clean_value),
        value_type=value_type,
        environment=environment,
        version=version,
        language=language,
        document_kind=document_kind,
        origin=ClaimOrigin.deterministic,
        status=ClaimStatus.confirmed,
        impact_flags=impact_flags,
        citation=citation,
    )


def extract_document_claims(
    *,
    artifacts: list[DocumentArtifact],
    sections: list[ArtifactSection],
    connector_labels: dict[str, str],
    glossary_entries: list[EntityGlossaryEntry],
    language: str,
    environment_filter: str | None = None,
) -> list[DocumentClaim]:
    aliases = _alias_map(glossary_entries)
    artifacts_by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    claims: dict[str, DocumentClaim] = {}
    for section in sections:
        artifact = artifacts_by_id.get(section.artifact_id)
        if artifact is None:
            continue
        content = section.content
        system_match = _SYSTEM_RE.search(content)
        system = canonical_value(
            system_match.group(1) if system_match else artifact.title, aliases
        )
        environment_match = _ENVIRONMENT_RE.search(content)
        environment = (
            canonical_value(environment_match.group(1), aliases)
            if environment_match
            else environment_filter
        )
        version_match = _VERSION_RE.search(content)
        version = _clean(version_match.group(1)) if version_match else None
        citation = _citation(
            artifact, section, connector_labels, environment=environment
        )
        flags = _impact_flags(content)
        values: list[tuple[str, str, str, str | None]] = []
        for entity, field in _QUALIFIED_FIELD_RE.findall(content):
            subject = canonical_value(entity, aliases)
            values.append((subject, f"field.{field.casefold()}.exists", "true", "boolean"))
        for entity, field, data_type in _TYPED_FIELD_RE.findall(content):
            subject = canonical_value(entity, aliases)
            values.append((subject, f"field.{field.casefold()}.type", data_type, "type"))
        for entity, field, requirement in _REQUIRED_FIELD_RE.findall(content):
            subject = canonical_value(entity, aliases)
            required = requirement.casefold() in {"required", "mandatory", "必須"}
            values.append(
                (subject, f"field.{field.casefold()}.required", str(required).lower(), "boolean")
            )
        for entity, field in _KEY_FIELD_RE.findall(content):
            subject = canonical_value(entity, aliases)
            values.append((subject, f"field.{field.casefold()}.key", "true", "boolean"))
        for entity, field, code in _CODE_VALUE_RE.findall(content):
            subject = canonical_value(entity, aliases)
            values.append((subject, f"field.{field.casefold()}.code", code, "code"))
        for key, value in _CONFIG_RE.findall(content):
            values.append((system, f"config.{key.casefold()}", value, "configuration"))
        for subject in _COMPLETE_SCOPE_RE.findall(content):
            values.append(
                (canonical_value(subject, aliases), "schema.complete", "true", "scope")
            )
        for regex, property_name, value_type in (
            (_VERSION_RE, "api_version", "version"),
            (_SCHEMA_VERSION_RE, "schema_version", "version"),
            (_ENVIRONMENT_RE, "environment", "environment"),
            (_DEPLOYMENT_RE, "deployment_revision", "revision"),
            (_TEST_RE, "test_status", "status"),
            (_OWNER_RE, "owner", "role"),
            (_CONTROL_RE, "control", "control"),
        ):
            for match in regex.findall(content):
                values.append((system, property_name, str(match), value_type))
        for subject, property_name, value, value_type in values:
            claim = _claim(
                subject=subject,
                property_name=property_name,
                value=value,
                value_type=value_type,
                environment=environment,
                version=version,
                language=language,
                document_kind=artifact.document_kind,
                citation=citation,
                impact_flags=flags,
            )
            claims.setdefault(claim.fingerprint, claim)
    return sorted(claims.values(), key=lambda value: value.claim_id)


def facts_from_erp_snapshot(
    snapshot: ERPMetadataSnapshot,
    *,
    connector_kind: ConnectorKind,
    connector_label: str,
    glossary_entries: list[EntityGlossaryEntry],
) -> list[ObservedSystemFact]:
    aliases = _alias_map(glossary_entries)
    citation = ConsistencyCitation(
        connector_id=snapshot.connector_id,
        source_kind=connector_kind,
        source_label=connector_label,
        source_reference=snapshot.source_reference,
        source_revision=snapshot.metadata_hash,
        environment=snapshot.environment,
        retrieved_at=snapshot.retrieved_at,
        freshness="current",
    )
    facts: list[ObservedSystemFact] = []

    def add(subject: str, property_name: str, value: str, value_type: str) -> None:
        clean_value = _clean(value)
        facts.append(
            ObservedSystemFact(
                fact_id=_digest(
                    f"{snapshot.snapshot_id}\0{subject}\0{property_name}\0{clean_value}"
                ),
                canonical_subject=subject,
                canonical_property=property_name,
                observed_value=clean_value,
                observed_value_sha256=_digest(clean_value),
                value_type=value_type,
                environment=snapshot.environment,
                version=snapshot.api_version,
                persistence=ObservationPersistence.metadata_safe,
                scope_complete=True,
                citation=citation,
            )
        )

    system = canonical_value(connector_label, aliases)
    if snapshot.api_version:
        add(system, "api_version", snapshot.api_version, "version")
    if snapshot.environment:
        add(system, "environment", snapshot.environment, "environment")
    for entity in snapshot.entities:
        subject = canonical_value(entity.name, aliases)
        add(subject, "entity.exists", "true", "boolean")
        for field in entity.fields:
            prefix = f"field.{field.name.casefold()}"
            add(subject, f"{prefix}.exists", "true", "boolean")
            add(subject, f"{prefix}.type", field.data_type, "type")
            add(subject, f"{prefix}.required", str(field.required).lower(), "boolean")
            add(subject, f"{prefix}.key", str(field.key).lower(), "boolean")
            for code in field.code_values:
                add(subject, f"{prefix}.code", code, "code")
    return facts


def facts_from_observed_artifacts(
    *,
    artifacts: list[DocumentArtifact],
    sections: list[ArtifactSection],
    connector_labels: dict[str, str],
    glossary_entries: list[EntityGlossaryEntry],
    language: str,
    environment_filter: str | None = None,
    leased_connector_ids: set[str] | None = None,
) -> list[ObservedSystemFact]:
    claims = extract_document_claims(
        artifacts=artifacts,
        sections=sections,
        connector_labels=connector_labels,
        glossary_entries=glossary_entries,
        language=language,
        environment_filter=environment_filter,
    )
    leased = leased_connector_ids or set()
    facts: list[ObservedSystemFact] = []
    for claim in claims:
        live_or_leased = claim.citation.connector_id in leased
        value = None if live_or_leased else claim.expected_value
        facts.append(
            ObservedSystemFact(
                fact_id=_digest(f"observed\0{claim.claim_id}"),
                canonical_subject=claim.canonical_subject,
                canonical_property=claim.canonical_property,
                observed_value=value,
                observed_value_sha256=claim.expected_value_sha256,
                value_type=claim.value_type,
                environment=claim.environment,
                version=claim.version,
                persistence=(
                    ObservationPersistence.encrypted_lease
                    if live_or_leased
                    else ObservationPersistence.metadata_safe
                ),
                scope_complete=True,
                citation=claim.citation,
            )
        )
    return facts


def _policy_for(
    claim: DocumentClaim, policies: list[AuthorityPolicy]
) -> AuthorityPolicy | None:
    matches = [
        policy
        for policy in policies
        if policy.enabled
        and fnmatch.fnmatchcase(claim.canonical_subject, policy.subject_pattern.casefold())
        and fnmatch.fnmatchcase(claim.canonical_property, policy.property_pattern.casefold())
        and (policy.environment is None or policy.environment == claim.environment)
    ]
    if not matches:
        return None
    return min(
        matches,
        key=lambda value: (
            value.priority,
            value.subject_pattern.count("*") + value.property_pattern.count("*"),
            value.policy_id,
        ),
    )


def _mismatch_kind(claim: DocumentClaim, *, missing: bool = False) -> ConsistencyMismatchKind:
    property_name = claim.canonical_property
    if missing:
        if property_name == "entity.exists":
            return ConsistencyMismatchKind.missing_entity
        if property_name.startswith("field."):
            return ConsistencyMismatchKind.missing_field
        if property_name == "test_status":
            return ConsistencyMismatchKind.missing_test
        if property_name == "deployment_revision":
            return ConsistencyMismatchKind.missing_deployment
    if property_name.endswith(".type"):
        return ConsistencyMismatchKind.type_mismatch
    if property_name.endswith(".required"):
        return ConsistencyMismatchKind.requiredness_mismatch
    if property_name.endswith(".key"):
        return ConsistencyMismatchKind.key_mismatch
    if property_name.endswith(".code"):
        return ConsistencyMismatchKind.code_list_mismatch
    if property_name == "api_version":
        return ConsistencyMismatchKind.api_version_drift
    if property_name == "schema_version":
        return ConsistencyMismatchKind.schema_version_drift
    if property_name == "environment":
        return ConsistencyMismatchKind.environment_drift
    if property_name == "deployment_revision":
        return ConsistencyMismatchKind.deployment_lag
    if property_name.startswith("config."):
        return ConsistencyMismatchKind.configuration_drift
    if property_name == "owner":
        return ConsistencyMismatchKind.ownership_conflict
    if property_name == "control":
        return ConsistencyMismatchKind.control_conflict
    return ConsistencyMismatchKind.value_mismatch


def _severity(claim: DocumentClaim, kind: ConsistencyMismatchKind) -> AssuranceSeverity:
    flags = set(claim.impact_flags)
    if "production" in flags and flags & {"security", "regulatory", "financial_close"}:
        return AssuranceSeverity.critical
    if flags or kind in {
        ConsistencyMismatchKind.missing_entity,
        ConsistencyMismatchKind.missing_field,
        ConsistencyMismatchKind.api_version_drift,
        ConsistencyMismatchKind.schema_version_drift,
        ConsistencyMismatchKind.control_conflict,
    }:
        return AssuranceSeverity.high
    return AssuranceSeverity.warning


def _attribution(
    kind: ConsistencyMismatchKind, policy: AuthorityPolicy | None
) -> ConsistencyAttribution:
    if policy is None or policy.authority is AuthoritySide.neutral:
        return ConsistencyAttribution.neutral
    if kind in {
        ConsistencyMismatchKind.environment_drift,
        ConsistencyMismatchKind.configuration_drift,
    }:
        return ConsistencyAttribution.configuration_drift
    if kind in {
        ConsistencyMismatchKind.missing_deployment,
        ConsistencyMismatchKind.deployment_lag,
        ConsistencyMismatchKind.api_version_drift,
        ConsistencyMismatchKind.schema_version_drift,
    }:
        return ConsistencyAttribution.release_drift
    if policy.authority is AuthoritySide.observed_system:
        return ConsistencyAttribution.documentation_drift
    return ConsistencyAttribution.implementation_drift


def _explanation(
    claim: DocumentClaim,
    fact: ObservedSystemFact,
    kind: ConsistencyMismatchKind,
    attribution: ConsistencyAttribution,
) -> tuple[str, str, str]:
    expected = claim.expected_value or f"hash:{claim.expected_value_sha256[:12]}"
    observed = fact.observed_value or f"hash:{fact.observed_value_sha256[:12]}"
    if claim.language == "ja":
        explanation = (
            f"文書では「{expected}」ですが、選択されたシステムでは「{observed}」です。"
            f"判定は {kind.value}、帰属は {attribution.value} です。"
        )
        impact = "設計、設定、テスト、運用手順が誤った前提に依存している可能性があります。"
        verify = "両方の版、対象環境、承認済みの正規情報源を確認してください。"
    elif claim.language == "ko":
        explanation = (
            f"문서에는 '{expected}'로 되어 있지만 선택한 시스템에서는 "
            f"'{observed}'로 확인됩니다. 비교 규칙은 {kind.value}, "
            f"분류는 {attribution.value}입니다."
        )
        impact = "설계, 구성, 테스트 또는 운영 절차가 잘못된 전제에 의존할 수 있습니다."
        verify = "양쪽 리비전, 대상 환경 및 승인된 기준 정보 정책을 확인하십시오."
    else:
        explanation = (
            f"Documentation states '{expected}', while the selected system exposes "
            f"'{observed}'. The rule is {kind.value}; attribution is {attribution.value}."
        )
        impact = (
            "Design, configuration, tests, or operating procedures may rely on a wrong "
            "assumption."
        )
        verify = "Verify both revisions, the target environment, and the reviewed authority policy."
    return explanation, impact, verify


def _absence_fact(claim: DocumentClaim, scope: ConsistencyCitation) -> ObservedSystemFact:
    value = "not_observed"
    return ObservedSystemFact(
        fact_id=_digest(f"absence\0{claim.claim_id}\0{scope.source_revision}"),
        canonical_subject=claim.canonical_subject,
        canonical_property=claim.canonical_property,
        observed_value=value,
        observed_value_sha256=_digest(value),
        value_type=claim.value_type,
        environment=claim.environment,
        version=claim.version,
        persistence=ObservationPersistence.metadata_safe,
        scope_complete=True,
        citation=scope,
    )


def compare_claims_to_observations(
    *,
    run_id: str,
    claims: list[DocumentClaim],
    observations: list[ObservedSystemFact],
    policies: list[AuthorityPolicy],
    exceptions: list[ApprovedException],
    observed_scope_citations: list[ConsistencyCitation],
    detect_undocumented: bool = True,
) -> list[ConsistencyFinding]:
    grouped: dict[tuple[str, str], list[ObservedSystemFact]] = defaultdict(list)
    for fact in observations:
        grouped[(fact.canonical_subject, fact.canonical_property)].append(fact)
    active_exceptions = {
        value.finding_fingerprint: value
        for value in exceptions
        if value.expires_at > datetime.now(UTC)
    }
    findings: list[ConsistencyFinding] = []
    complete_document_subjects = {
        claim.canonical_subject
        for claim in claims
        if claim.canonical_property == "schema.complete"
        and claim.status is ClaimStatus.confirmed
    }
    for claim in claims:
        if (
            claim.status is not ClaimStatus.confirmed
            or claim.canonical_property == "schema.complete"
        ):
            continue
        candidates = grouped.get((claim.canonical_subject, claim.canonical_property), [])
        environment_conflicts = [
            value
            for value in candidates
            if claim.environment
            and value.environment
            and claim.environment != value.environment
        ]
        compatible = [
            value
            for value in candidates
            if not claim.environment
            or not value.environment
            or claim.environment == value.environment
        ]
        version_ambiguous = bool(
            claim.canonical_property
            not in {"api_version", "schema_version", "deployment_revision"}
            and
            claim.version
            and compatible
            and all(value.version and value.version != claim.version for value in compatible)
        )
        missing = not compatible
        if missing and environment_conflicts:
            compatible = [environment_conflicts[0]]
            missing = False
            kind = ConsistencyMismatchKind.environment_drift
        elif missing:
            subject_scope = [
                value.citation
                for value in observations
                if value.canonical_subject == claim.canonical_subject
                and (
                    not claim.environment
                    or not value.environment
                    or value.environment == claim.environment
                )
            ]
            candidate_scope = subject_scope or observed_scope_citations
            unique_scope = {
                (value.connector_id, value.source_revision): value
                for value in candidate_scope
            }
            if not unique_scope:
                continue
            compatible = [
                _absence_fact(claim, unique_scope[key]) for key in sorted(unique_scope)
            ]
            kind = (
                ConsistencyMismatchKind.ambiguous_match
                if len(compatible) > 1
                else _mismatch_kind(claim, missing=True)
            )
        else:
            kind = _mismatch_kind(claim)
        if version_ambiguous:
            kind = ConsistencyMismatchKind.ambiguous_match
        exact = [
            value
            for value in compatible
            if value.observed_value_sha256 == claim.expected_value_sha256
        ]
        if exact and not version_ambiguous:
            continue
        distinct = {value.observed_value_sha256 for value in compatible}
        if len(distinct) > 1 and not claim.canonical_property.endswith(".code"):
            kind = ConsistencyMismatchKind.ambiguous_match
        fact = sorted(compatible, key=lambda value: value.fact_id)[0]
        policy = _policy_for(claim, policies)
        attribution = (
            ConsistencyAttribution.ambiguous
            if kind is ConsistencyMismatchKind.ambiguous_match
            else _attribution(kind, policy)
        )
        fingerprint = _digest(
            "\0".join(
                (
                    claim.fingerprint,
                    fact.canonical_subject,
                    fact.canonical_property,
                    fact.observed_value_sha256,
                    kind.value,
                )
            )
        )
        status = ConsistencyFindingStatus.open
        if fingerprint in active_exceptions:
            attribution = ConsistencyAttribution.approved_exception
            status = ConsistencyFindingStatus.approved_exception
        explanation, impact, verify = _explanation(claim, fact, kind, attribution)
        comparison = ConsistencyComparison(
            expected_value=claim.expected_value,
            expected_value_sha256=claim.expected_value_sha256,
            observed_value=fact.observed_value,
            observed_value_sha256=fact.observed_value_sha256,
            comparison_rule=kind.value,
            authority_policy_id=policy.policy_id if policy else None,
            attribution=attribution,
            explanation=explanation,
            likely_impact=impact,
            recommended_verification=verify,
        )
        findings.append(
            ConsistencyFinding(
                finding_id=_digest(
                    f"{fingerprint}\0{claim.citation.source_revision}\0"
                    f"{fact.citation.source_revision}"
                ),
                fingerprint=fingerprint,
                run_id=run_id,
                mismatch_kind=kind,
                severity=_severity(claim, kind),
                status=status,
                subject=claim.canonical_subject,
                property_name=claim.canonical_property,
                document_claim=claim,
                observed_fact=fact,
                comparison=comparison,
            )
        )
    if detect_undocumented and claims:
        documented_keys = {
            (claim.canonical_subject, claim.canonical_property) for claim in claims
        }
        scope_revision = _digest(
            "\0".join(sorted({claim.citation.source_revision for claim in claims}))
        )
        scope_citation = claims[0].citation.model_copy(
            update={
                "source_reference": "Selected approved documentation scope",
                "source_revision": scope_revision,
                "locator": ArtifactLocator(),
            }
        )
        seen_observations: set[tuple[str, str, str]] = set()
        for fact in observations:
            key = (fact.canonical_subject, fact.canonical_property)
            unique = (*key, fact.observed_value_sha256)
            if (
                key in documented_keys
                or unique in seen_observations
                or not fact.scope_complete
                or fact.canonical_subject not in complete_document_subjects
            ):
                continue
            seen_observations.add(unique)
            synthetic_claim = _claim(
                subject=fact.canonical_subject,
                property_name=fact.canonical_property,
                value="not_documented",
                value_type=fact.value_type,
                environment=fact.environment,
                version=fact.version,
                language=claims[0].language,
                document_kind=claims[0].document_kind,
                citation=scope_citation,
                impact_flags=[],
            )
            kind = ConsistencyMismatchKind.undocumented_implementation
            policy = _policy_for(synthetic_claim, policies)
            attribution = _attribution(kind, policy)
            fingerprint = _digest(
                "\0".join(
                    (
                        synthetic_claim.fingerprint,
                        fact.observed_value_sha256,
                        kind.value,
                    )
                )
            )
            status = ConsistencyFindingStatus.open
            if fingerprint in active_exceptions:
                status = ConsistencyFindingStatus.approved_exception
                attribution = ConsistencyAttribution.approved_exception
            explanation, impact, verify = _explanation(
                synthetic_claim, fact, kind, attribution
            )
            findings.append(
                ConsistencyFinding(
                    finding_id=_digest(
                        f"{fingerprint}\0{scope_revision}\0"
                        f"{fact.citation.source_revision}"
                    ),
                    fingerprint=fingerprint,
                    run_id=run_id,
                    mismatch_kind=kind,
                    severity=_severity(synthetic_claim, kind),
                    status=status,
                    subject=fact.canonical_subject,
                    property_name=fact.canonical_property,
                    document_claim=synthetic_claim,
                    observed_fact=fact,
                    comparison=ConsistencyComparison(
                        expected_value=synthetic_claim.expected_value,
                        expected_value_sha256=synthetic_claim.expected_value_sha256,
                        observed_value=fact.observed_value,
                        observed_value_sha256=fact.observed_value_sha256,
                        comparison_rule=kind.value,
                        authority_policy_id=policy.policy_id if policy else None,
                        attribution=attribution,
                        explanation=explanation,
                        likely_impact=impact,
                        recommended_verification=verify,
                    ),
                )
            )
    return sorted(findings, key=lambda value: value.finding_id)


def review_finding(
    finding: ConsistencyFinding,
    request: ConsistencyFindingReviewRequest,
) -> tuple[ConsistencyFinding, ApprovedException | None]:
    if finding.revision != request.expected_revision:
        raise RuntimeError("stale_consistency_finding")
    statuses = {
        "confirm": ConsistencyFindingStatus.confirmed,
        "dismiss": ConsistencyFindingStatus.dismissed,
        "resolve": ConsistencyFindingStatus.resolved,
        "reopen": ConsistencyFindingStatus.open,
        "classify": finding.status,
        "assign": finding.status,
        "request_refresh": ConsistencyFindingStatus.open,
        "mark_exception": ConsistencyFindingStatus.approved_exception,
    }
    comparison = finding.comparison
    if request.action == "classify" and request.attribution is not None:
        comparison = comparison.model_copy(update={"attribution": request.attribution})
    exception = None
    if request.action == "mark_exception":
        assert request.exception_reason is not None
        assert request.exception_expires_at is not None
        exception = ApprovedException(
            exception_id=_digest(
                f"{finding.fingerprint}\0{request.exception_expires_at.isoformat()}"
            ),
            finding_fingerprint=finding.fingerprint,
            reason=request.exception_reason,
            owner_role=request.owner_role,
            expires_at=request.exception_expires_at,
        )
        comparison = comparison.model_copy(
            update={"attribution": ConsistencyAttribution.approved_exception}
        )
    return (
        finding.model_copy(
            update={
                "status": statuses[request.action],
                "comparison": comparison,
                "owner_role": request.owner_role or finding.owner_role,
                "revision": finding.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        ),
        exception,
    )


def review_claim(claim: DocumentClaim, expected_revision: int, action: str) -> DocumentClaim:
    if claim.revision != expected_revision:
        raise RuntimeError("stale_consistency_claim")
    if claim.origin is not ClaimOrigin.semantic_suggested:
        raise ValueError("only semantic suggestions require claim review")
    return claim.model_copy(
        update={
            "origin": (
                ClaimOrigin.semantic_confirmed if action == "confirm" else claim.origin
            ),
            "status": ClaimStatus.confirmed if action == "confirm" else ClaimStatus.dismissed,
            "revision": claim.revision + 1,
        }
    )


def render_consistency_export(
    finding: ConsistencyFinding, request: ConsistencyExportRequest
) -> ConsistencyExport:
    if finding.status not in {
        ConsistencyFindingStatus.confirmed,
        ConsistencyFindingStatus.approved_exception,
    }:
        raise PermissionError("consistency finding must be reviewed before export")
    summary = f"[{request.draft_kind}] {finding.subject}: {finding.property_name}"
    description = "\n".join(
        (
            finding.comparison.explanation,
            f"Document: {finding.document_claim.citation.source_reference} "
            f"@ {finding.document_claim.citation.source_revision}",
            f"System: {finding.observed_fact.citation.source_reference} "
            f"@ {finding.observed_fact.citation.source_revision}",
            f"Impact: {finding.comparison.likely_impact}",
            f"Verify: {finding.comparison.recommended_verification}",
        )
    )
    if request.format == "canonical_json":
        payload = finding.model_dump_json(indent=2).encode()
        filename = f"consistency-{finding.finding_id[:12]}.json"
        media_type = "application/json"
    elif request.format in {"jira_csv", "azure_boards_csv"}:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\r\n")

        def safe(value: str) -> str:
            return f"'{value}" if value.startswith(("=", "+", "-", "@")) else value

        if request.format == "jira_csv":
            writer.writerow(["Summary", "Description", "Work Type", "Labels"])
            writer.writerow([safe(summary), safe(description), "Task", "consistency-review"])
            filename = f"consistency-{finding.finding_id[:12]}-jira.csv"
        else:
            writer.writerow(["Work Item Type", "Title", "Description", "Tags"])
            writer.writerow(["Task", safe(summary), safe(description), "consistency-review"])
            filename = f"consistency-{finding.finding_id[:12]}-azure.csv"
        payload = b"\xef\xbb\xbf" + stream.getvalue().encode()
        media_type = "text/csv"
    else:
        payload = (
            f"# {summary}\n\n{description}\n\n"
            f"Status: {finding.status.value}\nAttribution: "
            f"{finding.comparison.attribution.value}\n"
        ).encode()
        filename = f"consistency-{finding.finding_id[:12]}.md"
        media_type = "text/markdown"
    return ConsistencyExport(
        filename=filename,
        media_type=media_type,
        payload_base64=base64.b64encode(payload).decode(),
    )
