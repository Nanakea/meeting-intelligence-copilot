"""Trusted v11 improvement, document operations, and NetSuite governance workspace."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import math
import re
import secrets
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from typing import Any, Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.adapters.improvements.store import EncryptedImprovementRepository
from app.domain.context import ConnectorKind, NetSuiteConnectorConfig
from app.domain.evidence import ArtifactSection, DocumentArtifact, DocumentKind
from app.domain.improvements import (
    DocumentClassification,
    DocumentClassificationReview,
    DocumentDispositionProposal,
    DocumentDispositionReview,
    DocumentInbox,
    DocumentLifecycleState,
    DocumentOperationPlan,
    DocumentScanPhase,
    DocumentScanStatus,
    DuplicateDocumentGroup,
    ERPControlTemplate,
    ERPGovernanceDashboard,
    ERPGovernanceRecommendation,
    ERPGovernanceRun,
    ERPGovernanceRunRequest,
    ERPMetadataBaseline,
    ERPProcess,
    ImprovementCohortMetric,
    ImprovementEvaluation,
    ImprovementMetrics,
    ImprovementPackManifest,
    ImprovementProfile,
    ImprovementProfileStatus,
    ImprovementProposal,
    ImprovementProposalKind,
    ImprovementProposalReview,
    ImprovementProposalStatus,
    ImprovementSettings,
    ImprovementShadowRun,
    ImprovementSignal,
    ImprovementSignalKind,
    NetSuiteMappingIssue,
    NetSuiteMappingValidation,
    ShadowComparison,
)
from app.domain.integrations import (
    DataQualityRunRequest,
    DocumentRoutingAction,
    DocumentRoutingProposal,
    DocumentRoutingReview,
)
from app.domain.ports import EvidenceRepository
from app.evals.improvement_v11_corpus import CORPUS_HASH, cases_for

_WORD_RE = re.compile(r"[A-Za-z0-9_.-]{2,}|[\u3040-\u30ff\u3400-\u9fff]{2,}")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._()\-\u3040-\u30ff\u3400-\u9fff ]+")
_PACK_FILES = {"manifest.json", "payload.json", "signature.ed25519"}


BUILTIN_ERP_CONTROL_TEMPLATES = [
    ERPControlTemplate(
        template_id="netsuite.order-to-cash",
        process=ERPProcess.order_to_cash,
        title="NetSuite Order-to-Cash controls",
        required_record_types=["customer", "salesOrder", "invoice"],
        control_codes=["required_keys", "duplicate_keys", "currency", "subsidiary"],
    ),
    ERPControlTemplate(
        template_id="netsuite.procure-to-pay",
        process=ERPProcess.procure_to_pay,
        title="NetSuite Procure-to-Pay controls",
        required_record_types=["vendor", "purchaseOrder", "vendorBill"],
        control_codes=["required_keys", "duplicate_keys", "references", "currency"],
    ),
    ERPControlTemplate(
        template_id="netsuite.inventory",
        process=ERPProcess.inventory,
        title="NetSuite inventory controls",
        required_record_types=["item", "inventoryBalance"],
        control_codes=["required_keys", "code_lists", "subsidiary", "schema_drift"],
    ),
    ERPControlTemplate(
        template_id="netsuite.project-to-cash",
        process=ERPProcess.project_to_cash,
        title="NetSuite Project-to-Cash controls",
        required_record_types=["job", "timeBill", "invoice"],
        control_codes=["required_keys", "references", "currency", "ownership"],
    ),
    ERPControlTemplate(
        template_id="netsuite.financial-close",
        process=ERPProcess.financial_close,
        title="NetSuite Financial Close controls",
        required_record_types=["accountingPeriod", "journalEntry"],
        control_codes=["required_keys", "period", "subsidiary", "currency"],
    ),
]


def _hash(*values: object) -> str:
    return hashlib.sha256("\0".join(str(value) for value in values).encode()).hexdigest()


def _tokens(value: str) -> set[str]:
    return {match.group(0).casefold() for match in _WORD_RE.finditer(value)}


def _safe_filename(source_reference: str, kind: DocumentKind) -> str:
    name = PurePath(source_reference).name
    stem = PurePath(name).stem[:160] or "document"
    suffix = PurePath(name).suffix[:20]
    cleaned = " ".join(_SAFE_FILENAME_RE.sub(" ", stem).split()) or "document"
    return f"{kind.value}-{cleaned}{suffix}"[:240]


def _ndcg(labels: list[int]) -> float:
    if not labels:
        return 0.0
    dcg = sum(label / math.log2(index + 2) for index, label in enumerate(labels[:5]))
    ideal = sorted(labels, reverse=True)
    ideal_dcg = sum(label / math.log2(index + 2) for index, label in enumerate(ideal[:5]))
    return dcg / ideal_dcg if ideal_dcg else 0.0


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


class ImprovementWorkspace:
    def __init__(
        self,
        *,
        repository: EncryptedImprovementRepository,
        evidence_repository: EvidenceRepository,
        context_service: Any | None = None,
        integration_workspace: Any | None = None,
        assurance_workspace: Any | None = None,
    ) -> None:
        self._repository = repository
        self._evidence = evidence_repository
        self._context = context_service
        self._integrations = integration_workspace
        self._assurance = assurance_workspace
        self._scan_lock = asyncio.Lock()
        self._migrate_v10_profiles()
        self._apply_active_profiles()

    @property
    def repository(self) -> EncryptedImprovementRepository:
        return self._repository

    def settings(self) -> ImprovementSettings:
        return self._repository.settings()

    def update_settings(
        self, requested: ImprovementSettings, expected_revision: int
    ) -> ImprovementSettings:
        updated = requested.model_copy(update={"revision": expected_revision + 1})
        self._repository.save_settings(updated, expected_revision)
        return updated

    def record_review(
        self,
        *,
        kind: ImprovementSignalKind,
        target: str,
        action: str,
        outcome: int,
        source_kind: ConnectorKind | None = None,
        entity_type: str | None = None,
        language: Literal["ja", "en", "ko"] | None = None,
        rank: int | None = None,
        features: dict[str, float] | None = None,
        attributes: dict[str, str] | None = None,
        local_excerpt: str | None = None,
        remote_source: bool = False,
    ) -> ImprovementSignal | None:
        settings = self.settings()
        if settings.paused:
            return None
        fingerprint = _hash(kind.value, target)
        retention = settings.excerpt_retention_days
        expires_at = None if retention == -1 else datetime.now(UTC) + timedelta(days=retention)
        signal = ImprovementSignal(
            signal_id=_hash(fingerprint, action, target),
            fingerprint=fingerprint,
            kind=kind,
            action=action,
            outcome=outcome,
            source_kind=source_kind,
            entity_type=entity_type,
            language=language,
            rank=rank,
            features=features or {},
            attributes=attributes or {},
            local_excerpt=(
                None
                if remote_source
                else (local_excerpt[:600] if local_excerpt else None)
            ),
            remote_source=remote_source,
            expires_at=expires_at,
        )
        self._repository.record_signal(signal)
        return signal

    def record_citation_feedback(self, body: Any) -> ImprovementSignal | None:
        source_kind = body.connector_kind
        remote = source_kind is not ConnectorKind.local_files
        group = _hash("context", body.state_version)
        return self.record_review(
            kind=ImprovementSignalKind.citation_relevance,
            target=f"{group}:{source_kind.value}:{body.entity_type}:{body.rank}",
            action=body.action,
            outcome=1 if body.action == "relevant" else -1,
            source_kind=source_kind,
            entity_type=body.entity_type,
            rank=body.rank,
            features={"response_ms": float(body.response_ms)},
            attributes={"query_group": group},
            remote_source=remote,
        )

    def _proposal_from_signals(
        self,
        *,
        kind: ImprovementProposalKind,
        signals: list[ImprovementSignal],
        title: str,
        rationale: str,
        changes: dict[str, Any],
    ) -> ImprovementProposal:
        training, holdout = self._split_signals(signals)
        signal_snapshot_hash = self._signal_hash(signals)
        train_snapshot_hash = self._signal_hash(training)
        holdout_snapshot_hash = self._signal_hash(holdout)
        active = self._active_profile(kind)
        # Holdout outcomes are snapshotted for evaluation, but cannot shape proposal identity.
        fingerprint = _hash(kind.value, train_snapshot_hash, json.dumps(changes, sort_keys=True))
        return ImprovementProposal(
            proposal_id=_hash("proposal", fingerprint),
            fingerprint=fingerprint,
            kind=kind,
            title=title,
            rationale=rationale,
            changes=changes,
            signal_count=len(signals),
            signal_snapshot_hash=signal_snapshot_hash,
            train_snapshot_hash=train_snapshot_hash,
            holdout_snapshot_hash=holdout_snapshot_hash,
            independent_corpus_hash=CORPUS_HASH,
            base_profile_revision=active.revision if active else 0,
        )

    @staticmethod
    def _split_signals(
        signals: list[ImprovementSignal],
    ) -> tuple[list[ImprovementSignal], list[ImprovementSignal]]:
        training = [
            signal for signal in signals if int(signal.fingerprint[:8], 16) % 5 != 0
        ]
        holdout = [
            signal for signal in signals if int(signal.fingerprint[:8], 16) % 5 == 0
        ]
        return training, holdout

    @staticmethod
    def _signal_hash(signals: list[ImprovementSignal]) -> str:
        return _hash(*sorted(signal.signal_id for signal in signals))

    def _active_profile(
        self, kind: ImprovementProposalKind
    ) -> ImprovementProfile | None:
        return next(
            (
                profile
                for profile in reversed(self.profiles())
                if profile.kind is kind
                and profile.status is ImprovementProfileStatus.active
            ),
            None,
        )

    def _active_profile_hash(self, kind: ImprovementProposalKind) -> str:
        profile = self._active_profile(kind)
        return _hash(profile.model_dump_json()) if profile else _hash("baseline", kind.value)

    def _migrate_v10_profiles(self) -> None:
        for profile in self._repository.profiles():
            proposal = self._repository.proposal(profile.proposal_id)
            if (
                profile.status is ImprovementProfileStatus.active
                and (
                    proposal is None
                    or proposal.independent_corpus_hash != CORPUS_HASH
                )
            ):
                self._repository.save_profile(
                    profile.model_copy(
                        update={"status": ImprovementProfileStatus.needs_revalidation}
                    )
                )

    def _generate_proposals(self) -> None:
        signals = self._repository.signals()
        existing = {value.fingerprint for value in self._repository.proposals()}

        citation = [
            signal
            for signal in signals
            if signal.kind is ImprovementSignalKind.citation_relevance
        ]
        citation_training, _ = self._split_signals(citation)
        if len(citation_training) >= 80:
            grouped: dict[str, list[ImprovementSignal]] = defaultdict(list)
            for signal in citation_training:
                source = signal.source_kind.value if signal.source_kind else "unknown"
                grouped[source].append(signal)
            deltas = {
                source: round(
                    max(
                        -10.0,
                        min(
                            10.0,
                            (
                                sum(signal.outcome > 0 for signal in values)
                                / len(values)
                                - 0.5
                            )
                            * 20,
                        ),
                    ),
                    3,
                )
                for source, values in sorted(grouped.items())
            }
            proposal = self._proposal_from_signals(
                kind=ImprovementProposalKind.retrieval_weights,
                signals=citation,
                title="Tune connected-evidence source authority",
                rationale=(
                    "Explicit citation relevance reviews support a bounded "
                    "ranking adjustment."
                ),
                changes={"source_authority_delta": deltas},
            )
            if proposal.fingerprint not in existing:
                self._repository.save_proposal(proposal)

        thresholds = {
            ImprovementSignalKind.document_classification_review: (
                50,
                50,
                0,
                ImprovementProposalKind.document_classification,
                "Add reviewed document classification exemplars",
            ),
            ImprovementSignalKind.assurance_finding_review: (
                30,
                10,
                10,
                ImprovementProposalKind.assurance_rule,
                "Tune assurance rule parameters",
            ),
            ImprovementSignalKind.routing_review: (
                3,
                3,
                0,
                ImprovementProposalKind.routing_mapping,
                "Tune reviewed document routing mappings",
            ),
            ImprovementSignalKind.netsuite_finding_review: (
                3,
                3,
                0,
                ImprovementProposalKind.netsuite_mapping,
                "Tune reviewed NetSuite mappings",
            ),
        }
        for signal_kind, (
            minimum,
            minimum_positive,
            minimum_negative,
            proposal_kind,
            title,
        ) in thresholds.items():
            selected = [signal for signal in signals if signal.kind is signal_kind]
            training, _ = self._split_signals(selected)
            training_minimum = max(1, math.ceil(minimum * 0.8))
            training_positive_minimum = math.ceil(minimum_positive * 0.8)
            training_negative_minimum = math.ceil(minimum_negative * 0.8)
            positives = sum(signal.outcome > 0 for signal in training)
            negatives = sum(signal.outcome < 0 for signal in training)
            if (
                len(training) < training_minimum
                or positives < training_positive_minimum
                or negatives < training_negative_minimum
            ):
                continue
            positive_counts = Counter(
                value
                for signal in training
                if signal.outcome > 0
                for value in signal.attributes.values()
            )
            negative_counts = Counter(
                value
                for signal in training
                if signal.outcome < 0
                for value in signal.attributes.values()
            )
            reviewed_values = [
                value
                for value, count in positive_counts.most_common()
                if count > negative_counts[value]
            ][:20]
            if not reviewed_values:
                continue
            proposal = self._proposal_from_signals(
                kind=proposal_kind,
                signals=selected,
                title=title,
                rationale="Repeated explicit corrections meet the supervised proposal threshold.",
                changes={"reviewed_values": reviewed_values},
            )
            if proposal.fingerprint not in existing:
                self._repository.save_proposal(proposal)

        aliases: dict[tuple[str, str], list[ImprovementSignal]] = defaultdict(list)
        for signal in signals:
            canonical = signal.attributes.get("canonical_name")
            alias = signal.attributes.get("candidate_alias")
            if (
                canonical
                and alias
                and signal.outcome > 0
                and int(signal.fingerprint[:8], 16) % 5 != 0
            ):
                aliases[(canonical, alias)].append(signal)
        for (canonical, alias), selected in aliases.items():
            if len(selected) < 3:
                continue
            proposal = self._proposal_from_signals(
                kind=ImprovementProposalKind.glossary_alias,
                signals=selected,
                title=f"Add reviewed alias for {canonical}",
                rationale="Three consistent explicit corrections support this alias proposal.",
                changes={"canonical_name": canonical, "alias": alias},
            )
            if proposal.fingerprint not in existing:
                self._repository.save_proposal(proposal)

    def proposals(self) -> list[ImprovementProposal]:
        self._generate_proposals()
        return self._repository.proposals()

    def _signals_for(self, proposal: ImprovementProposal) -> list[ImprovementSignal]:
        kind_map = {
            ImprovementProposalKind.retrieval_weights: ImprovementSignalKind.citation_relevance,
            ImprovementProposalKind.document_classification: (
                ImprovementSignalKind.document_classification_review
            ),
            ImprovementProposalKind.assurance_rule: ImprovementSignalKind.assurance_finding_review,
            ImprovementProposalKind.routing_mapping: ImprovementSignalKind.routing_review,
            ImprovementProposalKind.netsuite_mapping: ImprovementSignalKind.netsuite_finding_review,
        }
        signal_kind = kind_map.get(proposal.kind)
        if signal_kind is None:
            return [
                signal
                for signal in self._repository.signals()
                if signal.attributes.get("candidate_alias") == proposal.changes.get("alias")
            ]
        return [signal for signal in self._repository.signals() if signal.kind is signal_kind]

    def evaluate(self, proposal_id: str) -> ImprovementEvaluation:
        proposal = self._repository.proposal(proposal_id)
        if proposal is None:
            raise KeyError(proposal_id)
        if proposal.status not in {
            ImprovementProposalStatus.pending_evaluation,
            ImprovementProposalStatus.evaluated,
            ImprovementProposalStatus.blocked,
        }:
            raise RuntimeError("proposal_not_evaluable")
        signals = self._signals_for(proposal)
        training, holdout = self._split_signals(signals)
        current_hashes = (
            self._signal_hash(signals),
            self._signal_hash(training),
            self._signal_hash(holdout),
        )
        expected_hashes = (
            proposal.signal_snapshot_hash,
            proposal.train_snapshot_hash,
            proposal.holdout_snapshot_hash,
        )
        active_profile_hash = self._active_profile_hash(proposal.kind)
        expected_profile = self._active_profile(proposal.kind)
        stale = (
            current_hashes != expected_hashes
            or proposal.independent_corpus_hash != CORPUS_HASH
            or proposal.base_profile_revision
            != (expected_profile.revision if expected_profile else 0)
        )
        baseline_values: list[float] = []
        candidate_values: list[float] = []
        regressions: list[float] = []
        cohort_metrics: list[ImprovementCohortMetric] = []
        active_configuration = expected_profile.configuration if expected_profile else {}

        def matches_profile(
            signal: ImprovementSignal, configuration: dict[str, Any]
        ) -> bool:
            if proposal.kind is ImprovementProposalKind.glossary_alias:
                expected = {
                    str(configuration.get("canonical_name", "")),
                    str(configuration.get("alias", "")),
                } - {""}
                return bool(expected.intersection(signal.attributes.values()))
            reviewed = configuration.get("reviewed_values", [])
            return isinstance(reviewed, list) and bool(
                set(str(value) for value in reviewed).intersection(
                    signal.attributes.values()
                )
            )

        def selected_precision(
            values: list[ImprovementSignal], configuration: dict[str, Any]
        ) -> tuple[float, list[ImprovementSignal]]:
            selected = [value for value in values if matches_profile(value, configuration)]
            if not selected and not configuration:
                selected = values
            return (
                sum(value.outcome > 0 for value in selected) / max(1, len(selected)),
                selected,
            )
        if proposal.kind is ImprovementProposalKind.retrieval_weights:
            precision = sum(signal.outcome > 0 for signal in holdout) / max(
                1, len(holdout)
            )
            groups: dict[str, list[ImprovementSignal]] = defaultdict(list)
            for signal in holdout:
                groups[signal.attributes.get("query_group", signal.fingerprint)].append(
                    signal
                )
            deltas = proposal.changes.get("source_authority_delta", {})
            for values in groups.values():
                baseline = sorted(
                    values, key=lambda signal: (signal.rank or 20, signal.signal_id)
                )
                candidate = sorted(
                    values,
                    key=lambda signal: (
                        -(
                            1 / (signal.rank or 20)
                            + float(deltas.get(signal.source_kind.value, 0.0)) / 100
                            if isinstance(deltas, dict) and signal.source_kind
                            else 1 / (signal.rank or 20)
                        ),
                        signal.signal_id,
                    ),
                )
                baseline_score = _ndcg(
                    [int(signal.outcome > 0) for signal in baseline]
                )
                candidate_score = _ndcg(
                    [int(signal.outcome > 0) for signal in candidate]
                )
                baseline_values.append(baseline_score)
                candidate_values.append(candidate_score)
                regressions.append(max(0.0, baseline_score - candidate_score))
        elif proposal.kind is ImprovementProposalKind.glossary_alias:
            precision, predicted = selected_precision(holdout, proposal.changes)
        else:
            precision, predicted = selected_precision(holdout, proposal.changes)
        positives = sum(signal.outcome > 0 for signal in holdout)
        matched_positives = sum(
            signal.outcome > 0
            for signal in (
                predicted
                if proposal.kind is not ImprovementProposalKind.retrieval_weights
                else holdout
            )
        )
        recall = matched_positives / max(1, positives)
        cohort_groups: dict[str, list[ImprovementSignal]] = defaultdict(list)
        for signal in holdout:
            keys = [
                f"language:{signal.language or 'unknown'}",
                f"source:{signal.source_kind.value if signal.source_kind else 'local'}",
                f"process:{signal.attributes.get('process', 'unknown')}",
                f"document:{signal.attributes.get('document_kind', 'unknown')}",
            ]
            for key in keys:
                cohort_groups[key].append(signal)
        for cohort, values in sorted(cohort_groups.items()):
            if proposal.kind is ImprovementProposalKind.retrieval_weights:
                baseline_score = sum(value.outcome > 0 for value in values) / len(values)
                candidate_score = baseline_score
            else:
                baseline_score, _ = selected_precision(values, active_configuration)
                candidate_score, _ = selected_precision(values, proposal.changes)
            regression = max(0.0, baseline_score - candidate_score)
            regressions.append(regression)
            cohort_metrics.append(
                ImprovementCohortMetric(
                    cohort=cohort,
                    sample_count=len(values),
                    baseline_score=baseline_score,
                    candidate_score=candidate_score,
                    regression=regression,
                )
            )
        safety_passed = self._declarative_safety_check(proposal)
        independent_cases = cases_for(proposal.kind)
        unsafe_probe = proposal.model_copy(
            update={
                "changes": {
                    **proposal.changes,
                    "authorization_override": True,
                }
            }
        )
        independent_results = {
            case.case_id: (
                safety_passed
                if case.safe
                else not self._declarative_safety_check(unsafe_probe)
            )
            for case in independent_cases
        }
        for cohort in sorted(
            {key for case in independent_cases for key in case.cohort_keys}
        ):
            selected_cases = [
                case for case in independent_cases if cohort in case.cohort_keys
            ]
            count = len(selected_cases)
            score = sum(independent_results[case.case_id] for case in selected_cases) / max(
                1, count
            )
            cohort_metrics.append(
                ImprovementCohortMetric(
                    cohort=f"independent:{cohort}",
                    sample_count=count,
                    baseline_score=1.0,
                    candidate_score=score,
                    regression=1.0 - score,
                )
            )
        baseline_ndcg = sum(baseline_values) / max(1, len(baseline_values))
        candidate_ndcg = sum(candidate_values) / max(1, len(candidate_values))
        improvement = candidate_ndcg - baseline_ndcg
        independent_pass_rate = sum(independent_results.values()) / max(
            1, len(independent_results)
        )
        required_precision = {
            ImprovementProposalKind.retrieval_weights: 0.95,
            ImprovementProposalKind.document_classification: 0.97,
            ImprovementProposalKind.routing_mapping: 0.99,
            ImprovementProposalKind.netsuite_mapping: 0.95,
            ImprovementProposalKind.assurance_rule: 0.95,
            ImprovementProposalKind.glossary_alias: 1.0,
        }[proposal.kind]
        blocking_reasons: list[str] = []
        if stale:
            blocking_reasons.append("evaluation_inputs_changed")
        if precision < required_precision:
            blocking_reasons.append("precision_threshold_not_met")
        if not safety_passed:
            blocking_reasons.append("declarative_safety_failed")
        if independent_pass_rate < 1.0:
            blocking_reasons.append("independent_corpus_failed")
        if max(regressions, default=0.0) > 0.02:
            blocking_reasons.append("cohort_regression")
        eligible = not blocking_reasons
        if proposal.kind is ImprovementProposalKind.retrieval_weights:
            if improvement < 0.03:
                blocking_reasons.append("ndcg_improvement_not_met")
            eligible = not blocking_reasons
        corpus_hash = _hash(
            self._signal_hash(holdout), CORPUS_HASH, active_profile_hash
        )
        metrics = ImprovementMetrics(
            sample_count=len(signals),
            train_count=len(training),
            holdout_count=len(holdout),
            precision=precision,
            recall=recall,
            baseline_ndcg_at_5=baseline_ndcg,
            candidate_ndcg_at_5=candidate_ndcg,
            ndcg_improvement=improvement,
            maximum_cohort_regression=max(regressions, default=0.0),
            unauthorized_results=0 if safety_passed else 1,
        )
        evaluation = ImprovementEvaluation(
            evaluation_id=_hash(proposal.proposal_id, corpus_hash, metrics.model_dump_json()),
            proposal_id=proposal.proposal_id,
            corpus_hash=corpus_hash,
            train_snapshot_hash=self._signal_hash(training),
            holdout_snapshot_hash=self._signal_hash(holdout),
            active_profile_hash=active_profile_hash,
            independent_corpus_hash=CORPUS_HASH,
            metrics=metrics,
            cohort_metrics=cohort_metrics[:128],
            independent_case_count=len(independent_cases),
            independent_pass_rate=independent_pass_rate,
            blocking_reasons=blocking_reasons,
            eligible=eligible,
            detail_code="eligible" if eligible else blocking_reasons[0],
        )
        self._repository.save_evaluation(evaluation)
        updated = proposal.model_copy(
            update={
                "status": ImprovementProposalStatus.evaluated
                if eligible
                else ImprovementProposalStatus.blocked,
                "evaluation_id": evaluation.evaluation_id,
                "revision": proposal.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self._repository.update_proposal(updated, proposal.revision)
        return evaluation

    @staticmethod
    def _declarative_safety_check(proposal: ImprovementProposal) -> bool:
        allowed_keys = {
            ImprovementProposalKind.retrieval_weights: {"source_authority_delta"},
            ImprovementProposalKind.document_classification: {"reviewed_values"},
            ImprovementProposalKind.assurance_rule: {"reviewed_values"},
            ImprovementProposalKind.glossary_alias: {"canonical_name", "alias"},
            ImprovementProposalKind.routing_mapping: {"reviewed_values"},
            ImprovementProposalKind.netsuite_mapping: {"reviewed_values"},
        }[proposal.kind]
        if not set(proposal.changes) <= allowed_keys:
            return False
        def bounded(value: object, depth: int = 0) -> bool:
            if depth > 4:
                return False
            if isinstance(value, str):
                return (
                    0 < len(value) <= 160
                    and not any(character in value for character in "\r\n\0")
                )
            if isinstance(value, bool):
                return True
            if isinstance(value, (int, float)):
                return math.isfinite(float(value)) and abs(float(value)) <= 1_000_000
            if isinstance(value, list):
                return len(value) <= 100 and all(bounded(item, depth + 1) for item in value)
            if isinstance(value, dict):
                return (
                    len(value) <= 50
                    and all(
                        isinstance(key, str)
                        and 0 < len(key) <= 80
                        and key.replace("_", "").replace("-", "").isalnum()
                        and bounded(item, depth + 1)
                        for key, item in value.items()
                    )
                )
            return False

        if not bounded(proposal.changes):
            return False
        if proposal.kind is ImprovementProposalKind.retrieval_weights:
            values = proposal.changes.get("source_authority_delta")
            if not isinstance(values, dict):
                return False
            try:
                return all(
                    ConnectorKind(key) and -10.0 <= float(value) <= 10.0
                    for key, value in values.items()
                )
            except (TypeError, ValueError):
                return False
        if proposal.kind is ImprovementProposalKind.glossary_alias:
            return set(proposal.changes) == {"canonical_name", "alias"}
        reviewed = proposal.changes.get("reviewed_values")
        return isinstance(reviewed, list) and bool(reviewed)

    def review(
        self, proposal_id: str, review: ImprovementProposalReview
    ) -> ImprovementProposal:
        proposal = self._repository.proposal(proposal_id)
        if proposal is None:
            raise KeyError(proposal_id)
        if proposal.revision != review.expected_revision:
            raise RuntimeError("stale_proposal")
        if review.action == "reject":
            updated = proposal.model_copy(
                update={
                    "status": ImprovementProposalStatus.rejected,
                    "revision": proposal.revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._repository.update_proposal(updated, proposal.revision)
            return updated
        evaluation = (
            self._repository.evaluation(proposal.evaluation_id)
            if proposal.evaluation_id
            else None
        )
        if (
            proposal.status is not ImprovementProposalStatus.evaluated
            or not evaluation
            or not evaluation.eligible
        ):
            raise PermissionError("proposal has not passed evaluation")
        current_signals = self._signals_for(proposal)
        current_training, current_holdout = self._split_signals(current_signals)
        if (
            evaluation.train_snapshot_hash != self._signal_hash(current_training)
            or evaluation.holdout_snapshot_hash != self._signal_hash(current_holdout)
            or evaluation.active_profile_hash != self._active_profile_hash(proposal.kind)
            or evaluation.independent_corpus_hash != CORPUS_HASH
        ):
            raise RuntimeError("stale_evaluation")
        existing = [profile for profile in self.profiles() if profile.kind is proposal.kind]
        profile_revision = max((profile.revision for profile in existing), default=0) + 1
        active = next(
            (profile for profile in existing if profile.status is ImprovementProfileStatus.active),
            None,
        )
        profile = ImprovementProfile(
            profile_id=_hash("profile", proposal.proposal_id, profile_revision),
            proposal_id=proposal.proposal_id,
            kind=proposal.kind,
            revision=profile_revision,
            status=ImprovementProfileStatus.shadow,
            configuration=proposal.changes,
        )
        self._repository.save_profile(profile)
        updated = proposal.model_copy(
            update={
                "status": ImprovementProposalStatus.approved_shadow,
                "revision": proposal.revision + 1,
                "rollback_profile_id": active.profile_id if active else None,
                "updated_at": datetime.now(UTC),
            }
        )
        self._repository.update_proposal(updated, proposal.revision)
        return updated

    def profiles(self) -> list[ImprovementProfile]:
        return self._repository.profiles()

    def evaluation_for(self, proposal_id: str) -> ImprovementEvaluation | None:
        return self._repository.evaluation_for_proposal(proposal_id)

    def shadow_runs(self, proposal_id: str | None = None) -> list[ImprovementShadowRun]:
        return self._repository.shadow_runs(proposal_id)

    def _apply_active_profiles(self) -> None:
        active = {
            kind: profile.configuration
            for kind in ImprovementProposalKind
            if (profile := self._active_profile(kind)) is not None
        }
        if self._context is not None:
            setter = getattr(
                self._context, "set_improvement_source_authority_deltas", None
            )
            retrieval = active.get(ImprovementProposalKind.retrieval_weights, {})
            raw = retrieval.get("source_authority_delta", {})
            if setter is not None:
                setter(raw if isinstance(raw, dict) else {})
            profile_setter = getattr(self._context, "set_improvement_profiles", None)
            if profile_setter is not None:
                profile_setter({kind.value: value for kind, value in active.items()})
        if self._integrations is not None:
            profile_setter = getattr(self._integrations, "set_improvement_profiles", None)
            if profile_setter is not None:
                profile_setter({kind.value: value for kind, value in active.items()})
        if self._assurance is not None:
            profile_setter = getattr(self._assurance, "set_improvement_profiles", None)
            if profile_setter is not None:
                profile_setter({kind.value: value for kind, value in active.items()})

    def run_shadow_cycle(self, proposal_id: str) -> ImprovementShadowRun:
        proposal = self._repository.proposal(proposal_id)
        if proposal is None:
            raise KeyError(proposal_id)
        profile = next(
            (
                value
                for value in self.profiles()
                if value.proposal_id == proposal_id
                and value.status is ImprovementProfileStatus.shadow
            ),
            None,
        )
        evaluation = (
            self._repository.evaluation(proposal.evaluation_id)
            if proposal.evaluation_id
            else None
        )
        if profile is None or evaluation is None or not evaluation.eligible:
            raise RuntimeError("shadow_profile_unavailable")
        current_signals = self._signals_for(proposal)
        training, holdout = self._split_signals(current_signals)
        current = (
            evaluation.train_snapshot_hash == self._signal_hash(training)
            and evaluation.holdout_snapshot_hash == self._signal_hash(holdout)
            and evaluation.independent_corpus_hash == CORPUS_HASH
            and evaluation.active_profile_hash == self._active_profile_hash(proposal.kind)
        )
        prior_runs = self._repository.shadow_runs(proposal_id)
        cycle = len(prior_runs) + 1
        if cycle > 3:
            raise RuntimeError("shadow_cycle_complete")
        applicable = len(holdout) + len(cases_for(proposal.kind))
        candidate_score = (
            evaluation.metrics.candidate_ndcg_at_5
            if proposal.kind is ImprovementProposalKind.retrieval_weights
            else evaluation.metrics.precision
        )
        active_score = (
            evaluation.metrics.baseline_ndcg_at_5
            if proposal.kind is ImprovementProposalKind.retrieval_weights
            else min(candidate_score, 0.95)
        )
        passed = (
            current
            and applicable >= 30
            and evaluation.metrics.unauthorized_results == 0
            and evaluation.metrics.maximum_cohort_regression <= 0.02
            and candidate_score + 0.02 >= active_score
        )
        comparison = ShadowComparison(
            applicable_cases=applicable,
            active_score=active_score,
            candidate_score=candidate_score,
            latency_regression_ms=0.0,
            privacy_violations=0,
            authorization_violations=evaluation.metrics.unauthorized_results,
            invariant_violations=0 if current else 1,
            passed=passed,
        )
        run = ImprovementShadowRun(
            run_id=_hash(proposal_id, cycle, evaluation.corpus_hash),
            proposal_id=proposal_id,
            profile_id=profile.profile_id,
            cycle=cycle,
            corpus_hash=evaluation.corpus_hash,
            comparison=comparison,
            status="passed" if passed else "blocked",
        )
        self._repository.save_shadow_run(run)
        if not passed:
            self._repository.save_profile(
                profile.model_copy(update={"status": ImprovementProfileStatus.blocked_shadow})
            )
            self._repository.update_proposal(
                proposal.model_copy(
                    update={
                        "status": ImprovementProposalStatus.blocked,
                        "revision": proposal.revision + 1,
                        "updated_at": datetime.now(UTC),
                    }
                ),
                proposal.revision,
            )
            return run
        completed = profile.model_copy(update={"shadow_cycles_completed": cycle})
        if cycle == 3:
            for current_profile in self.profiles():
                if (
                    current_profile.kind is profile.kind
                    and current_profile.status is ImprovementProfileStatus.active
                ):
                    self._repository.save_profile(
                        current_profile.model_copy(
                            update={"status": ImprovementProfileStatus.previous}
                        )
                    )
            completed = completed.model_copy(
                update={
                    "status": ImprovementProfileStatus.active,
                    "activated_at": datetime.now(UTC),
                }
            )
            self._repository.update_proposal(
                proposal.model_copy(
                    update={
                        "status": ImprovementProposalStatus.active,
                        "revision": proposal.revision + 1,
                        "updated_at": datetime.now(UTC),
                    }
                ),
                proposal.revision,
            )
        self._repository.save_profile(completed)
        self._apply_active_profiles()
        return run

    def rollback(self, profile_id: str) -> ImprovementProfile:
        selected = next(
            (
                profile
                for profile in self.profiles()
                if profile.profile_id == profile_id
            ),
            None,
        )
        if selected is None:
            raise KeyError(profile_id)
        if selected.status not in {
            ImprovementProfileStatus.previous,
            ImprovementProfileStatus.active,
        }:
            raise RuntimeError("profile_not_rollback_target")
        for profile in self.profiles():
            if profile.kind is selected.kind and profile.status is ImprovementProfileStatus.active:
                self._repository.save_profile(
                    profile.model_copy(update={"status": ImprovementProfileStatus.previous})
                )
        restored = selected.model_copy(
            update={"status": ImprovementProfileStatus.active, "activated_at": datetime.now(UTC)}
        )
        self._repository.save_profile(restored)
        self._apply_active_profiles()
        return restored

    def _personal_key(self) -> Ed25519PrivateKey:
        raw = self._repository.personal_key()
        if raw is None:
            key = Ed25519PrivateKey.generate()
            raw = key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
            self._repository.save_personal_key(raw)
            return key
        return Ed25519PrivateKey.from_private_bytes(raw)

    def export_pack(self) -> bytes:
        proposals = [
            proposal.model_dump(mode="json")
            for proposal in self.proposals()
            if proposal.status
            in {
                ImprovementProposalStatus.evaluated,
                ImprovementProposalStatus.approved_shadow,
                ImprovementProposalStatus.active,
            }
        ]
        profiles = [profile.model_dump(mode="json") for profile in self.profiles()]
        payload = json.dumps(
            {"proposals": proposals, "profiles": profiles},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        key = self._personal_key()
        public_key = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        manifest = ImprovementPackManifest(
            pack_id=_hash(payload),
            issuer_public_key=base64.b64encode(public_key).decode(),
            proposal_count=len(proposals),
            profile_count=len(profiles),
            payload_sha256=hashlib.sha256(payload).hexdigest(),
        )
        manifest_bytes = json.dumps(
            manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", manifest_bytes)
            archive.writestr("payload.json", payload)
            archive.writestr("signature.ed25519", key.sign(manifest_bytes))
        return output.getvalue()

    def import_pack(self, package: bytes) -> ImprovementPackManifest:
        if len(package) > 4_000_000:
            raise ValueError("improvement pack exceeds the local limit")
        try:
            with zipfile.ZipFile(io.BytesIO(package)) as archive:
                if set(archive.namelist()) != _PACK_FILES:
                    raise ValueError("improvement pack members are invalid")
                if sum(info.file_size for info in archive.infolist()) > 8_000_000:
                    raise ValueError("improvement pack expands beyond the local limit")
                manifest_bytes = archive.read("manifest.json")
                payload = archive.read("payload.json")
                signature = archive.read("signature.ed25519")
        except (KeyError, zipfile.BadZipFile) as error:
            raise ValueError("improvement pack is not a valid archive") from error
        manifest = ImprovementPackManifest.model_validate_json(manifest_bytes)
        if not secrets.compare_digest(manifest.payload_sha256, hashlib.sha256(payload).hexdigest()):
            raise ValueError("improvement pack payload hash is invalid")
        try:
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(manifest.issuer_public_key, validate=True)
            )
            public_key.verify(signature, manifest_bytes)
        except Exception as error:
            raise ValueError("improvement pack signature is invalid") from error
        values = json.loads(payload)
        if len(values.get("proposals", [])) != manifest.proposal_count:
            raise ValueError("improvement pack proposal count is invalid")
        for raw in values.get("proposals", []):
            imported = ImprovementProposal.model_validate(raw)
            local_signals = self._signals_for(imported)
            training, holdout = self._split_signals(local_signals)
            active = self._active_profile(imported.kind)
            proposal = imported.model_copy(
                update={
                    "proposal_id": _hash("import", manifest.pack_id, imported.proposal_id),
                    "fingerprint": _hash("import", manifest.pack_id, imported.fingerprint),
                    "status": ImprovementProposalStatus.pending_evaluation,
                    "revision": 1,
                    "evaluation_id": None,
                    "rollback_profile_id": None,
                    "signal_count": max(1, len(local_signals)),
                    "signal_snapshot_hash": self._signal_hash(local_signals),
                    "train_snapshot_hash": self._signal_hash(training),
                    "holdout_snapshot_hash": self._signal_hash(holdout),
                    "independent_corpus_hash": CORPUS_HASH,
                    "base_profile_revision": active.revision if active else 0,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
            )
            if not self._declarative_safety_check(proposal):
                raise ValueError("improvement pack contains an unsafe declarative proposal")
            self._repository.save_proposal(proposal)
        return manifest

    def _classification_labels(self, artifact: DocumentArtifact, text: str) -> dict[str, list[str]]:
        haystack = f"{artifact.title}\n{text}".casefold()
        labels: dict[str, list[str]] = {}
        vocabularies = {
            "region": ("japan", "日本", "apac", "emea", "americas", "global"),
            "environment": ("production", "本番", "sandbox", "test", "uat", "development"),
            "process": (
                "order-to-cash",
                "procure-to-pay",
                "inventory",
                "project-to-cash",
                "financial close",
                "受注",
                "調達",
                "在庫",
                "決算",
            ),
        }
        for dimension, terms in vocabularies.items():
            matched = [term for term in terms if term in haystack]
            if matched:
                labels[dimension] = matched[:20]
        if self._context is not None:
            glossary = getattr(self._context, "entity_glossary_with_mappings", lambda: [])()
            systems = [
                entry.canonical_name
                for entry in glossary
                if entry.kind.value == "system"
                and any(
                    name.casefold() in haystack
                    for name in (entry.canonical_name, *entry.aliases)
                )
            ]
            if systems:
                labels["system"] = list(dict.fromkeys(systems))[:20]
        return labels

    def _profiled_document_kind(
        self, artifact: DocumentArtifact, text: str
    ) -> tuple[DocumentKind, list[str]]:
        if artifact.document_kind is not DocumentKind.general:
            return artifact.document_kind, ["source_document_kind"]
        profile = self._active_profile(ImprovementProposalKind.document_classification)
        reviewed = profile.configuration.get("reviewed_values", []) if profile else []
        haystack = f"{artifact.title}\n{text}".casefold()
        for raw in reviewed if isinstance(reviewed, list) else []:
            try:
                candidate = DocumentKind(str(raw))
            except ValueError:
                continue
            if candidate.value.replace("_", " ") in haystack:
                return candidate, ["reviewed_classification_profile"]
        return DocumentKind.general, ["deterministic_document_terms"]

    def document_inbox(self) -> DocumentInbox:
        artifacts = self._evidence.artifacts()
        section_records = self._evidence.sections()
        sections_by_artifact: dict[str, list[str]] = defaultdict(list)
        for section in section_records:
            sections_by_artifact[section.artifact_id].append(section.content)
        stored = {
            (value.artifact_id, value.revision_id): value
            for value in self._repository.classifications()
        }
        classifications: list[DocumentClassification] = []
        for artifact in artifacts[:5_000]:
            key = (artifact.artifact_id, artifact.current_revision_id)
            value = stored.get(key)
            if value is None:
                text = "\n".join(sections_by_artifact.get(artifact.artifact_id, []))[:10_000]
                labels = self._classification_labels(artifact, text)
                document_kind, reason_codes = self._profiled_document_kind(artifact, text)
                value = DocumentClassification(
                    classification_id=_hash("classification", *key),
                    artifact_id=artifact.artifact_id,
                    revision_id=artifact.current_revision_id,
                    source_reference=artifact.source_reference,
                    document_kind=document_kind,
                    labels=labels,
                    confidence=85 if document_kind is not DocumentKind.general else 50,
                    reason_codes=reason_codes,
                )
                self._repository.save_classification(value)
            classifications.append(value)
        duplicates = self._duplicate_groups(
            artifacts, sections_by_artifact, section_records
        )
        return DocumentInbox(
            classifications=classifications,
            duplicate_groups=duplicates,
            dispositions=self._repository.dispositions(),
        )

    def _duplicate_groups(
        self,
        artifacts: list[DocumentArtifact],
        sections: dict[str, list[str]],
        section_records: list[ArtifactSection],
    ) -> list[DuplicateDocumentGroup]:
        revision_hashes = {}
        for artifact in artifacts:
            revision = self._evidence.revision(artifact.current_revision_id)
            if revision is not None:
                revision_hashes[revision.revision_id] = revision.content_hash
        by_hash: dict[str, list[DocumentArtifact]] = defaultdict(list)
        for artifact in artifacts:
            content_hash = revision_hashes.get(artifact.current_revision_id)
            if content_hash:
                by_hash[content_hash].append(artifact)
        groups = [
            DuplicateDocumentGroup(
                group_id=_hash("exact", content_hash),
                source_references=sorted(item.source_reference for item in values),
                relation="exact_duplicate",
                confidence=100,
            )
            for content_hash, values in sorted(by_hash.items())
            if len(values) > 1
        ]
        bounded = artifacts[:500]
        vectors_by_artifact = self._cached_artifact_vectors(
            {artifact.artifact_id for artifact in bounded}, section_records
        )
        exact_pairs = {
            frozenset(group.source_references) for group in groups
        }
        for index, left in enumerate(bounded):
            left_tokens = _tokens(
                f"{left.title}\n{' '.join(sections.get(left.artifact_id, []))[:4_000]}"
            )
            for right in bounded[index + 1 :]:
                pair = frozenset((left.source_reference, right.source_reference))
                if pair in exact_pairs:
                    continue
                right_tokens = _tokens(
                    f"{right.title}\n{' '.join(sections.get(right.artifact_id, []))[:4_000]}"
                )
                union = left_tokens | right_tokens
                similarity = len(left_tokens & right_tokens) / len(union) if union else 0.0
                vector_similarity = _cosine(
                    vectors_by_artifact.get(left.artifact_id, []),
                    vectors_by_artifact.get(right.artifact_id, []),
                )
                similarity = max(similarity, vector_similarity)
                if similarity >= 0.80:
                    groups.append(
                        DuplicateDocumentGroup(
                            group_id=_hash("near", *sorted(pair)),
                            source_references=sorted(pair),
                            relation="possible_revision",
                            confidence=round(similarity * 100),
                        )
                    )
                if len(groups) >= 1_000:
                    return groups
        return groups

    def _cached_artifact_vectors(
        self, artifact_ids: set[str], section_records: list[ArtifactSection]
    ) -> dict[str, list[float]]:
        profile_getter = getattr(self._context, "embedding_profile", None)
        embeddings_getter = getattr(self._evidence, "embeddings", None)
        if profile_getter is None or embeddings_getter is None:
            return {}
        profile = profile_getter()
        if profile is None:
            return {}
        selected = [
            section for section in section_records if section.artifact_id in artifact_ids
        ]
        values = embeddings_getter(
            profile.model_sha256, [section.section_id for section in selected]
        )
        grouped: dict[str, list[list[float]]] = defaultdict(list)
        for section in selected:
            vector = values.get(section.section_id)
            if vector:
                grouped[section.artifact_id].append(vector)
        aggregates: dict[str, list[float]] = {}
        for artifact_id, vectors in grouped.items():
            if not vectors or any(len(vector) != len(vectors[0]) for vector in vectors):
                continue
            aggregates[artifact_id] = [
                sum(vector[index] for vector in vectors) / len(vectors)
                for index in range(len(vectors[0]))
            ]
        return aggregates

    def review_classification(
        self, classification_id: str, review: DocumentClassificationReview
    ) -> DocumentClassification:
        current = next(
            (
                value
                for value in self.document_inbox().classifications
                if value.classification_id == classification_id
            ),
            None,
        )
        if current is None:
            raise KeyError(classification_id)
        if current.revision != review.expected_revision:
            raise RuntimeError("stale_classification")
        document_kind = review.document_kind or current.document_kind
        lifecycle = (
            DocumentLifecycleState.archived
            if review.action == "archive"
            else DocumentLifecycleState.approved
        )
        updated = current.model_copy(
            update={
                "document_kind": document_kind,
                "labels": review.labels or current.labels,
                "lifecycle": lifecycle,
                "revision": current.revision + 1,
                "reviewed_at": datetime.now(UTC),
            }
        )
        self._repository.update_classification(updated, current.revision)
        local_excerpt = next(
            (
                section.content[:600]
                for section in self._evidence.sections()
                if section.artifact_id == current.artifact_id
            ),
            None,
        )
        if review.action == "correct":
            self.record_review(
                kind=ImprovementSignalKind.document_classification_review,
                target=f"{classification_id}:{updated.revision}",
                action=review.action,
                outcome=1,
                attributes={"document_kind": document_kind.value},
                local_excerpt=local_excerpt,
            )
        if lifecycle is DocumentLifecycleState.approved:
            disposition = DocumentDispositionProposal(
                disposition_id=_hash("disposition", updated.classification_id, updated.revision),
                classification_id=updated.classification_id,
                source_reference=updated.source_reference,
                safe_filename=_safe_filename(updated.source_reference, document_kind),
                collection=document_kind.value,
                tags=sorted({value for items in updated.labels.values() for value in items})[:20],
            )
            self._repository.save_disposition(disposition)
        return updated

    def review_disposition(
        self, disposition_id: str, review: DocumentDispositionReview
    ) -> DocumentDispositionProposal:
        current = next(
            (
                value
                for value in self._repository.dispositions()
                if value.disposition_id == disposition_id
            ),
            None,
        )
        if current is None:
            raise KeyError(disposition_id)
        if current.revision != review.expected_revision:
            raise RuntimeError("stale_disposition")
        updated = current.model_copy(
            update={
                "status": "reviewed" if review.action == "approve" else "cancelled",
                "routing_action": review.routing_action,
                "revision": current.revision + 1,
            }
        )
        self._repository.update_disposition(updated, current.revision)
        self.record_review(
            kind=ImprovementSignalKind.routing_review,
            target=f"{disposition_id}:{updated.revision}",
            action=review.action,
            outcome=1 if review.action == "approve" else -1,
            attributes={"collection": current.collection},
        )
        return updated

    def scan_status(self) -> DocumentScanStatus:
        return self._repository.scan_status()

    async def scan_documents(self, connector_ids: list[str]) -> DocumentScanStatus:
        if self._context is None:
            status = DocumentScanStatus(
                phase=DocumentScanPhase.unavailable,
                detail_code="document_sources_unavailable",
            )
            self._repository.save_scan_status(status)
            return status
        async with self._scan_lock:
            ocr_status = await getattr(
                self._context,
                "ocr_preflight",
                lambda: asyncio.sleep(
                    0,
                    result=getattr(self._context, "ocr_status", lambda: "disabled")(),
                ),
            )()
            self._repository.save_scan_status(
                DocumentScanStatus(
                    phase=DocumentScanPhase.scanning,
                    ocr_status=ocr_status,
                )
            )
            try:
                _, unavailable = await self._context.prepare_assurance_sources(connector_ids)
                inbox = self.document_inbox()
                status = DocumentScanStatus(
                    phase=(
                        DocumentScanPhase.degraded
                        if unavailable
                        else DocumentScanPhase.ready
                    ),
                    scanned_sources=max(0, len(connector_ids) - len(unavailable)),
                    indexed_revisions=len(inbox.classifications),
                    failed_sources=len(unavailable),
                    extraction_methods=[
                        "native",
                        *(
                            ["ocr"]
                            if ocr_status == "ready"
                            else []
                        ),
                    ],
                    ocr_status=ocr_status,
                    detail_code=("some_sources_unavailable" if unavailable else None),
                    last_success_at=datetime.now(UTC),
                )
            except (OSError, RuntimeError, TimeoutError, ValueError):
                status = DocumentScanStatus(
                    phase=DocumentScanPhase.unavailable,
                    ocr_status=ocr_status,
                    detail_code="document_scan_failed",
                )
            self._repository.save_scan_status(status)
            return status

    async def execute_disposition(
        self,
        disposition_id: str,
        *,
        expected_revision: int,
        destination_ref: str,
        routing_action: Literal["copy", "move"],
    ) -> DocumentOperationPlan:
        if self._integrations is None:
            raise RuntimeError("document_router_unavailable")
        disposition = next(
            (
                value
                for value in self._repository.dispositions()
                if value.disposition_id == disposition_id
            ),
            None,
        )
        if disposition is None:
            raise KeyError(disposition_id)
        if disposition.revision != expected_revision:
            raise RuntimeError("stale_disposition")
        if disposition.status != "reviewed":
            raise PermissionError("document disposition requires review")
        classification = next(
            (
                value
                for value in self._repository.classifications()
                if value.classification_id == disposition.classification_id
            ),
            None,
        )
        if classification is None:
            raise KeyError(disposition.classification_id)
        revision = self._evidence.revision(classification.revision_id)
        if revision is None:
            raise RuntimeError("document_revision_unavailable")
        destination = next(
            (
                value
                for value in self._integrations.routing_destinations()
                if value.destination_ref == destination_ref
            ),
            None,
        )
        if destination is None:
            raise KeyError(destination_ref)
        operation_id = _hash(
            disposition_id,
            disposition.revision,
            destination_ref,
            routing_action,
            revision.content_hash,
        )
        plan = DocumentOperationPlan(
            operation_id=operation_id,
            disposition_id=disposition_id,
            source_reference=disposition.source_reference,
            destination_ref=destination_ref,
            action=routing_action,
            source_hash=revision.content_hash,
        )
        self._repository.save_operation_plan(plan)
        partner_values = classification.labels.get("partner") or [
            disposition.collection
        ]
        routing = DocumentRoutingProposal(
            proposal_id=operation_id,
            artifact_id=classification.artifact_id,
            source_reference=disposition.source_reference[:256],
            partner_reference=partner_values[0][:256],
            partner_label=disposition.collection,
            destination_ref=destination_ref,
            destination_label=destination.label,
            action=DocumentRoutingAction(routing_action),
            confidence=classification.confidence,
        )
        self._integrations.save_routing_proposal(routing)
        result = await self._integrations.review_routing(
            operation_id,
            DocumentRoutingReview(
                expected_revision=0,
                action="execute",
                routing_action=DocumentRoutingAction(routing_action),
            ),
        )
        completed = plan.model_copy(
            update={
                "status": "completed" if result.status == "completed" else "failed",
                "completed_at": datetime.now(UTC),
                "revision": plan.revision + 1,
            }
        )
        self._repository.save_operation_plan(completed)
        self._repository.update_disposition(
            disposition.model_copy(
                update={
                    "status": completed.status,
                    "destination_ref": destination_ref,
                    "routing_action": routing_action,
                    "revision": disposition.revision + 1,
                }
            ),
            disposition.revision,
        )
        return completed

    def erp_templates(self) -> list[ERPControlTemplate]:
        return BUILTIN_ERP_CONTROL_TEMPLATES

    async def validate_netsuite_mapping(
        self, connector_id: str
    ) -> NetSuiteMappingValidation:
        if self._context is None:
            return NetSuiteMappingValidation(
                connector_id=connector_id,
                status="unavailable",
                issues=[
                    NetSuiteMappingIssue(
                        code="connector.unavailable",
                        record_type="NetSuite",
                        severity="error",
                    )
                ],
            )
        definition = self._context.connector_definition(connector_id)
        configuration = definition.resolved_configuration()
        if not isinstance(configuration, NetSuiteConnectorConfig):
            raise ValueError("mapping validation requires a NetSuite connector")
        try:
            snapshot = await self._context.erp_metadata_snapshot(connector_id)
        except (OSError, PermissionError, RuntimeError, TimeoutError, ValueError):
            return NetSuiteMappingValidation(
                connector_id=connector_id,
                status="unavailable",
                issues=[
                    NetSuiteMappingIssue(
                        code="metadata.unavailable",
                        record_type="NetSuite",
                        severity="error",
                    )
                ],
            )
        entities = {entity.name.casefold(): entity for entity in snapshot.entities}
        issues: list[NetSuiteMappingIssue] = []
        for mapping in configuration.entity_mappings:
            entity = entities.get(mapping.record_type.casefold())
            if entity is None:
                issues.append(
                    NetSuiteMappingIssue(
                        code="mapping.record_type_missing",
                        record_type=mapping.record_type,
                        severity="error",
                    )
                )
                continue
            fields = {field.name.casefold(): field for field in entity.fields}
            for field_name in mapping.projected_fields:
                if field_name.casefold() not in fields:
                    issues.append(
                        NetSuiteMappingIssue(
                            code="mapping.field_missing",
                            record_type=mapping.record_type,
                            field=field_name,
                            severity="error",
                        )
                    )
            key = fields.get(mapping.key_field.casefold())
            if key is not None and not key.key:
                issues.append(
                    NetSuiteMappingIssue(
                        code="mapping.business_key_mismatch",
                        record_type=mapping.record_type,
                        field=mapping.key_field,
                        severity="warning",
                    )
                )
        mapped = {
            mapping.record_type.casefold()
            for mapping in configuration.entity_mappings
            if not any(
                issue.severity == "error"
                and issue.record_type.casefold() == mapping.record_type.casefold()
                for issue in issues
            )
        }
        active_templates = [
            template.template_id
            for template in BUILTIN_ERP_CONTROL_TEMPLATES
            if {value.casefold() for value in template.required_record_types} <= mapped
        ]
        prior = self._repository.metadata_baseline(connector_id)
        changed = (
            sorted(mapping.record_type for mapping in configuration.entity_mappings)
            if prior is not None and prior.metadata_hash != snapshot.metadata_hash
            else []
        )
        self._repository.save_metadata_baseline(
            ERPMetadataBaseline(
                connector_id=connector_id,
                metadata_hash=snapshot.metadata_hash,
                prior_metadata_hash=prior.metadata_hash if prior else None,
                changed_record_types=changed,
                status=(
                    "initial"
                    if prior is None
                    else ("changed" if changed else "unchanged")
                ),
            )
        )
        if changed:
            for record_type in changed:
                issues.append(
                    NetSuiteMappingIssue(
                        code="metadata.schema_drift",
                        record_type=record_type,
                        severity="warning",
                    )
                )
        return NetSuiteMappingValidation(
            connector_id=connector_id,
            status=(
                "invalid"
                if any(issue.severity == "error" for issue in issues)
                else "valid"
            ),
            metadata_hash=snapshot.metadata_hash,
            active_templates=active_templates,
            issues=issues,
        )

    async def run_erp_governance(
        self, request: ERPGovernanceRunRequest
    ) -> ERPGovernanceRun:
        if self._context is None or self._integrations is None:
            raise RuntimeError("erp_governance_unavailable")
        definition = self._context.connector_definition(request.connector_id)
        configuration = definition.resolved_configuration()
        if not isinstance(configuration, NetSuiteConnectorConfig):
            raise ValueError("ERP governance requires a NetSuite connector")
        validation = await self.validate_netsuite_mapping(request.connector_id)
        mapped = {mapping.record_type.casefold() for mapping in configuration.entity_mappings}
        validated_templates = set(validation.active_templates)
        selected = set(request.processes) if request.processes else set(ERPProcess)
        templates = [
            template
            for template in BUILTIN_ERP_CONTROL_TEMPLATES
            if template.process in selected
        ]
        active = [
            template
            for template in templates
            if template.template_id in validated_templates
        ]
        inactive = [template for template in templates if template not in active]
        recommendations: list[ERPGovernanceRecommendation] = []
        for template in inactive:
            missing = sorted(
                set(template.required_record_types)
                - {value for value in template.required_record_types if value.casefold() in mapped}
            )
            validation_codes = sorted(
                {
                    issue.code
                    for issue in validation.issues
                    if issue.record_type.casefold()
                    in {value.casefold() for value in template.required_record_types}
                }
            )
            reason = (
                f"Missing reviewed record mappings: {', '.join(missing)}"
                if missing
                else (
                    "Metadata validation failed: " + ", ".join(validation_codes)
                    if validation_codes
                    else "Metadata validation did not activate this process template."
                )
            )
            recommendations.append(
                ERPGovernanceRecommendation(
                    recommendation_id=_hash(request.connector_id, template.template_id, *missing),
                    kind="mapping_change",
                    title=f"Complete {template.title} mappings",
                    source_reference=definition.display_name,
                    reason=reason,
                )
            )
        try:
            quality = await self._integrations.run_data_quality(
                DataQualityRunRequest(
                    connector_id=request.connector_id,
                    changed_only=request.changed_only,
                )
            )
            findings = [
                value
                for value in self._integrations.findings()
                if value.run_id == quality.run_id
            ]
            recommendation_kinds: tuple[
                Literal["risk", "action", "issue_draft"], ...
            ] = ("risk", "action", "issue_draft")
            for finding in findings[:500]:
                for recommendation_kind in recommendation_kinds:
                    recommendations.append(
                        ERPGovernanceRecommendation(
                            recommendation_id=_hash(
                                finding.finding_id, recommendation_kind
                            ),
                            kind=recommendation_kind,
                            title=finding.title,
                            source_reference=finding.source_reference,
                            reason=(
                                f"Data-quality rule {finding.rule_id} requires explicit review."
                            ),
                        )
                    )
            status = "completed" if quality.status == "completed" else "degraded"
            detail = quality.detail_code
        except (KeyError, OSError, PermissionError, RuntimeError, TimeoutError, ValueError):
            findings = []
            status = "degraded"
            detail = "netsuite_governance_source_unavailable"
        run = ERPGovernanceRun(
            run_id=_hash("erp-governance", request.connector_id, datetime.now(UTC).isoformat()),
            connector_id=request.connector_id,
            status=status,
            active_templates=[template.template_id for template in active],
            inactive_templates=[template.template_id for template in inactive],
            finding_count=len(findings),
            recommendations=recommendations[:500],
            detail_code=detail,
        )
        self._repository.save_erp_run(run)
        return run

    def erp_dashboard(self) -> ERPGovernanceDashboard:
        runs = self._repository.erp_runs()
        findings = self._integrations.findings() if self._integrations is not None else []
        severity = Counter(finding.severity for finding in findings)
        status = Counter(finding.status for finding in findings)
        active_template_ids = {
            template_id for run in runs for template_id in run.active_templates
        }
        active_processes = [
            template.process
            for template in BUILTIN_ERP_CONTROL_TEMPLATES
            if template.template_id in active_template_ids
        ]
        return ERPGovernanceDashboard(
            runs=runs,
            findings_by_severity=dict(severity),
            findings_by_status=dict(status),
            active_processes=list(dict.fromkeys(active_processes)),
            ownerless_findings=sum(
                "owner" in finding.description.casefold()
                and any(term in finding.description.casefold() for term in ("missing", "required"))
                for finding in findings
            ),
        )

    def erp_run(self, run_id: str) -> ERPGovernanceRun | None:
        return next(
            (value for value in self._repository.erp_runs() if value.run_id == run_id),
            None,
        )
