"""Composition service for local confirmed solution governance."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from app.domain.context import EntityGlossaryEntry
from app.domain.contracts import MeetingState
from app.domain.governance import (
    GovernanceCandidate,
    GovernanceCandidateStatus,
    GovernanceKind,
    GovernanceRecord,
    ImpactAnalysis,
    ImpactPath,
    SolutionBrief,
    SolutionPortfolio,
    SystemEntity,
    SystemEntityKind,
    SystemRelationship,
)
from app.domain.ports import GovernanceRepository
from app.services.governance import (
    build_portfolio,
    confirm_candidate,
    detect_governance_candidates,
    update_record,
)


def _digest(*values: str) -> str:
    return hashlib.sha256("\0".join(values).encode()).hexdigest()[:24]


_ENTITY_KIND = {
    "system": SystemEntityKind.system,
    "interface": SystemEntityKind.interface,
    "process": SystemEntityKind.process,
    "business_object": SystemEntityKind.data_object,
    "business_capability": SystemEntityKind.business_capability,
    "team": SystemEntityKind.team,
    "region": SystemEntityKind.region,
    "environment": SystemEntityKind.environment,
    "standard": SystemEntityKind.standard,
    "architecture_pattern": SystemEntityKind.architecture_pattern,
}


class GovernanceWorkspace:
    def __init__(
        self,
        repository: GovernanceRepository,
        glossary_provider: Callable[[], list[EntityGlossaryEntry]] | None = None,
    ) -> None:
        self._repository = repository
        self._glossary_provider = glossary_provider
        self._observed_seq: dict[str, int] = {}

    def set_glossary_provider(
        self, provider: Callable[[], list[EntityGlossaryEntry]] | None
    ) -> None:
        self._glossary_provider = provider

    def _glossary(self) -> list[EntityGlossaryEntry]:
        if self._glossary_provider is None:
            return []
        try:
            return self._glossary_provider()
        except Exception:
            return []

    def observe_state(self, state: MeetingState) -> list[GovernanceCandidate]:
        last_seq = self._observed_seq.get(state.meeting_id, -1)
        created: list[GovernanceCandidate] = []
        for event in state.transcript:
            if event.seq <= last_seq:
                continue
            for candidate in detect_governance_candidates(event, state, self._glossary()):
                created.append(self._repository.save_candidate(candidate))
            last_seq = max(last_seq, event.seq)
        self._observed_seq[state.meeting_id] = last_seq
        return created

    def candidates(
        self, session_id: str, *, pending_only: bool = False
    ) -> list[GovernanceCandidate]:
        values = self._repository.candidates(session_id)
        if pending_only:
            values = [
                value for value in values if value.status is GovernanceCandidateStatus.pending
            ]
        return values

    def review_candidate(
        self,
        candidate_id: str,
        session_id: str,
        state_version: int,
        action: str,
        edits: dict[str, object] | None = None,
    ) -> tuple[GovernanceCandidate, GovernanceRecord | None]:
        candidate = self._repository.candidate(candidate_id)
        if candidate is None or candidate.session_id != session_id:
            raise KeyError(candidate_id)
        if candidate.state_version > state_version:
            raise RuntimeError("stale governance candidate")
        if candidate.status is not GovernanceCandidateStatus.pending:
            existing = next(
                (
                    record
                    for record in self._repository.records()
                    if record.record_id.endswith(
                        _digest(candidate.workspace_id, candidate.fingerprint)
                    )
                ),
                None,
            )
            return candidate, existing
        if action == "dismiss":
            dismissed = candidate.model_copy(update={"status": GovernanceCandidateStatus.dismissed})
            self._repository.save_review(dismissed, None, None)
            return dismissed, None
        if action != "confirm":
            raise ValueError("unsupported governance candidate review")
        record_id = f"gr-{_digest(candidate.workspace_id, candidate.fingerprint)}"
        confirmed, record, event = confirm_candidate(
            candidate, edits, self._repository.record(record_id)
        )
        self._repository.save_review(confirmed, record, event)
        return confirmed, record

    def records(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        entity_id: str | None = None,
    ) -> list[GovernanceRecord]:
        records = self._repository.records()
        if kind is not None:
            records = [record for record in records if record.kind.value == kind]
        if status is not None:
            records = [record for record in records if record.status.value == status]
        if entity_id is not None:
            records = [record for record in records if entity_id in record.linked_entity_ids]
        return records

    def change_record(
        self,
        record_id: str,
        expected_revision: int,
        action: str,
        changes: dict[str, object],
    ) -> GovernanceRecord:
        record = self._repository.record(record_id)
        if record is None:
            raise KeyError(record_id)
        if record.revision != expected_revision:
            raise RuntimeError("stale governance record")
        updated, event = update_record(record, action, changes)
        self._repository.save_record_event(updated, event)
        return updated

    def portfolio(self) -> SolutionPortfolio:
        return build_portfolio(self._repository.records())

    def brief(self, language: str, entity_ids: list[str], limit: int) -> SolutionBrief:
        records = self._repository.records()
        if entity_ids:
            records = [
                record for record in records if set(record.linked_entity_ids) & set(entity_ids)
            ]
        portfolio = build_portfolio(records)
        return SolutionBrief(
            language=language, records=portfolio.records[:limit], alerts=portfolio.alerts
        )

    def _entities(self) -> dict[str, SystemEntity]:
        values: dict[str, SystemEntity] = {}
        for entry in self._glossary():
            kind = _ENTITY_KIND.get(entry.kind.value)
            if kind is None:
                continue
            values[entry.entry_id] = SystemEntity(
                entity_id=entry.entry_id,
                kind=kind,
                label=entry.canonical_name,
                aliases=entry.aliases,
            )
        return values

    def impact_analysis(self, root_entity_id: str) -> ImpactAnalysis:
        entities = self._entities()
        if root_entity_id not in entities:
            raise KeyError(root_entity_id)
        relationships: list[SystemRelationship] = []
        by_name = {entity.label.casefold(): entity.entity_id for entity in entities.values()}
        for record in self._repository.records():
            if record.kind is not GovernanceKind.dependency:
                continue
            payload = record.payload.model_dump(mode="json")
            provider = by_name.get(str(payload.get("provider") or "").casefold())
            consumer = by_name.get(str(payload.get("consumer") or "").casefold())
            if not provider or not consumer:
                continue
            relationships.append(
                SystemRelationship(
                    relationship_id=f"rel-{_digest(provider, consumer, 'depends_on')}",
                    source_entity_id=consumer,
                    target_entity_id=provider,
                    relation="depends_on",
                    confirmed=True,
                    evidence=record.evidence,
                )
            )
        adjacency: dict[str, list[SystemRelationship]] = {}
        for relationship in relationships:
            adjacency.setdefault(relationship.source_entity_id, []).append(relationship)
            adjacency.setdefault(relationship.target_entity_id, []).append(relationship)
        paths: list[ImpactPath] = [ImpactPath(entity_ids=[root_entity_id])]
        seen_paths = {(root_entity_id,)}
        frontier = [(root_entity_id, [root_entity_id], [])]
        while frontier:
            current, entity_path, relation_path = frontier.pop(0)
            if len(relation_path) == 2:
                continue
            for relationship in adjacency.get(current, []):
                other = (
                    relationship.target_entity_id
                    if relationship.source_entity_id == current
                    else relationship.source_entity_id
                )
                next_entities = [*entity_path, other]
                key = tuple(next_entities)
                if other in entity_path or key in seen_paths:
                    continue
                seen_paths.add(key)
                next_relations = [*relation_path, relationship.relationship_id]
                paths.append(ImpactPath(entity_ids=next_entities, relationship_ids=next_relations))
                frontier.append((other, next_entities, next_relations))
        impacted_ids = {entity_id for path in paths for entity_id in path.entity_ids}
        used_relationship_ids = {
            relationship_id for path in paths for relationship_id in path.relationship_ids
        }
        return ImpactAnalysis(
            root_entity_id=root_entity_id,
            entities=[entities[value] for value in sorted(impacted_ids)],
            relationships=[
                value for value in relationships if value.relationship_id in used_relationship_ids
            ],
            paths=paths,
        )

    def delete_meeting(self, session_id: str) -> None:
        self._repository.delete_meeting(session_id)
        self._observed_seq.pop(session_id, None)

    def delete_all(self) -> None:
        self._repository.delete_all()
        self._observed_seq.clear()

    def close(self) -> None:
        self._repository.close()
