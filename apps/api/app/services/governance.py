"""Pure deterministic governance candidate and projection logic."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime, timedelta

from app.domain.context import EntityGlossaryEntry
from app.domain.contracts import MeetingState, TranscriptEvent
from app.domain.governance import (
    ActionPayload,
    ArchitectureImpactPayload,
    AssumptionPayload,
    ConstraintPayload,
    DecisionPayload,
    DependencyPayload,
    FieldProvenance,
    GovernanceAlert,
    GovernanceCandidate,
    GovernanceCandidateStatus,
    GovernanceEvent,
    GovernanceEvidenceRef,
    GovernanceKind,
    GovernancePayload,
    GovernanceRecord,
    GovernanceRecordStatus,
    ResponsibilityPayload,
    RiskPayload,
    SolutionPortfolio,
)

WORKSPACE_ID = "local-personal"

_QUESTION_START = re.compile(
    r"^(?:who|what|when|where|why|how|do|does|did|can|could|would|should|is|are|"
    r"誰|何|いつ|どこ|なぜ|どう|どの|でしょうか|ですか|"
    r"누가|무엇|언제|어디|왜|어떻게|어떤)",
    re.IGNORECASE,
)
_HYPOTHETICAL = re.compile(
    r"(?:\bif\b|\bwhat if\b|\bwould\b|\bcould hypothetically\b|"
    r"もし|仮に|たとえば|만약|가령|예를 들어)",
    re.IGNORECASE,
)
_QUOTED = re.compile(r"^(?:[\"'“‘「『]).*(?:[\"'”’」』])$")
_NEGATED_GOVERNANCE = re.compile(
    r"(?:\b(?:did not|didn't|have not|haven't|not)\s+(?:decide|agree|depend|commit)\b|"
    r"\bno\s+(?:risk|dependency|constraint|action item)\b|"
    r"(?:決定していません|合意していません|リスクはありません|依存していません|"
    r"ブロックされていません|制約ではありません|担当ではありません|"
    r"결정하지 않았습니다|합의하지 않았습니다|리스크가 없습니다|의존하지 않습니다|"
    r"차단되지 않았습니다|제약이 아닙니다|담당이 아닙니다))",
    re.IGNORECASE,
)
_REPORTED_SPEECH = re.compile(
    r"(?:\b(?:said|told us|quoted)\b.*\b(?:decided|agreed|risk|action)\b|"
    r"(?:決定|合意|リスク|対応).*(?:と言いました|との発言)|"
    r"(?:결정|합의|리스크|조치).*(?:라고 말했습니다|라는 발언))",
    re.IGNORECASE,
)

_KIND_PATTERNS: dict[str, dict[GovernanceKind, tuple[re.Pattern[str], ...]]] = {
    "en": {
        GovernanceKind.decision: (
            re.compile(r"\b(?:we|the team|steering committee)\s+(?:decided|agreed)\b", re.I),
            re.compile(r"\bthe decision is\b", re.I),
        ),
        GovernanceKind.risk: (
            re.compile(r"\b(?:the|a|our)\s+risk\s+(?:is|remains)\b", re.I),
            re.compile(r"\bwe have a risk\b", re.I),
        ),
        GovernanceKind.dependency: (
            re.compile(r"\b(?:depends on|is dependent on|blocked by|waiting for)\b", re.I),
            re.compile(r"\bdependency\s+(?:is|on)\b", re.I),
        ),
        GovernanceKind.action: (
            re.compile(r"\baction item\b", re.I),
            re.compile(
                r"\b(?:i|we|[A-Z][a-z]+)\s+will\s+(?:prepare|confirm|review|send|update|investigate|document|schedule)\b"
            ),
        ),
        GovernanceKind.assumption: (
            re.compile(r"\b(?:we assume|our assumption is|the assumption is)\b", re.I),
        ),
        GovernanceKind.constraint: (
            re.compile(
                r"\b(?:the constraint is|we must use|cannot be changed|is mandatory)\b", re.I
            ),
        ),
        GovernanceKind.architecture_impact: (
            re.compile(
                r"\b(?:architecture impact|affects? the architecture|"
                r"impacts? (?:our )?(?:systems?|interfaces?|data flow))\b",
                re.I,
            ),
        ),
        GovernanceKind.responsibility: (
            re.compile(
                r"\b(?:is responsible for|is accountable for|"
                r"owns this process|ultimate owner is)\b",
                re.I,
            ),
        ),
    },
    "ja": {
        GovernanceKind.decision: (
            re.compile(r"(?:決定しました|決めました|合意しました|決定事項は)"),
        ),
        GovernanceKind.risk: (
            re.compile(r"(?:リスクは|リスクがあります|懸念があります|懸念点は)"),
        ),
        GovernanceKind.dependency: (
            re.compile(
                r"(?:に依存しています|が前提です|待ちです|でブロックされています|依存関係は)"
            ),
        ),
        GovernanceKind.action: (
            re.compile(
                r"(?:アクション(?:項目)?は|対応します|確認します|調査します|整理します|共有します)"
            ),
        ),
        GovernanceKind.assumption: (
            re.compile(r"(?:前提は|前提として|という前提です|と仮定します)"),
        ),
        GovernanceKind.constraint: (
            re.compile(r"(?:制約は|必須です|変更できません|使用しなければなりません)"),
        ),
        GovernanceKind.architecture_impact: (
            re.compile(
                r"(?:アーキテクチャへの影響|システム構成に影響|データフローに影響|インターフェースに影響)"
            ),
        ),
        GovernanceKind.responsibility: (
            re.compile(r"(?:が責任者です|が担当します|が最終責任を持ちます|がオーナーです)"),
        ),
    },
    "ko": {
        GovernanceKind.decision: (re.compile(r"(?:결정했습니다|합의했습니다|결정 사항은|결론은)"),),
        GovernanceKind.risk: (re.compile(r"(?:리스크는|위험은|리스크가 있습니다|우려 사항은)"),),
        GovernanceKind.dependency: (
            re.compile(
                r"(?:에 의존하고 있습니다|가 전제입니다|대기 중입니다|때문에 차단되었습니다)"
            ),
        ),
        GovernanceKind.action: (
            re.compile(
                r"(?:조치 항목은|대응하겠습니다|확인하겠습니다|조사하겠습니다|공유하겠습니다)"
            ),
        ),
        GovernanceKind.assumption: (
            re.compile(r"(?:전제는|전제로 합니다|라고 가정합니다|가정은)"),
        ),
        GovernanceKind.constraint: (
            re.compile(r"(?:제약은|필수입니다|변경할 수 없습니다|사용해야 합니다)"),
        ),
        GovernanceKind.architecture_impact: (
            re.compile(
                r"(?:아키텍처에 영향|시스템 구성에 영향|데이터 흐름에 영향|인터페이스에 영향)"
            ),
        ),
        GovernanceKind.responsibility: (
            re.compile(r"(?:가 책임자입니다|가 담당합니다|가 최종 책임을 집니다|가 오너입니다)"),
        ),
    },
}


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def _digest(*values: str, length: int = 24) -> str:
    return hashlib.sha256("\0".join(values).encode()).hexdigest()[:length]


def _is_guarded(event: TranscriptEvent) -> bool:
    text = event.text.strip()
    if not text or text.endswith(("?", "？")) or _QUESTION_START.search(text):
        return True
    if (
        _HYPOTHETICAL.search(text)
        or _QUOTED.fullmatch(text)
        or _NEGATED_GOVERNANCE.search(text)
        or _REPORTED_SPEECH.search(text)
    ):
        return True
    return False


def _payload(kind: GovernanceKind, text: str, entities: list[str]) -> GovernancePayload:
    if kind is GovernanceKind.decision:
        return DecisionPayload(statement=text)
    if kind is GovernanceKind.risk:
        return RiskPayload(statement=text)
    if kind is GovernanceKind.dependency:
        return DependencyPayload(statement=text)
    if kind is GovernanceKind.action:
        return ActionPayload(task=text)
    if kind is GovernanceKind.assumption:
        return AssumptionPayload(statement=text)
    if kind is GovernanceKind.constraint:
        return ConstraintPayload(statement=text, affected_scope=entities)
    if kind is GovernanceKind.architecture_impact:
        return ArchitectureImpactPayload(change=text, affected_entities=entities)
    return ResponsibilityPayload(subject=text)


def _glossary_entities(
    text: str, glossary: list[EntityGlossaryEntry]
) -> tuple[list[str], list[str]]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    matches: list[tuple[int, int, str, str]] = []
    for entry in glossary:
        if not entry.enabled:
            continue
        for alias in [entry.canonical_name, *entry.aliases]:
            needle = unicodedata.normalize("NFKC", alias).casefold().strip()
            if not needle:
                continue
            found = normalized.find(needle)
            if found >= 0:
                matches.append((len(needle), found, entry.entry_id, entry.canonical_name))
    matches.sort(key=lambda value: (-value[0], value[1], value[2]))
    selected: list[tuple[int, int, str, str]] = []
    occupied: list[tuple[int, int]] = []
    for length, start, entry_id, name in matches:
        end = start + length
        if any(start < other_end and end > other_start for other_start, other_end in occupied):
            continue
        occupied.append((start, end))
        selected.append((length, start, entry_id, name))
    selected.sort(key=lambda value: (value[1], value[2]))
    return (
        list(dict.fromkeys(value[2] for value in selected)),
        list(dict.fromkeys(value[3] for value in selected)),
    )


def detect_governance_candidates(
    event: TranscriptEvent,
    state: MeetingState,
    glossary: list[EntityGlossaryEntry] | None = None,
) -> list[GovernanceCandidate]:
    """Return deterministic proposals. Nothing here mutates authoritative state."""

    if not event.is_final or _is_guarded(event):
        return []
    language = event.lang.value
    rules = _KIND_PATTERNS.get(language, {})
    linked_entity_ids, entity_names = _glossary_entities(event.text, glossary or [])
    linked_pain_ids = [
        pain.pain_id for pain in state.pain_points if event.event_id in pain.evidence_event_ids
    ]
    candidates: list[GovernanceCandidate] = []
    normalized_text = _normalize(event.text)
    for kind, patterns in rules.items():
        if not any(pattern.search(event.text) for pattern in patterns):
            continue
        fingerprint = f"gf-{_digest(kind.value, normalized_text)}"
        candidate_id = f"gc-{_digest(event.meeting_id, kind.value, normalized_text)}"
        title_prefix = {
            "ja": {
                GovernanceKind.decision: "決定",
                GovernanceKind.risk: "リスク",
                GovernanceKind.dependency: "依存関係",
                GovernanceKind.action: "アクション",
                GovernanceKind.assumption: "前提",
                GovernanceKind.constraint: "制約",
                GovernanceKind.architecture_impact: "アーキテクチャ影響",
                GovernanceKind.responsibility: "責任分担",
            },
            "en": {
                GovernanceKind.decision: "Decision",
                GovernanceKind.risk: "Risk",
                GovernanceKind.dependency: "Dependency",
                GovernanceKind.action: "Action",
                GovernanceKind.assumption: "Assumption",
                GovernanceKind.constraint: "Constraint",
                GovernanceKind.architecture_impact: "Architecture impact",
                GovernanceKind.responsibility: "Responsibility",
            },
            "ko": {
                GovernanceKind.decision: "결정",
                GovernanceKind.risk: "리스크",
                GovernanceKind.dependency: "의존성",
                GovernanceKind.action: "조치",
                GovernanceKind.assumption: "전제",
                GovernanceKind.constraint: "제약",
                GovernanceKind.architecture_impact: "아키텍처 영향",
                GovernanceKind.responsibility: "책임",
            },
        }[language][kind]
        statement = " ".join(event.text.split())
        candidates.append(
            GovernanceCandidate(
                candidate_id=candidate_id,
                workspace_id=WORKSPACE_ID,
                session_id=event.meeting_id,
                state_version=state.version,
                kind=kind,
                title=f"{title_prefix}: {statement[:180]}",
                payload=_payload(kind, statement, entity_names),
                language=language,
                fingerprint=fingerprint,
                evidence_event_ids=[event.event_id],
                linked_pain_ids=linked_pain_ids,
                linked_entity_ids=linked_entity_ids,
                created_seq=event.seq,
            )
        )
    return candidates


_PAYLOAD_TYPES = {
    GovernanceKind.decision: DecisionPayload,
    GovernanceKind.risk: RiskPayload,
    GovernanceKind.dependency: DependencyPayload,
    GovernanceKind.action: ActionPayload,
    GovernanceKind.assumption: AssumptionPayload,
    GovernanceKind.constraint: ConstraintPayload,
    GovernanceKind.architecture_impact: ArchitectureImpactPayload,
    GovernanceKind.responsibility: ResponsibilityPayload,
}


def confirm_candidate(
    candidate: GovernanceCandidate,
    edits: dict[str, object] | None = None,
    existing: GovernanceRecord | None = None,
) -> tuple[GovernanceCandidate, GovernanceRecord, GovernanceEvent]:
    values = (existing.payload if existing is not None else candidate.payload).model_dump(
        mode="json"
    )
    changed_fields: list[str] = []
    for key, value in (edits or {}).items():
        if key == "payload_type":
            continue
        values[key] = value
        changed_fields.append(key)
    payload = _PAYLOAD_TYPES[candidate.kind].model_validate(values)
    record_id = f"gr-{_digest(candidate.workspace_id, candidate.fingerprint)}"
    provenance = {
        key: (FieldProvenance.user if key in changed_fields else FieldProvenance.transcript)
        for key in payload.model_fields_set
        if key != "payload_type"
    }
    now = datetime.now(UTC)
    if existing is not None:
        provenance = dict(existing.field_provenance)
        provenance.update({key: FieldProvenance.user for key in changed_fields})
        evidence = list(existing.evidence)
        if candidate.session_id not in existing.source_session_ids:
            evidence.append(
                GovernanceEvidenceRef(
                    session_id=candidate.session_id,
                    evidence_event_ids=candidate.evidence_event_ids,
                )
            )
        revision = existing.revision + 1
        record = existing.model_copy(
            update={
                "payload": payload,
                "revision": revision,
                "field_provenance": provenance,
                "evidence": evidence,
                "source_session_ids": list(
                    dict.fromkeys([*existing.source_session_ids, candidate.session_id])
                ),
                "linked_pain_ids": list(
                    dict.fromkeys([*existing.linked_pain_ids, *candidate.linked_pain_ids])
                ),
                "linked_entity_ids": list(
                    dict.fromkeys([*existing.linked_entity_ids, *candidate.linked_entity_ids])
                ),
                "updated_at": now,
            }
        )
        event = GovernanceEvent(
            event_id=f"ge-{_digest(record_id, 'linked', str(revision))}",
            record_id=record_id,
            revision=revision,
            action="linked",
            changed_fields=[
                "source_session_ids",
                "evidence",
                "linked_pain_ids",
                "linked_entity_ids",
                *changed_fields,
            ],
            provenance=(FieldProvenance.user if changed_fields else FieldProvenance.transcript),
            created_at=now,
        )
        return (
            candidate.model_copy(update={"status": GovernanceCandidateStatus.confirmed}),
            record,
            event,
        )
    record = GovernanceRecord(
        record_id=record_id,
        workspace_id=candidate.workspace_id,
        kind=candidate.kind,
        title=candidate.title,
        payload=payload,
        field_provenance=provenance,
        evidence=[
            {
                "session_id": candidate.session_id,
                "evidence_event_ids": candidate.evidence_event_ids,
            }
        ],
        source_session_ids=[candidate.session_id],
        linked_pain_ids=candidate.linked_pain_ids,
        linked_entity_ids=candidate.linked_entity_ids,
        created_at=now,
        updated_at=now,
    )
    event = GovernanceEvent(
        event_id=f"ge-{_digest(record_id, 'created', '1')}",
        record_id=record_id,
        revision=1,
        action="created",
        changed_fields=sorted(provenance),
        provenance=(FieldProvenance.user if changed_fields else FieldProvenance.transcript),
        created_at=now,
    )
    return (
        candidate.model_copy(update={"status": GovernanceCandidateStatus.confirmed}),
        record,
        event,
    )


def update_record(
    record: GovernanceRecord,
    action: str,
    changes: dict[str, object],
) -> tuple[GovernanceRecord, GovernanceEvent]:
    revision = record.revision + 1
    now = datetime.now(UTC)
    changed_fields: list[str] = []
    update: dict[str, object] = {"revision": revision, "updated_at": now}
    if action == "update":
        payload_values = record.payload.model_dump(mode="json")
        for key, value in changes.items():
            if key == "payload_type":
                continue
            payload_values[key] = value
            changed_fields.append(key)
        update["payload"] = _PAYLOAD_TYPES[record.kind].model_validate(payload_values)
        provenance = dict(record.field_provenance)
        provenance.update({key: FieldProvenance.user for key in changed_fields})
        update["field_provenance"] = provenance
        event_action = "updated"
    elif action == "status":
        status = GovernanceRecordStatus(str(changes.get("status")))
        update["status"] = status
        changed_fields = ["status"]
        event_action = "status_changed"
    elif action == "supersede":
        replacement = str(changes.get("superseded_by", "")).strip()
        if not replacement:
            raise ValueError("superseded_by is required")
        update.update({"status": GovernanceRecordStatus.superseded, "superseded_by": replacement})
        changed_fields = ["status", "superseded_by"]
        event_action = "superseded"
    elif action in {"link", "unlink"}:
        entity_id = str(changes.get("entity_id", "")).strip()
        if not entity_id:
            raise ValueError("entity_id is required")
        entities = list(record.linked_entity_ids)
        if action == "link" and entity_id not in entities:
            entities.append(entity_id)
        if action == "unlink":
            entities = [value for value in entities if value != entity_id]
        update["linked_entity_ids"] = entities
        changed_fields = ["linked_entity_ids"]
        event_action = "linked" if action == "link" else "unlinked"
    else:
        raise ValueError("unsupported governance action")
    updated = record.model_copy(update=update)
    event = GovernanceEvent(
        event_id=f"ge-{_digest(record.record_id, event_action, str(revision))}",
        record_id=record.record_id,
        revision=revision,
        action=event_action,
        changed_fields=changed_fields,
        provenance=FieldProvenance.user,
        created_at=now,
    )
    return updated, event


def build_portfolio(records: list[GovernanceRecord]) -> SolutionPortfolio:
    now = datetime.now(UTC)
    alerts: list[GovernanceAlert] = []
    counts: dict[str, int] = {}
    for record in records:
        counts[record.kind.value] = counts.get(record.kind.value, 0) + 1
        payload = record.payload.model_dump(mode="json")
        if record.status in {GovernanceRecordStatus.open, GovernanceRecordStatus.monitoring}:
            owner_fields = [
                payload.get("owner"),
                payload.get("approver"),
                payload.get("accountable"),
            ]
            if record.kind in {
                GovernanceKind.risk,
                GovernanceKind.dependency,
                GovernanceKind.action,
            } and not any(owner_fields):
                alerts.append(
                    GovernanceAlert(
                        alert_id=f"ga-{_digest(record.record_id, 'ownerless')}",
                        record_id=record.record_id,
                        kind="ownerless",
                        severity="warning",
                        label=f"Owner needed: {record.title[:180]}",
                    )
                )
            due_value = (
                payload.get("due_date") or payload.get("needed_by") or payload.get("target_date")
            )
            due_date = _parse_governance_date(due_value)
            if due_date is not None and due_date < now:
                alerts.append(
                    GovernanceAlert(
                        alert_id=f"ga-{_digest(record.record_id, 'overdue')}",
                        record_id=record.record_id,
                        kind="overdue",
                        severity="critical"
                        if record.kind is GovernanceKind.dependency
                        else "warning",
                        label=f"Overdue: {record.title[:190]}",
                    )
                )
            if (
                record.kind is GovernanceKind.dependency
                and due_date is not None
                and due_date <= now + timedelta(days=7)
                and not payload.get("fallback")
            ):
                alerts.append(
                    GovernanceAlert(
                        alert_id=f"ga-{_digest(record.record_id, 'critical-dependency')}",
                        record_id=record.record_id,
                        kind="critical_dependency",
                        severity="critical",
                        label=f"Dependency needs a fallback: {record.title[:170]}",
                    )
                )
            if record.kind is GovernanceKind.decision and record.created_at < now - timedelta(
                days=30
            ):
                alerts.append(
                    GovernanceAlert(
                        alert_id=f"ga-{_digest(record.record_id, 'aging-decision')}",
                        record_id=record.record_id,
                        kind="aging_decision",
                        severity="warning",
                        label=f"Decision needs review: {record.title[:180]}",
                    )
                )
        if record.needs_provenance_review:
            alerts.append(
                GovernanceAlert(
                    alert_id=f"ga-{_digest(record.record_id, 'provenance')}",
                    record_id=record.record_id,
                    kind="provenance_review",
                    severity="warning",
                    label=f"Evidence review needed: {record.title[:170]}",
                )
            )
    return SolutionPortfolio(records=records[:500], alerts=alerts[:100], counts=counts)


def _parse_governance_date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
