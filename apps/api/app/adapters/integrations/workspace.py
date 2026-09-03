"""Composition for v9 data quality, governed actions, and document routing."""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.adapters.context.service import ContextConnectorService
from app.adapters.integrations.store import EncryptedIntegrationRepository
from app.domain.context import (
    AmazonFbaConnectorConfig,
    MicrosoftTeamsConnectorConfig,
    NetSuiteConnectorConfig,
    RepositoryConnectorConfig,
    SlackConnectorConfig,
    WmsConnectorConfig,
    WorkManagementConnectorConfig,
)
from app.domain.integrations import (
    DataQualityFinding,
    DataQualityFindingReview,
    DataQualityRule,
    DataQualityRuleKind,
    DataQualityRulePack,
    DataQualityRun,
    DataQualityRunRequest,
    DeliveryStatus,
    DocumentRoutingAction,
    DocumentRoutingDestination,
    DocumentRoutingProposal,
    DocumentRoutingReview,
    ExternalActionCreateRequest,
    ExternalActionDraft,
    ExternalActionKind,
    NotificationPolicy,
)
from app.services.integrations import (
    build_external_action,
    classify_partner,
    evaluate_quality_records,
    transition_action,
)

_SEVERITY = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class LocalDocumentRouter:
    """Copy or move one indexed local artifact into an approved local root."""

    def __init__(
        self,
        context: ContextConnectorService,
        repository: EncryptedIntegrationRepository,
    ) -> None:
        self._context = context
        self._repository = repository

    @staticmethod
    def _inside(root: Path, candidate: Path) -> bool:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _is_link(path: Path) -> bool:
        is_junction = getattr(path, "is_junction", None)
        return path.is_symlink() or bool(is_junction and is_junction())

    def _execute_sync(self, proposal: DocumentRoutingProposal) -> DocumentRoutingProposal:
        source = self._context.local_artifact_source(proposal.artifact_id)
        root = self._repository.routing_root(proposal.destination_ref)
        if root is None:
            raise ValueError("routing destination is unavailable")
        if self._is_link(source) or self._is_link(root):
            raise ValueError("routing paths must not use links")
        source = source.resolve(strict=True)
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("routing paths must not use links")
        destination = (root / source.name).resolve(strict=False)
        if not self._inside(root, destination):
            raise ValueError("routing destination escaped its approved root")
        if destination.exists():
            suffix = hashlib.sha256(source.read_bytes()).hexdigest()[:10]
            destination = root / f"{source.stem}-{suffix}{source.suffix}"
        temporary = root / f".{destination.name}.{secrets.token_hex(8)}.partial"
        try:
            shutil.copy2(source, temporary)
            source_hash = hashlib.sha256(source.read_bytes()).digest()
            copied_hash = hashlib.sha256(temporary.read_bytes()).digest()
            if not secrets.compare_digest(source_hash, copied_hash):
                raise OSError("routed document verification failed")
            os.replace(temporary, destination)
            if proposal.action is DocumentRoutingAction.move:
                source.unlink()
            completed = proposal.model_copy(
                update={"status": "completed", "revision": proposal.revision + 1}
            )
            self._repository.record_routing_operation(
                completed,
                source_path=str(source),
                destination_path=str(destination),
                content_hash=source_hash.hex(),
            )
        finally:
            temporary.unlink(missing_ok=True)
        return completed

    async def execute(
        self, proposal: DocumentRoutingProposal
    ) -> DocumentRoutingProposal:
        return await asyncio.to_thread(self._execute_sync, proposal)


class IntegrationWorkspace:
    def __init__(
        self,
        *,
        context: ContextConnectorService,
        repository: EncryptedIntegrationRepository,
        rules: list[DataQualityRule] | None = None,
    ) -> None:
        self._context = context
        self._repository = repository
        stored_rules = [rule for pack in repository.rule_packs() for rule in pack.rules]
        self._rules = {rule.rule_id: rule for rule in [*(rules or []), *stored_rules]}
        self._router = LocalDocumentRouter(context, repository)
        self._mutation_lock = asyncio.Lock()
        self._improvement_profiles: dict[str, dict[str, object]] = {}

    def set_improvement_profiles(self, profiles: dict[str, dict[str, object]]) -> None:
        self._improvement_profiles = {
            key: dict(value) for key, value in profiles.items() if len(value) <= 32
        }

    def _selected_rules(self, request: DataQualityRunRequest) -> list[DataQualityRule]:
        rules = list(self._rules.values())
        if not rules:
            definition = self._context.connector_definition(request.connector_id)
            configuration = definition.resolved_configuration()
            if isinstance(
                configuration,
                (NetSuiteConnectorConfig, WmsConnectorConfig, AmazonFbaConnectorConfig),
            ):
                mappings = (
                    [
                        (mapping.record_type, mapping.key_field, True)
                        for mapping in configuration.entity_mappings
                    ]
                    if isinstance(configuration, NetSuiteConnectorConfig)
                    else [
                        (mapping.entity_name, mapping.key_field, True)
                        for mapping in configuration.entity_mappings
                    ]
                    if isinstance(configuration, WmsConnectorConfig)
                    else [
                        ("fba_inventory", "sku", False),
                        ("fba_inbound_shipments", "shipment_id", True),
                        ("fba_inbound_items", "sku", False),
                    ]
                )
                pack_id = (
                    "builtin-netsuite-safety"
                    if isinstance(configuration, NetSuiteConnectorConfig)
                    else "builtin-wms-safety"
                    if isinstance(configuration, WmsConnectorConfig)
                    else "builtin-amazon-fba-safety"
                )
                for record_type, key_field, check_duplicate in mappings:
                    base = record_type.casefold()
                    rules.append(
                        DataQualityRule(
                            rule_id=f"builtin.{base}.required_key",
                            pack_id=pack_id,
                            kind=DataQualityRuleKind.required_value,
                            severity="high",
                            record_type=record_type,
                            fields=[key_field],
                            parameters={"key_field": key_field},
                        )
                    )
                    if check_duplicate:
                        rules.append(
                            DataQualityRule(
                                rule_id=f"builtin.{base}.duplicate_key",
                                pack_id=pack_id,
                                kind=DataQualityRuleKind.duplicate_key,
                                severity="high",
                                record_type=record_type,
                                fields=[key_field],
                                parameters={"key_field": key_field},
                            )
                        )
        if request.rule_pack_ids:
            selected = set(request.rule_pack_ids)
            rules = [rule for rule in rules if rule.pack_id in selected]
        preferred = self._improvement_profiles.get("netsuite_mapping", {}).get(
            "reviewed_values", []
        )
        preferred_ids = set(preferred) if isinstance(preferred, list) else set()
        rules.sort(key=lambda rule: (rule.rule_id not in preferred_ids, rule.rule_id))
        return rules

    def rule_packs(self) -> list[DataQualityRulePack]:
        return self._repository.rule_packs()

    def save_rule_pack(self, pack: DataQualityRulePack) -> DataQualityRulePack:
        if not pack.trusted or any(not rule.trusted for rule in pack.rules):
            raise PermissionError("data-quality rule pack is not trusted")
        self._repository.save_rule_pack(pack)
        self._rules.update({rule.rule_id: rule for rule in pack.rules})
        return pack

    async def run_data_quality(self, request: DataQualityRunRequest) -> DataQualityRun:
        run_id = hashlib.sha256(
            f"quality\0{datetime.now(UTC).isoformat()}\0{secrets.token_hex(32)}".encode()
        ).hexdigest()
        running = DataQualityRun(
            run_id=run_id,
            connector_id=request.connector_id,
            status="running",
        )
        await asyncio.to_thread(self._repository.save_run, running)
        try:
            records = await asyncio.wait_for(
                self._context.quality_records(request.connector_id), timeout=30.0
            )
            rules = self._selected_rules(request)
            findings = evaluate_quality_records(
                run_id=run_id,
                connector_id=request.connector_id,
                records_by_type=records,
                rules=rules,
            )
            findings.extend(
                await asyncio.to_thread(
                    self._refresh_routing_proposals, run_id, request.connector_id
                )
            )
            await asyncio.to_thread(self._repository.save_findings, findings)
            completed = running.model_copy(
                update={
                    "status": "completed",
                    "inspected_records": sum(len(values) for values in records.values()),
                    "finding_count": len(findings),
                    "completed_at": datetime.now(UTC),
                }
            )
            await asyncio.to_thread(self._repository.save_run, completed)
            await self._apply_notification_policies(findings)
            return completed
        except (KeyError, OSError, PermissionError, RuntimeError, TimeoutError, ValueError):
            degraded = running.model_copy(
                update={
                    "status": "degraded",
                    "detail_code": "data_quality_source_unavailable",
                    "completed_at": datetime.now(UTC),
                }
            )
            await asyncio.to_thread(self._repository.save_run, degraded)
            return degraded

    def _refresh_routing_proposals(
        self, run_id: str, connector_id: str
    ) -> list[DataQualityFinding]:
        destinations = self._repository.routing_destinations()
        if not destinations:
            return []
        ambiguous_findings: list[DataQualityFinding] = []
        existing = {proposal.proposal_id for proposal in self.routing_proposals()}
        sections_by_artifact: dict[str, list[str]] = {}
        for section in self._context.evidence_repository.sections():
            sections_by_artifact.setdefault(section.artifact_id, []).append(section.content)
        artifacts = {
            artifact.artifact_id: artifact
            for artifact in self._context.evidence_repository.artifacts()
        }
        partners = [
            (reference, destination.label, destination.aliases)
            for destination in destinations
            for reference in destination.partner_references
        ]
        for artifact_id, content in sections_by_artifact.items():
            classification = classify_partner(
                artifact_id=artifact_id,
                content="\n".join(content),
                partners=partners,
            )
            if classification is None:
                continue
            if classification.ambiguous:
                fingerprint = hashlib.sha256(
                    f"routing-ambiguity\0{artifact_id}\0"
                    f"{classification.partner_reference}".encode()
                ).hexdigest()
                ambiguous_findings.append(
                    DataQualityFinding(
                        finding_id=hashlib.sha256(
                            f"{run_id}\0{fingerprint}".encode()
                        ).hexdigest(),
                        fingerprint=fingerprint,
                        run_id=run_id,
                        connector_id=connector_id,
                        rule_id="builtin.partner_routing_ambiguity",
                        severity="medium",
                        title="Partner routing requires review",
                        description=(
                            "More than one approved partner matched this document; "
                            "no routing proposal was created."
                        ),
                        source_reference=artifacts.get(artifact_id).source_reference
                        if artifacts.get(artifact_id)
                        else "Local document",
                    )
                )
                continue
            matches = [
                destination
                for destination in destinations
                if classification.partner_reference in destination.partner_references
            ]
            if len(matches) != 1:
                continue
            destination = matches[0]
            artifact = artifacts.get(artifact_id)
            if artifact is None:
                continue
            proposal_id = hashlib.sha256(
                f"route\0{artifact_id}\0{artifact.current_revision_id}\0"
                f"{destination.destination_ref}".encode()
            ).hexdigest()
            if proposal_id in existing:
                continue
            self._repository.save_routing_proposal(
                DocumentRoutingProposal(
                    proposal_id=proposal_id,
                    artifact_id=artifact_id,
                    source_reference=artifact.source_reference,
                    partner_reference=classification.partner_reference,
                    partner_label=classification.partner_label,
                    destination_ref=destination.destination_ref,
                    destination_label=destination.label,
                    action=self._profiled_routing_action(),
                    confidence=classification.confidence,
                )
            )
        return ambiguous_findings

    def _profiled_routing_action(self) -> DocumentRoutingAction:
        reviewed = self._improvement_profiles.get("routing_mapping", {}).get(
            "reviewed_values", []
        )
        if isinstance(reviewed, list) and "move" in reviewed and "copy" not in reviewed:
            return DocumentRoutingAction.move
        return DocumentRoutingAction.copy

    def findings(self) -> list[DataQualityFinding]:
        return self._repository.findings()

    def review_finding(
        self, finding_id: str, review: DataQualityFindingReview
    ) -> DataQualityFinding:
        finding = next(
            (value for value in self.findings() if value.finding_id == finding_id), None
        )
        if finding is None:
            raise KeyError(finding_id)
        updated = finding.model_copy(
            update={
                "status": "confirmed" if review.action == "confirm" else "dismissed",
                "revision": finding.revision + 1,
            }
        )
        self._repository.review_finding(updated, review.expected_revision)
        return updated

    def _action_kind_and_label(
        self, connector_id: str, destination_ref: str
    ) -> tuple[ExternalActionKind, str]:
        definition = self._context.connector_definition(connector_id)
        configuration = definition.resolved_configuration()
        if isinstance(configuration, NetSuiteConnectorConfig):
            if destination_ref != "netsuite_review" or configuration.review_target is None:
                raise ValueError("NetSuite destination is not the configured review record")
            return ExternalActionKind.netsuite_review_record, "NetSuite review queue"
        if isinstance(
            configuration, (RepositoryConnectorConfig, WorkManagementConnectorConfig)
        ):
            selections = {
                selection.selection_id: selection
                for selection in self._context.source_selections(connector_id)
            }
            selection = selections.get(destination_ref)
            if selection is None:
                raise ValueError("external draft destination is not selected")
            kinds = {
                "github": ExternalActionKind.github_issue,
                "gitlab": ExternalActionKind.gitlab_issue,
                "jira": ExternalActionKind.jira_work_item,
                "azure_devops": ExternalActionKind.azure_boards_work_item,
                "servicenow": ExternalActionKind.servicenow_change,
            }
            kind = kinds.get(configuration.kind)
            if kind is None:
                raise ValueError("connector does not support issue drafts")
            return kind, selection.label
        spaces = {
            space.selection_id: space
            for space in self._context.external_spaces(connector_id)
        }
        space = spaces.get(destination_ref)
        if space is None:
            raise ValueError("notification destination is not selected")
        if isinstance(configuration, MicrosoftTeamsConnectorConfig):
            return ExternalActionKind.teams_notification, space.label
        if isinstance(configuration, SlackConnectorConfig):
            return ExternalActionKind.slack_notification, space.label
        raise ValueError("connector does not support governed actions")

    def create_action(
        self,
        request: ExternalActionCreateRequest,
        *,
        allow_trusted_unconfirmed: bool = False,
    ) -> ExternalActionDraft:
        finding = next(
            (value for value in self.findings() if value.finding_id == request.finding_id),
            None,
        )
        if finding is None:
            raise KeyError(request.finding_id)
        if finding.status != "confirmed" and not allow_trusted_unconfirmed:
            raise PermissionError("finding must be confirmed before drafting an action")
        kind, label = self._action_kind_and_label(
            request.connector_id, request.destination_ref
        )
        action = build_external_action(
            finding=finding,
            connector_id=request.connector_id,
            destination_ref=request.destination_ref,
            destination_label=label,
            kind=kind,
        )
        return self._repository.create_action(action)

    def actions(self) -> list[ExternalActionDraft]:
        return self._repository.actions()

    async def _transition(
        self,
        action: ExternalActionDraft,
        status: DeliveryStatus,
        *,
        origin: str | None = None,
        detail_code: str | None = None,
    ) -> ExternalActionDraft:
        updated, event = transition_action(
            action,
            status,
            approval_origin=origin,
            detail_code=detail_code,
        )
        await asyncio.to_thread(
            self._repository.transition_action, updated, event, action.revision
        )
        return updated

    async def approve_action(
        self, action_id: str, expected_revision: int, *, origin: str = "user"
    ) -> ExternalActionDraft:
        del action_id, expected_revision, origin
        raise PermissionError("direct external writes are disabled in API v13")

    async def cancel_action(
        self, action_id: str, expected_revision: int
    ) -> ExternalActionDraft:
        async with self._mutation_lock:
            action = self._repository.action(action_id)
            if action is None:
                raise KeyError(action_id)
            if action.revision != expected_revision:
                raise RuntimeError("stale_action")
            return await self._transition(action, DeliveryStatus.cancelled, origin="user")

    def policies(self) -> list[NotificationPolicy]:
        return self._repository.policies()

    def save_policy(
        self, policy: NotificationPolicy, expected_revision: int | None
    ) -> NotificationPolicy:
        self._action_kind_and_label(policy.connector_id, policy.destination_ref)
        if policy.enabled:
            raise PermissionError("automatic external delivery is disabled in API v13")
        trusted_rules = {
            rule.rule_id for rule in self._rules.values() if rule.trusted
        }
        if policy.enabled and not set(policy.trusted_rule_ids) <= trusted_rules:
            raise PermissionError("notification policy contains an untrusted rule")
        self._repository.save_policy(policy, expected_revision)
        return policy

    async def _apply_notification_policies(
        self, findings: list[DataQualityFinding]
    ) -> None:
        policies = [policy for policy in self.policies() if policy.enabled]
        for finding in findings:
            rule = self._rules.get(finding.rule_id)
            if rule is None or not rule.trusted:
                continue
            for policy in policies:
                if (
                    finding.rule_id not in policy.trusted_rule_ids
                    or _SEVERITY[finding.severity] < _SEVERITY[policy.minimum_severity]
                ):
                    continue
                self.create_action(
                    ExternalActionCreateRequest(
                        connector_id=policy.connector_id,
                        destination_ref=policy.destination_ref,
                        finding_id=finding.finding_id,
                    ),
                    allow_trusted_unconfirmed=True,
                )

    def routing_destinations(self) -> list[DocumentRoutingDestination]:
        return self._repository.routing_destinations()

    def save_routing_destination(
        self, destination: DocumentRoutingDestination, root_path: str
    ) -> DocumentRoutingDestination:
        candidate = Path(root_path)
        if LocalDocumentRouter._is_link(candidate):
            raise ValueError("routing destination must be an existing local folder")
        root = candidate.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("routing destination must be an existing local folder")
        return self._repository.save_routing_destination(destination, str(root))

    def routing_proposals(self) -> list[DocumentRoutingProposal]:
        return self._repository.routing_proposals()

    def save_routing_proposal(
        self, proposal: DocumentRoutingProposal
    ) -> DocumentRoutingProposal:
        if proposal.status != "proposed":
            raise ValueError("new routing proposals must require review")
        self._repository.save_routing_proposal(proposal)
        return proposal

    async def review_routing(
        self, proposal_id: str, review: DocumentRoutingReview
    ) -> DocumentRoutingProposal:
        async with self._mutation_lock:
            proposal = next(
                (
                    value
                    for value in self.routing_proposals()
                    if value.proposal_id == proposal_id
                ),
                None,
            )
            if proposal is None:
                raise KeyError(proposal_id)
            if proposal.revision != review.expected_revision:
                raise RuntimeError("stale_routing_proposal")
            if review.action == "cancel":
                updated = proposal.model_copy(
                    update={"status": "cancelled", "revision": proposal.revision + 1}
                )
            else:
                requested = proposal.model_copy(update={"action": review.routing_action})
                try:
                    updated = await self._router.execute(requested)
                except (KeyError, OSError, ValueError):
                    updated = requested.model_copy(
                        update={"status": "failed", "revision": proposal.revision + 1}
                    )
            self._repository.save_routing_proposal(updated)
            return updated
