"""Persistent composition service for read-only context connectors."""

from __future__ import annotations

import asyncio
import getpass
import hashlib
import json
import os
import time
from pathlib import Path

from app.adapters.context.amazon_fba import AmazonFbaContextProvider
from app.adapters.context.broker import ContextBroker
from app.adapters.context.collaboration import (
    MicrosoftTeamsContextProvider,
    SlackContextProvider,
    SlackPkceAuthorization,
    SlackPkceManager,
)
from app.adapters.context.encrypted_index import EncryptedContextIndex
from app.adapters.context.enterprise_gateway import (
    GatewayBackedContextProvider,
    enterprise_connector_catalog,
)
from app.adapters.context.enterprise_oidc import (
    EnterpriseAuthorization,
    EnterpriseOidcPkceManager,
)
from app.adapters.context.local_files import LocalFilesContextProvider
from app.adapters.context.microsoft_oauth import (
    DeviceAuthorization,
    MicrosoftDeviceCodeManager,
    MicrosoftOAuthCredentialStore,
)
from app.adapters.context.netsuite import (
    NetSuiteContextProvider,
)
from app.adapters.context.notion import NotionContextProvider, normalize_notion_page_id
from app.adapters.context.problem_context import resolve_problem_context
from app.adapters.context.question_context import resolve_question_context
from app.adapters.context.remote import (
    MicrosoftGraphContextProvider,
    ODataContextProvider,
    validate_remote_base_url,
)
from app.adapters.context.windows_security import (
    SecretStore,
    WindowsCredentialStore,
    WindowsDpapiProtector,
)
from app.adapters.context.wms import WmsContextProvider
from app.adapters.evidence.repository import EncryptedEvidenceRepository
from app.adapters.evidence.retrieval import LocalHybridRetrievalEngine
from app.adapters.evidence.tesseract_ocr import TesseractOcrProvider
from app.domain.assurance import ERPMetadataSnapshot
from app.domain.context import (
    AmazonFbaConnectorConfig,
    ConnectedProblemBrief,
    ConnectedProblemContextRequest,
    ConnectedQuestionBrief,
    ConnectedQuestionContextRequest,
    ConnectorDefinition,
    ConnectorHealth,
    ConnectorKind,
    ConnectorPhase,
    ContextDiagnostics,
    ContextDocument,
    ContextSearchQuery,
    ContextSearchResult,
    EntityGlossary,
    EntityGlossaryEntry,
    EntityGlossaryKind,
    EntityMapping,
    ExternalSpaceSelection,
    LocalFilesConnectorConfig,
    MicrosoftGraphConnectorConfig,
    MicrosoftTeamsConnectorConfig,
    NetSuiteConnectorConfig,
    NotionConnectorConfig,
    ODataConnectorConfig,
    OpenApiConnectorConfig,
    PublicContextCitation,
    RepositoryConnectorConfig,
    SftpConnectorConfig,
    SlackConnectorConfig,
    SqlConnectorConfig,
    WmsConnectorConfig,
    WorkManagementConnectorConfig,
)
from app.domain.contracts import MeetingState
from app.domain.enterprise import (
    AccessLease,
    ConnectorCatalogEntry,
    EnterpriseSearchCitation,
    EnterpriseSearchRequest,
    EnterpriseSearchResult,
    SourceSelection,
)
from app.domain.erp_analytics import ERPAnalyticsRequest, ERPAnalyticsRun
from app.domain.evidence import (
    ArtifactLocator,
    DocumentKind,
    EmbeddingProfile,
    EvidenceBundle,
    EvidenceCitation,
    RetrievalRequest,
    RetrievalScore,
)
from app.domain.fulfillment_reconciliation import (
    FulfillmentReconciliationRequest,
    FulfillmentReconciliationRun,
    FulfillmentStage,
)
from app.domain.ports import EmbeddingProvider, ExternalActionExecutor, OcrProvider
from app.services.erp_analytics import (
    ERPAnalyticsSource,
    analyze_erp_records,
)
from app.services.fulfillment_reconciliation import (
    FulfillmentSource,
    reconcile_fulfillment_records,
)

_EMBEDDING_MODEL_DIR_ENV = "MEETING_INTELLIGENCE_EMBEDDING_MODEL_DIR"
_EMBEDDING_MODEL_SHA256_ENV = "MEETING_INTELLIGENCE_EMBEDDING_MODEL_SHA256"
_ERP_ANALYTICS_DEADLINE_SECONDS = 10.0
_OCR_EXECUTABLE_ENV = "MEETING_INTELLIGENCE_TESSERACT_PATH"
_OCR_PDF_RENDERER_ENV = "MEETING_INTELLIGENCE_PDFTOPPM_PATH"
_OCR_LANGUAGES_ENV = "MEETING_INTELLIGENCE_OCR_LANGUAGES"

_ENTERPRISE_CONFIGURATIONS = (
    RepositoryConnectorConfig,
    WorkManagementConnectorConfig,
    SqlConnectorConfig,
    SftpConnectorConfig,
    OpenApiConnectorConfig,
)

_SAP_MAPPINGS = [
    EntityMapping(
        entity_name="BusinessPartners",
        key_field="CardCode",
        display_field="CardName",
        searchable_fields=["CardCode", "CardName"],
        projected_fields=["CardCode", "CardName"],
    ),
    EntityMapping(
        entity_name="Orders",
        key_field="DocEntry",
        display_field="CardCode",
        searchable_fields=["DocEntry", "CardCode"],
        projected_fields=["DocEntry", "CardCode", "DocDate", "DocTotal"],
    ),
    EntityMapping(
        entity_name="Items",
        key_field="ItemCode",
        display_field="ItemName",
        searchable_fields=["ItemCode", "ItemName"],
        projected_fields=["ItemCode", "ItemName", "QuantityOnStock"],
    ),
]
_DYNAMICS_MAPPINGS = [
    EntityMapping(
        entity_name="accounts",
        key_field="accountid",
        display_field="name",
        searchable_fields=["name", "accountnumber"],
        projected_fields=["accountid", "name", "accountnumber"],
    ),
    EntityMapping(
        entity_name="contacts",
        key_field="contactid",
        display_field="fullname",
        searchable_fields=["fullname", "emailaddress1"],
        projected_fields=["contactid", "fullname", "emailaddress1"],
    ),
    EntityMapping(
        entity_name="salesorders",
        key_field="salesorderid",
        display_field="name",
        searchable_fields=["name"],
        projected_fields=["salesorderid", "name", "totalamount"],
    ),
    EntityMapping(
        entity_name="products",
        key_field="productid",
        display_field="name",
        searchable_fields=["name", "productnumber"],
        projected_fields=["productid", "name", "productnumber"],
    ),
]


def default_context_database() -> Path:
    configured = os.environ.get("MEETING_INTELLIGENCE_CONTEXT_DB")
    if configured:
        return Path(configured)
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise OSError("LOCALAPPDATA is required for the encrypted context index")
    return Path(local) / "MeetingIntelligenceCopilot" / "context-v1.sqlite3"


def current_user_principal() -> str:
    """Bind connector ACLs to the Windows account running the local sidecar."""

    user = getpass.getuser().strip().casefold()
    if not user:
        raise OSError("the current Windows user is unavailable")
    return f"windows-user:{user}"


class ContextConnectorService:
    def __init__(
        self,
        *,
        index: EncryptedContextIndex,
        secret_store: SecretStore,
        evidence_repository: EncryptedEvidenceRepository | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        ocr_provider: OcrProvider | None = None,
        timeout_seconds: float = 2.0,
        principal_id: str | None = None,
    ) -> None:
        self._index = index
        self._evidence_repository = evidence_repository or EncryptedEvidenceRepository(
            index.storage_path, index.protector
        )
        self._secret_store = MicrosoftOAuthCredentialStore(secret_store)
        self._embedding_provider = embedding_provider
        self._ocr_provider = ocr_provider
        self._microsoft_auth = MicrosoftDeviceCodeManager(self._secret_store)
        self._slack_auth = SlackPkceManager()
        self._enterprise_oidc = EnterpriseOidcPkceManager.from_env(self._secret_store)
        self._principal_id = principal_id or current_user_principal()
        self._broker = ContextBroker(timeout_seconds=timeout_seconds)
        self._mutation_lock = asyncio.Lock()
        self._glossary = index.entity_glossary()
        self._definitions = {
            definition.connector_id: definition
            for definition in index.connector_definitions()
        }
        self._mapped_glossary: list[EntityGlossaryEntry] = []
        self._improvement_source_authority_delta: dict[ConnectorKind, float] = {}
        self._rebuild_mapped_glossary()
        # A previous disconnect may have purged connector metadata while
        # Credential Manager was temporarily unavailable. Retry its encrypted
        # cleanup tombstones before exposing restored providers.
        self._retry_pending_credential_cleanup_sync()
        for definition in self._definitions.values():
            self._broker.register(self._provider(definition))

    @classmethod
    def windows_default(cls) -> ContextConnectorService:
        embedding_provider = None
        ocr_provider = None
        model_directory = os.environ.get(_EMBEDDING_MODEL_DIR_ENV)
        model_sha256 = os.environ.get(_EMBEDDING_MODEL_SHA256_ENV)
        if model_directory and model_sha256:
            try:
                from app.adapters.evidence.onnx_embeddings import (
                    OnnxMultilingualE5Provider,
                )

                embedding_provider = OnnxMultilingualE5Provider(
                    Path(model_directory), expected_bundle_sha256=model_sha256
                )
            except (OSError, RuntimeError, ValueError):
                embedding_provider = None
        ocr_executable = os.environ.get(_OCR_EXECUTABLE_ENV)
        if ocr_executable:
            try:
                renderer = os.environ.get(_OCR_PDF_RENDERER_ENV)
                ocr_provider = TesseractOcrProvider(
                    Path(ocr_executable),
                    pdf_renderer=Path(renderer) if renderer else None,
                    languages=os.environ.get(_OCR_LANGUAGES_ENV, "jpn+eng"),
                )
            except (OSError, RuntimeError, ValueError):
                ocr_provider = None
        return cls(
            index=EncryptedContextIndex(
                default_context_database(), WindowsDpapiProtector()
            ),
            secret_store=WindowsCredentialStore(),
            embedding_provider=embedding_provider,
            ocr_provider=ocr_provider,
        )

    @staticmethod
    def legacy_credential_target(connector_id: str) -> str:
        return f"MeetingIntelligenceCopilot/context/{connector_id}"

    @classmethod
    def credential_target(cls, definition: ConnectorDefinition) -> str:
        if definition.kind is ConnectorKind.microsoft_graph:
            binding = {
                "kind": definition.kind.value,
                "tenant_id": definition.tenant_id,
                "client_id": definition.client_id,
            }
        elif definition.kind is ConnectorKind.local_files:
            binding = {"kind": definition.kind.value}
        elif definition.kind in {
            ConnectorKind.netsuite,
            ConnectorKind.microsoft_teams,
            ConnectorKind.slack,
            ConnectorKind.notion,
            ConnectorKind.amazon_fba,
        }:
            configuration = definition.resolved_configuration()
            if isinstance(configuration, NetSuiteConnectorConfig):
                binding = {
                    "kind": definition.kind.value,
                    "account_id": configuration.account_id,
                    "client_id": configuration.client_id,
                    "certificate_id": configuration.certificate_id,
                }
            elif isinstance(configuration, AmazonFbaConnectorConfig):
                binding = {
                    "kind": definition.kind.value,
                    "region": configuration.region,
                    "client_id": configuration.lwa_client_id,
                    "marketplace_ids": configuration.marketplace_ids,
                }
            elif isinstance(configuration, MicrosoftTeamsConnectorConfig):
                binding = {
                    "kind": definition.kind.value,
                    "tenant_id": configuration.tenant_id,
                    "client_id": configuration.client_id,
                    "certificate_id": configuration.certificate_id,
                }
            elif isinstance(configuration, SlackConnectorConfig):
                binding = {
                    "kind": definition.kind.value,
                    "client_id": configuration.client_id,
                    "team_id": configuration.team_id,
                }
            else:
                assert isinstance(configuration, NotionConnectorConfig)
                binding = {
                    "kind": definition.kind.value,
                    "workspace_label": configuration.workspace_label,
                }
        elif isinstance(definition.resolved_configuration(), _ENTERPRISE_CONFIGURATIONS):
            configuration = definition.resolved_configuration()
            binding = {
                "kind": definition.kind.value,
                "configuration": configuration.model_dump(mode="json"),
            }
        else:
            assert definition.base_url is not None
            binding = {
                "kind": definition.kind.value,
                "base_url": validate_remote_base_url(definition.base_url),
            }
        digest = hashlib.sha256(
            json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        return f"{cls.legacy_credential_target(definition.connector_id)}/{digest}"

    def _provider(self, definition: ConnectorDefinition):
        configuration = definition.resolved_configuration()
        if isinstance(configuration, LocalFilesConnectorConfig):
            return LocalFilesContextProvider(
                connector_id=definition.connector_id,
                display_name=definition.display_name,
                root=Path(configuration.root_path),
                principal_id=definition.principal_id,
                index=self._index,
                evidence_repository=self._evidence_repository,
                ocr_provider=self._ocr_provider,
            )
        if isinstance(configuration, MicrosoftGraphConnectorConfig):
            return MicrosoftGraphContextProvider(
                connector_id=definition.connector_id,
                display_name=definition.display_name,
                principal_id=definition.principal_id,
                credential_target=self.credential_target(definition),
                secret_store=self._secret_store,
                drive_ids=configuration.drive_ids,
            )
        spaces = self._index.external_spaces(definition.connector_id)
        external_space_ids = self._index.resolve_external_spaces(definition.connector_id)
        if isinstance(configuration, NetSuiteConnectorConfig):
            return NetSuiteContextProvider(
                connector_id=definition.connector_id,
                display_name=definition.display_name,
                principal_id=definition.principal_id,
                configuration=configuration,
                credential_target=self.credential_target(definition),
                secret_store=self._secret_store,
            )
        if isinstance(configuration, AmazonFbaConnectorConfig):
            return AmazonFbaContextProvider(
                connector_id=definition.connector_id,
                display_name=definition.display_name,
                principal_id=definition.principal_id,
                configuration=configuration,
                credential_target=self.credential_target(definition),
                secret_store=self._secret_store,
            )
        if isinstance(configuration, MicrosoftTeamsConnectorConfig):
            return MicrosoftTeamsContextProvider(
                connector_id=definition.connector_id,
                display_name=definition.display_name,
                principal_id=definition.principal_id,
                configuration=configuration,
                spaces=spaces,
                external_space_ids=external_space_ids,
                credential_target=self.credential_target(definition),
                secret_store=self._secret_store,
            )
        if isinstance(configuration, SlackConnectorConfig):
            return SlackContextProvider(
                connector_id=definition.connector_id,
                display_name=definition.display_name,
                principal_id=definition.principal_id,
                configuration=configuration,
                spaces=spaces,
                external_space_ids=external_space_ids,
                credential_target=self.credential_target(definition),
                secret_store=self._secret_store,
            )
        if isinstance(configuration, NotionConnectorConfig):
            return NotionContextProvider(
                connector_id=definition.connector_id,
                display_name=definition.display_name,
                principal_id=definition.principal_id,
                configuration=configuration,
                selections=self._index.source_selections(definition.connector_id),
                external_page_ids=self._index.resolve_source_selections(
                    definition.connector_id
                ),
                credential_target=self.credential_target(definition),
                secret_store=self._secret_store,
            )
        if isinstance(configuration, WmsConnectorConfig):
            return WmsContextProvider(
                connector_id=definition.connector_id,
                display_name=definition.display_name,
                principal_id=definition.principal_id,
                configuration=configuration,
                credential_target=self.credential_target(definition),
                secret_store=self._secret_store,
            )
        if isinstance(configuration, _ENTERPRISE_CONFIGURATIONS):
            credential_target = self.credential_target(definition)
            return GatewayBackedContextProvider(
                connector_id=definition.connector_id,
                display_name=definition.display_name,
                kind=definition.kind,
                configuration=configuration,
                principal_id=definition.principal_id,
                index=self._index,
                bearer_token=(
                    (lambda: self._enterprise_oidc.access_token(credential_target))
                    if self._enterprise_oidc is not None
                    else None
                ),
            )
        assert isinstance(configuration, ODataConnectorConfig)
        mappings = definition.resolved_entity_mappings()
        if definition.kind is ConnectorKind.sap and not mappings:
            mappings = _SAP_MAPPINGS
        if definition.kind is ConnectorKind.dynamics365 and not mappings:
            mappings = _DYNAMICS_MAPPINGS
        return ODataContextProvider(
            connector_id=definition.connector_id,
            display_name=definition.display_name,
            kind=definition.kind,
            base_url=configuration.base_url,
            principal_id=definition.principal_id,
            credential_target=self.credential_target(definition),
            secret_store=self._secret_store,
            entity_mappings=mappings,
        )

    async def upsert(
        self, definition: ConnectorDefinition, credential: str | None
    ) -> ConnectorHealth:
        async with self._mutation_lock:
            definition = definition.model_copy(update={"principal_id": self._principal_id})
            # Validate before changing either persistence system. Credentials are
            # bound to their origin/tenant so old and new providers can never
            # resolve the same target during an in-flight reconfiguration.
            provider = self._provider(definition)
            previous = await asyncio.to_thread(
                self._index.connector_definition, definition.connector_id
            )
            previous_target = (
                self.credential_target(previous) if previous is not None else None
            )
            target = self.credential_target(definition)
            binding_changed = previous_target is not None and previous_target != target

            await self._broker.remove(definition.connector_id)
            if binding_changed:
                await asyncio.to_thread(self._secret_store.delete, previous_target)
                self._microsoft_auth.cancel(definition.connector_id)
                if self._enterprise_oidc is not None:
                    self._enterprise_oidc.cancel(definition.connector_id)
            # Remove the pre-fingerprint credential format rather than migrating
            # an unbound token into a new security context.
            await asyncio.to_thread(
                self._secret_store.delete,
                self.legacy_credential_target(definition.connector_id),
            )
            if credential is not None:
                await asyncio.to_thread(self._secret_store.put, target, credential)
            try:
                await asyncio.to_thread(self._index.save_connector, definition)
            except Exception:
                if credential is not None or binding_changed:
                    await asyncio.to_thread(self._secret_store.delete, target)
                raise
            self._broker.register(provider)
            self._definitions[definition.connector_id] = definition
            self._rebuild_mapped_glossary()
            try:
                return await provider.health()
            except Exception:
                return ConnectorHealth(
                    connector_id=provider.connector_id,
                    kind=provider.kind,
                    display_name=provider.display_name,
                    phase=ConnectorPhase.unavailable,
                    auth_enabled=provider.auth_enabled,
                    detail_code="provider_unavailable",
                )

    async def start_microsoft_authorization(
        self, connector_id: str
    ) -> DeviceAuthorization:
        async with self._mutation_lock:
            definition = await asyncio.to_thread(
                self._index.connector_definition, connector_id
            )
            if definition is None:
                raise KeyError(connector_id)
            if definition.kind is not ConnectorKind.microsoft_graph:
                raise ValueError("connector does not use Microsoft authentication")
            if definition.tenant_id is None or definition.client_id is None:
                raise ValueError("Microsoft OAuth configuration is incomplete")
            return await asyncio.to_thread(
                self._microsoft_auth.start,
                connector_id=connector_id,
                tenant_id=definition.tenant_id,
                client_id=definition.client_id,
            )

    async def poll_microsoft_authorization(self, connector_id: str) -> str:
        async with self._mutation_lock:
            definition = await asyncio.to_thread(
                self._index.connector_definition, connector_id
            )
            if definition is None:
                raise KeyError(connector_id)
            if definition.kind is not ConnectorKind.microsoft_graph:
                raise ValueError("connector does not use Microsoft authentication")
            return await asyncio.to_thread(
                self._microsoft_auth.poll,
                connector_id,
                self.credential_target(definition),
            )

    def start_enterprise_authorization(
        self, connector_id: str
    ) -> EnterpriseAuthorization:
        definition = self.connector_definition(connector_id)
        if not isinstance(definition.resolved_configuration(), _ENTERPRISE_CONFIGURATIONS):
            raise ValueError("connector does not use enterprise gateway authorization")
        if self._enterprise_oidc is None:
            raise OSError("enterprise gateway OIDC is not configured")
        return self._enterprise_oidc.start(
            connector_id, self.credential_target(definition)
        )

    async def complete_enterprise_authorization(
        self, connector_id: str, *, code: str, state: str
    ) -> ConnectorHealth:
        definition = self.connector_definition(connector_id)
        if not isinstance(definition.resolved_configuration(), _ENTERPRISE_CONFIGURATIONS):
            raise ValueError("connector does not use enterprise gateway authorization")
        if self._enterprise_oidc is None:
            raise OSError("enterprise gateway OIDC is not configured")
        await asyncio.to_thread(
            self._enterprise_oidc.complete,
            connector_id,
            code=code,
            state=state,
        )
        provider = self._provider(definition)
        await self._broker.remove(connector_id)
        self._broker.register(provider)
        return await provider.health()

    def start_slack_authorization(
        self, connector_id: str, redirect_uri: str
    ) -> SlackPkceAuthorization:
        definition = self.connector_definition(connector_id)
        configuration = definition.resolved_configuration()
        if not isinstance(configuration, SlackConnectorConfig):
            raise ValueError("connector does not use Slack authorization")
        return self._slack_auth.start(
            connector_id, configuration.client_id, redirect_uri
        )

    async def complete_slack_authorization(
        self,
        connector_id: str,
        *,
        state: str,
        code: str,
        redirect_uri: str,
    ) -> ConnectorHealth:
        definition = self.connector_definition(connector_id)
        configuration = definition.resolved_configuration()
        if not isinstance(configuration, SlackConnectorConfig):
            raise ValueError("connector does not use Slack authorization")
        credentials = await asyncio.to_thread(
            self._slack_auth.exchange,
            connector_id=connector_id,
            client_id=configuration.client_id,
            state=state,
            code=code,
            redirect_uri=redirect_uri,
        )
        configured_team = configuration.team_id
        if configured_team and credentials.get("team_id") != configured_team:
            raise PermissionError("Slack authorization is for another workspace")
        return await self.upsert(definition, json.dumps(credentials))

    async def health(self) -> list[ConnectorHealth]:
        return await self._broker.health()

    def entity_glossary(self) -> EntityGlossary:
        return self._glossary

    def entity_glossary_with_mappings(self) -> list[EntityGlossaryEntry]:
        entries = [*self._glossary.entries, *self._mapped_glossary]
        profile = getattr(self, "_improvement_profiles", {}).get("glossary_alias", {})
        canonical = profile.get("canonical_name")
        alias = profile.get("alias")
        if isinstance(canonical, str) and isinstance(alias, str):
            entries = [
                entry.model_copy(
                    update={"aliases": list(dict.fromkeys([*entry.aliases, alias]))}
                )
                if entry.canonical_name == canonical
                else entry
                for entry in entries
            ]
        return entries

    def _rebuild_mapped_glossary(self) -> None:
        entries: list[EntityGlossaryEntry] = []
        known_ids = {entry.entry_id for entry in self._glossary.entries}
        for definition in self._definitions.values():
            system_hash = hashlib.sha256(
                definition.connector_id.encode()
            ).hexdigest()[:16]
            system_id = f"mapping-system-{system_hash}"
            if system_id not in known_ids:
                entries.append(
                    EntityGlossaryEntry(
                        entry_id=system_id,
                        kind=EntityGlossaryKind.system,
                        canonical_name=definition.display_name[:120],
                    )
                )
                known_ids.add(system_id)
            for mapping in definition.resolved_entity_mappings():
                entry_id = (
                    "mapping-object-"
                    + hashlib.sha256(
                        f"{definition.connector_id}\0{mapping.entity_name}".encode()
                    ).hexdigest()[:16]
                )
                if entry_id in known_ids:
                    continue
                entries.append(
                    EntityGlossaryEntry(
                        entry_id=entry_id,
                        kind=EntityGlossaryKind.business_object,
                        canonical_name=mapping.entity_name[:120],
                    )
                )
                known_ids.add(entry_id)
            configuration = definition.resolved_configuration()
            if isinstance(configuration, NetSuiteConnectorConfig):
                for mapping in configuration.entity_mappings:
                    entry_id = (
                        "mapping-object-"
                        + hashlib.sha256(
                            f"{definition.connector_id}\0{mapping.record_type}".encode()
                        ).hexdigest()[:16]
                    )
                    if entry_id in known_ids:
                        continue
                    entries.append(
                        EntityGlossaryEntry(
                            entry_id=entry_id,
                            kind=EntityGlossaryKind.business_object,
                            canonical_name=mapping.record_type[:120],
                        )
                    )
                    known_ids.add(entry_id)
            elif isinstance(configuration, WmsConnectorConfig):
                for mapping in configuration.entity_mappings:
                    entry_id = (
                        "mapping-object-"
                        + hashlib.sha256(
                            f"{definition.connector_id}\0{mapping.entity_name}".encode()
                        ).hexdigest()[:16]
                    )
                    if entry_id in known_ids:
                        continue
                    entries.append(
                        EntityGlossaryEntry(
                            entry_id=entry_id,
                            kind=EntityGlossaryKind.business_object,
                            canonical_name=mapping.entity_name[:120],
                        )
                    )
                    known_ids.add(entry_id)
            elif isinstance(configuration, AmazonFbaConnectorConfig):
                for entity_name in (
                    "fba_inventory",
                    "fba_inbound_shipments",
                    "fba_inbound_items",
                ):
                    entry_id = (
                        "mapping-object-"
                        + hashlib.sha256(
                            f"{definition.connector_id}\0{entity_name}".encode()
                        ).hexdigest()[:16]
                    )
                    if entry_id in known_ids:
                        continue
                    entries.append(
                        EntityGlossaryEntry(
                            entry_id=entry_id,
                            kind=EntityGlossaryKind.business_object,
                            canonical_name=entity_name[:120],
                        )
                    )
                    known_ids.add(entry_id)
        self._mapped_glossary = entries

    async def save_external_space(
        self,
        connector_id: str,
        selection: ExternalSpaceSelection,
        external_id: str,
    ) -> ExternalSpaceSelection:
        async with self._mutation_lock:
            definition = self._definitions.get(connector_id)
            if definition is None:
                raise KeyError(connector_id)
            configuration = definition.resolved_configuration()
            if not isinstance(
                configuration, (MicrosoftTeamsConnectorConfig, SlackConnectorConfig)
            ):
                raise ValueError("connector does not support collaboration spaces")
            if (
                configuration.kind == "microsoft_teams"
                and not selection.kind.value.startswith("teams_")
            ):
                raise ValueError("space kind does not match Teams")
            if configuration.kind == "slack" and not selection.kind.value.startswith("slack_"):
                raise ValueError("space kind does not match Slack")
            await asyncio.to_thread(
                self._index.save_external_space,
                connector_id,
                selection,
                external_id,
            )
            selected = list(
                dict.fromkeys(
                    [*configuration.selected_space_ids, selection.selection_id]
                )
            )
            updated_configuration = configuration.model_copy(
                update={"selected_space_ids": selected}
            )
            updated = definition.model_copy(
                update={"configuration": updated_configuration}
            )
            await asyncio.to_thread(self._index.save_connector, updated)
            await self._broker.remove(connector_id)
            self._definitions[connector_id] = updated
            self._broker.register(self._provider(updated))
            return selection

    def external_spaces(self, connector_id: str) -> list[ExternalSpaceSelection]:
        if connector_id not in self._definitions:
            raise KeyError(connector_id)
        return self._index.external_spaces(connector_id)

    async def delete_external_space(self, connector_id: str, selection_id: str) -> bool:
        async with self._mutation_lock:
            definition = self._definitions.get(connector_id)
            if definition is None:
                raise KeyError(connector_id)
            configuration = definition.resolved_configuration()
            if not isinstance(
                configuration, (MicrosoftTeamsConnectorConfig, SlackConnectorConfig)
            ):
                raise ValueError("connector does not support collaboration spaces")
            deleted = await asyncio.to_thread(
                self._index.delete_external_space, connector_id, selection_id
            )
            updated_configuration = configuration.model_copy(
                update={
                    "selected_space_ids": [
                        value
                        for value in configuration.selected_space_ids
                        if value != selection_id
                    ]
                }
            )
            updated = definition.model_copy(update={"configuration": updated_configuration})
            await asyncio.to_thread(self._index.save_connector, updated)
            await self._broker.remove(connector_id)
            self._definitions[connector_id] = updated
            self._broker.register(self._provider(updated))
            return deleted

    async def update_entity_glossary(self, requested: EntityGlossary) -> EntityGlossary:
        async with self._mutation_lock:
            if requested.revision != self._glossary.revision:
                raise RuntimeError("stale_glossary")
            aliases: dict[str, str] = {}
            for entry in requested.entries:
                for value in [entry.canonical_name, *entry.aliases]:
                    normalized = " ".join(value.casefold().split())
                    owner = aliases.setdefault(normalized, entry.entry_id)
                    if owner != entry.entry_id:
                        raise ValueError("glossary aliases must identify one canonical entity")
            updated = requested.model_copy(
                update={"revision": requested.revision + 1}
            )
            await asyncio.to_thread(self._index.save_entity_glossary, updated)
            self._glossary = updated
            self._rebuild_mapped_glossary()
            return updated

    async def sync(self, connector_id: str) -> int:
        return await self._broker.sync(connector_id)

    @staticmethod
    def enterprise_catalog() -> list[ConnectorCatalogEntry]:
        return enterprise_connector_catalog()

    async def save_source_selection(
        self,
        connector_id: str,
        selection: SourceSelection,
        external_id: str,
    ) -> SourceSelection:
        async with self._mutation_lock:
            definition = self._definitions.get(connector_id)
            if definition is None:
                raise KeyError(connector_id)
            configuration = definition.resolved_configuration()
            if not isinstance(
                configuration, (*_ENTERPRISE_CONFIGURATIONS, NotionConnectorConfig)
            ):
                raise ValueError("connector does not support enterprise source selection")
            if selection.connector_id != connector_id:
                raise ValueError("source selection belongs to another connector")
            if (
                isinstance(configuration, NotionConnectorConfig)
                and selection.kind.value != "space"
            ):
                raise ValueError("Notion selections must identify pages or spaces")
            if isinstance(configuration, NotionConnectorConfig):
                external_id = normalize_notion_page_id(external_id)
            saved = await asyncio.to_thread(
                self._index.save_source_selection, selection, external_id
            )
            await asyncio.to_thread(self._index.revoke_access_lease, connector_id)
            if isinstance(configuration, NotionConnectorConfig):
                await self._broker.remove(connector_id)
                self._broker.register(self._provider(definition))
            return saved

    def source_selections(self, connector_id: str) -> list[SourceSelection]:
        definition = self._definitions.get(connector_id)
        if definition is None:
            raise KeyError(connector_id)
        if not isinstance(
            definition.resolved_configuration(),
            (*_ENTERPRISE_CONFIGURATIONS, NotionConnectorConfig),
        ):
            raise ValueError("connector does not support enterprise source selection")
        return self._index.source_selections(connector_id)

    async def delete_source_selection(
        self, connector_id: str, selection_id: str
    ) -> bool:
        async with self._mutation_lock:
            definition = self._definitions.get(connector_id)
            if definition is None:
                raise KeyError(connector_id)
            deleted = await asyncio.to_thread(
                self._index.delete_source_selection, connector_id, selection_id
            )
            await asyncio.to_thread(self._index.revoke_access_lease, connector_id)
            if isinstance(
                definition.resolved_configuration(), NotionConnectorConfig
            ):
                await self._broker.remove(connector_id)
                self._broker.register(self._provider(definition))
            return deleted

    async def refresh_access_lease(self, connector_id: str) -> AccessLease:
        definition = self.connector_definition(connector_id)
        provider = self._provider(definition)
        if not isinstance(provider, GatewayBackedContextProvider):
            raise ValueError("connector does not use access leases")
        return await provider.refresh_lease()

    async def revoke_access(self, connector_id: str) -> bool:
        definition = self.connector_definition(connector_id)
        provider = self._provider(definition)
        if not isinstance(provider, GatewayBackedContextProvider):
            raise ValueError("connector does not use access leases")
        try:
            await provider.revoke()
        except OSError:
            # Local revocation and purge remain authoritative if the gateway is down.
            await asyncio.to_thread(self._index.revoke_access_lease, connector_id)
        return True

    async def enterprise_search(
        self, request: EnterpriseSearchRequest
    ) -> EnterpriseSearchResult:
        started = time.monotonic()
        candidate_ids = request.connector_ids or list(self._definitions)
        eligible: list[str] = []
        expired: list[str] = []
        for connector_id in candidate_ids:
            definition = self._definitions.get(connector_id)
            if definition is None:
                continue
            if request.source_kinds and definition.kind not in request.source_kinds:
                continue
            if isinstance(definition.resolved_configuration(), _ENTERPRISE_CONFIGURATIONS):
                lease = await asyncio.to_thread(self._index.access_lease, connector_id)
                if lease is None or not lease.is_active():
                    expired.append(connector_id)
                    continue
            eligible.append(connector_id)
        if not eligible:
            return EnterpriseSearchResult(
                status="partial" if expired else "unavailable",
                expired_lease_connector_ids=expired,
                elapsed_ms=round((time.monotonic() - started) * 1_000),
            )
        try:
            result = await asyncio.wait_for(
                self.search(
                    ContextSearchQuery(
                        text=request.text,
                        principal_id=self._principal_id,
                        connector_ids=eligible,
                        entity_types=request.entity_types,
                        source_selection_ids=request.source_selection_ids,
                        limit=min(request.limit, 20),
                    )
                ),
                timeout=request.deadline_ms / 1_000,
            )
        except TimeoutError:
            return EnterpriseSearchResult(
                status="timeout",
                expired_lease_connector_ids=expired,
                elapsed_ms=round((time.monotonic() - started) * 1_000),
            )
        citations: list[EnterpriseSearchCitation] = []
        for citation in result.citations:
            compared_at = citation.updated_at or citation.retrieved_at
            if request.date_from and compared_at < request.date_from:
                continue
            if request.date_to and compared_at > request.date_to:
                continue
            lease = await asyncio.to_thread(
                self._index.access_lease, citation.connector_id
            )
            freshness = "stale" if citation.stale else "current"
            citations.append(
                EnterpriseSearchCitation(
                    citation_id=citation.citation_id,
                    source_kind=citation.source_kind,
                    source_label=citation.source_label,
                    source_reference=citation.source_reference,
                    entity_type=citation.entity_type,
                    excerpt=citation.excerpt,
                    uri=citation.uri,
                    relation=citation.relation,
                    freshness=freshness,
                    retrieved_at=citation.retrieved_at,
                    source_updated_at=citation.updated_at,
                    rank=citation.rank,
                    score_reasons=["lexical_match", "source_acl"],
                    access_expires_at=lease.expires_at if lease else None,
                    local_indexed=self._index.document_count(citation.connector_id) > 0,
                )
            )
        unavailable = result.unavailable_connector_ids
        status = (
            "partial"
            if unavailable or expired
            else ("current" if citations else "unavailable")
        )
        return EnterpriseSearchResult(
            status=status,
            citations=citations[: request.limit],
            unavailable_connector_ids=unavailable,
            expired_lease_connector_ids=expired,
            elapsed_ms=round((time.monotonic() - started) * 1_000),
        )

    def connector_definition(self, connector_id: str) -> ConnectorDefinition:
        definition = self._definitions.get(connector_id)
        if definition is None:
            raise KeyError(connector_id)
        return definition

    def external_action_executor(
        self, connector_id: str
    ) -> ExternalActionExecutor:
        self.connector_definition(connector_id)
        raise PermissionError("direct external writes are disabled in API v13")

    async def quality_records(
        self, connector_id: str, *, changed_after=None
    ) -> dict[str, list[dict[str, object]]]:
        definition = self.connector_definition(connector_id)
        provider = self._provider(definition)
        if not isinstance(
            provider,
            (NetSuiteContextProvider, WmsContextProvider, AmazonFbaContextProvider),
        ):
            raise ValueError("connector does not support data-quality inspection")
        return await provider.quality_records(changed_after=changed_after)

    async def erp_analytics(self, request: ERPAnalyticsRequest) -> ERPAnalyticsRun:
        """Fetch bounded live rows, then aggregate without persisting source records."""

        async def fetch_source(connector_id: str) -> ERPAnalyticsSource | None:
            definition = self._definitions.get(connector_id)
            if definition is None:
                return None
            configuration = definition.resolved_configuration()
            if not isinstance(
                configuration,
                (NetSuiteConnectorConfig, WmsConnectorConfig, AmazonFbaConnectorConfig),
            ):
                return None
            try:
                records = await self.quality_records(
                    connector_id, changed_after=request.period_start
                )
            except (
                KeyError,
                OSError,
                PermissionError,
                RuntimeError,
                TimeoutError,
                ValueError,
            ):
                return None
            return ERPAnalyticsSource(
                connector_id=connector_id,
                source_kind=definition.kind,
                source_label=definition.display_name,
                mappings=configuration.analytics_mappings,
                records=records,
            )

        sources: list[ERPAnalyticsSource] = []
        unavailable: list[str] = []
        tasks = {
            asyncio.create_task(fetch_source(connector_id)): connector_id
            for connector_id in request.connector_ids
        }
        done, pending = await asyncio.wait(
            tasks, timeout=_ERP_ANALYTICS_DEADLINE_SECONDS
        )
        for task in done:
            connector_id = tasks[task]
            source = task.result()
            if source is None:
                unavailable.append(connector_id)
            else:
                sources.append(source)
        for task in pending:
            task.cancel()
            unavailable.append(tasks[task])
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        sources.sort(key=lambda value: value.connector_id)
        unavailable.sort()
        return analyze_erp_records(
            request,
            sources,
            unavailable_connector_ids=unavailable,
        )

    async def fulfillment_reconciliation(
        self, request: FulfillmentReconciliationRequest
    ) -> FulfillmentReconciliationRun:
        requested = [
            (request.netsuite_connector_id, ConnectorKind.netsuite, FulfillmentStage.netsuite),
            (request.wms_connector_id, ConnectorKind.wms, FulfillmentStage.wms),
            (
                request.amazon_fba_connector_id,
                ConnectorKind.amazon_fba,
                FulfillmentStage.amazon_fba,
            ),
        ]

        async def fetch(
            connector_id: str, expected_kind: ConnectorKind, stage: FulfillmentStage
        ) -> FulfillmentSource | None:
            definition = self._definitions.get(connector_id)
            if definition is None or definition.kind is not expected_kind:
                return None
            configuration = definition.resolved_configuration()
            if not isinstance(
                configuration,
                (NetSuiteConnectorConfig, WmsConnectorConfig, AmazonFbaConnectorConfig),
            ):
                return None
            try:
                records = await self.quality_records(connector_id)
            except (
                KeyError,
                OSError,
                PermissionError,
                RuntimeError,
                TimeoutError,
                ValueError,
            ):
                return None
            return FulfillmentSource(
                connector_id=connector_id,
                stage=stage,
                source_label=definition.display_name,
                mappings=configuration.fulfillment_mappings,
                records=records,
            )

        tasks = {
            asyncio.create_task(fetch(connector_id, kind, stage)): connector_id
            for connector_id, kind, stage in requested
        }
        done, pending = await asyncio.wait(
            tasks, timeout=_ERP_ANALYTICS_DEADLINE_SECONDS
        )
        sources: list[FulfillmentSource] = []
        unavailable: list[str] = []
        for task in done:
            source = task.result()
            if source is None:
                unavailable.append(tasks[task])
            else:
                sources.append(source)
        for task in pending:
            task.cancel()
            unavailable.append(tasks[task])
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        sources.sort(key=lambda value: value.stage.value)
        return reconcile_fulfillment_records(
            request,
            sources,
            unavailable_connector_ids=unavailable,
        )

    def local_artifact_source(self, artifact_id: str) -> Path:
        for definition in self._definitions.values():
            if definition.kind is not ConnectorKind.local_files:
                continue
            provider = self._provider(definition)
            assert isinstance(provider, LocalFilesContextProvider)
            source = provider.resolve_artifact_source(artifact_id)
            if source is not None:
                return source
        raise KeyError(artifact_id)

    async def search(self, query: ContextSearchQuery) -> ContextSearchResult:
        return await self._broker.search(
            query.model_copy(update={"principal_id": self._principal_id})
        )

    @property
    def evidence_repository(self) -> EncryptedEvidenceRepository:
        return self._evidence_repository

    def embedding_profile(self) -> EmbeddingProfile | None:
        return self._embedding_provider.profile if self._embedding_provider else None

    def ocr_status(self) -> str:
        return "ready" if self._ocr_provider is not None else "disabled"

    async def ocr_preflight(self) -> str:
        if self._ocr_provider is None:
            return "disabled"
        try:
            ready = await asyncio.to_thread(self._ocr_provider.preflight)
        except (OSError, RuntimeError, TimeoutError, ValueError):
            return "unavailable"
        return "ready" if ready else "degraded"

    def set_improvement_profiles(self, profiles: dict[str, dict[str, object]]) -> None:
        """Apply bounded declarative profiles without changing connector authority."""

        self._improvement_profiles = {
            key: dict(value) for key, value in profiles.items() if len(value) <= 32
        }

    def set_improvement_source_authority_deltas(
        self, values: dict[str, int | float]
    ) -> None:
        """Apply only bounded ranking deltas; connector ACLs and scopes are untouched."""

        parsed: dict[ConnectorKind, float] = {}
        for raw_kind, raw_delta in values.items():
            kind = ConnectorKind(raw_kind)
            delta = float(raw_delta)
            if not -10.0 <= delta <= 10.0:
                raise ValueError("improvement source-authority delta is out of bounds")
            parsed[kind] = delta
        self._improvement_source_authority_delta = parsed

    def _effective_source_authority(self, definition: ConnectorDefinition) -> float:
        delta = self._improvement_source_authority_delta.get(definition.kind, 0.0)
        return max(0.0, min(100.0, definition.source_authority + delta))

    def retrieval_engine(self) -> LocalHybridRetrievalEngine:
        return LocalHybridRetrievalEngine(
            self._evidence_repository,
            source_labels={
                connector_id: definition.display_name
                for connector_id, definition in self._definitions.items()
            },
            source_authority={
                connector_id: self._effective_source_authority(definition)
                for connector_id, definition in self._definitions.items()
            },
            embedding_provider=self._embedding_provider,
            embedding_store=self._evidence_repository,
        )

    async def retrieve_evidence(self, request: RetrievalRequest) -> EvidenceBundle:
        """Combine encrypted local retrieval with live-only remote connector results."""

        request = request.model_copy(update={"principal_id": self._principal_id})
        selected = set(request.connector_ids)
        local_ids = {
            connector_id
            for connector_id, definition in self._definitions.items()
            if definition.kind is ConnectorKind.local_files
            and (not selected or connector_id in selected)
        }
        remote_ids = [
            connector_id
            for connector_id, definition in self._definitions.items()
            if definition.kind is not ConnectorKind.local_files
            and (not selected or connector_id in selected)
        ]
        local_request = request.model_copy(update={"connector_ids": sorted(local_ids)})
        local_task = (
            self.retrieval_engine().retrieve(local_request)
            if local_ids
            else asyncio.sleep(0, result=EvidenceBundle())
        )
        remote_task = (
            self.search(
                ContextSearchQuery(
                    text=request.text,
                    principal_id=self._principal_id,
                    connector_ids=remote_ids,
                    entity_types=[],
                    limit=request.limit,
                )
            )
            if remote_ids
            else None
        )
        try:
            if remote_task is None:
                local = await asyncio.wait_for(local_task, timeout=2.0)
                return local
            local, remote = await asyncio.wait_for(
                asyncio.gather(local_task, remote_task), timeout=2.0
            )
        except TimeoutError:
            return EvidenceBundle(status="timeout", lexical_only=True)
        citations = list(local.citations)
        for citation in remote.citations:
            authority = (
                self._effective_source_authority(
                    self._definitions[citation.connector_id]
                )
                / 100
                if citation.connector_id in self._definitions
                else 0.5
            )
            citations.append(
                EvidenceCitation(
                    citation_id=hashlib.sha256(
                        f"remote\0{citation.citation_id}".encode()
                    ).hexdigest(),
                    connector_id=citation.connector_id,
                    source_kind=citation.source_kind,
                    source_label=citation.source_label,
                    source_reference=citation.source_reference,
                    document_kind=DocumentKind.general,
                    excerpt=citation.excerpt,
                    uri=citation.uri,
                    locator=ArtifactLocator(),
                    retrieved_at=citation.retrieved_at,
                    updated_at=citation.updated_at,
                    relation=citation.relation.value,
                    rank=citation.rank,
                    score=RetrievalScore(
                        lexical_rank=citation.rank,
                        reciprocal_rank_score=(1 / (60 + citation.rank)) + authority * 0.005,
                        source_authority_boost=authority,
                    ),
                )
            )
        citations.sort(
            key=lambda value: (-value.score.reciprocal_rank_score, value.citation_id)
        )
        citations = [
            citation.model_copy(update={"rank": rank})
            for rank, citation in enumerate(citations[: request.limit], start=1)
        ]
        return EvidenceBundle(
            status=(
                "unavailable"
                if remote.unavailable_connector_ids and not citations
                else "current"
            ),
            citations=citations,
            unavailable_connector_ids=remote.unavailable_connector_ids,
            lexical_only=local.lexical_only,
            elapsed_ms=local.elapsed_ms,
        )

    async def erp_metadata_snapshot(self, connector_id: str) -> ERPMetadataSnapshot:
        definition = self._definitions.get(connector_id)
        if definition is None:
            raise KeyError(connector_id)
        if definition.kind not in {
            ConnectorKind.odata,
            ConnectorKind.sap,
            ConnectorKind.dynamics365,
            ConnectorKind.netsuite,
            ConnectorKind.wms,
            ConnectorKind.amazon_fba,
        }:
            raise ValueError("connector does not expose ERP metadata")
        provider = self._provider(definition)
        if not isinstance(
            provider,
            (
                ODataContextProvider,
                NetSuiteContextProvider,
                WmsContextProvider,
                AmazonFbaContextProvider,
            ),
        ):
            raise ValueError("connector does not expose ERP metadata")
        return await asyncio.wait_for(provider.metadata_snapshot(), timeout=2.0)

    async def prepare_consistency_sources(
        self,
        document_connector_ids: list[str],
        observed_connector_ids: list[str],
    ) -> tuple[
        list[ContextDocument],
        list[ERPMetadataSnapshot],
        list[str],
        set[str],
    ]:
        """Refresh selected read-only sources without making assurance a runtime dependency."""

        transient_documents, unavailable = await self.prepare_assurance_sources(
            document_connector_ids
        )
        snapshots: list[ERPMetadataSnapshot] = []
        leased: set[str] = set()
        for connector_id in observed_connector_ids:
            definition = self._definitions.get(connector_id)
            if definition is None:
                unavailable.append(connector_id)
                continue
            configuration = definition.resolved_configuration()
            if isinstance(configuration, _ENTERPRISE_CONFIGURATIONS):
                leased.add(connector_id)
            try:
                if definition.kind in {
                    ConnectorKind.odata,
                    ConnectorKind.sap,
                    ConnectorKind.dynamics365,
                    ConnectorKind.netsuite,
                    ConnectorKind.wms,
                    ConnectorKind.amazon_fba,
                }:
                    snapshots.append(await self.erp_metadata_snapshot(connector_id))
                else:
                    await asyncio.wait_for(self.sync(connector_id), timeout=30.0)
            except (KeyError, OSError, RuntimeError, TimeoutError, ValueError):
                unavailable.append(connector_id)
        return (
            transient_documents,
            snapshots,
            list(dict.fromkeys(unavailable)),
            leased,
        )

    async def assurance_documents(
        self, connector_ids: list[str]
    ) -> tuple[list[ContextDocument], list[str]]:
        """Fetch remote documents into memory for one bounded assurance run."""

        documents: list[ContextDocument] = []
        unavailable: list[str] = []
        for connector_id in connector_ids:
            definition = self._definitions.get(connector_id)
            if definition is None:
                unavailable.append(connector_id)
                continue
            if definition.kind is ConnectorKind.local_files:
                continue
            if definition.kind not in {
                ConnectorKind.microsoft_graph,
                ConnectorKind.notion,
            }:
                continue
            provider = self._provider(definition)
            try:
                if isinstance(
                    provider, (MicrosoftGraphContextProvider, NotionContextProvider)
                ):
                    documents.extend(
                        await asyncio.wait_for(
                            provider.assurance_documents(), timeout=30.0
                        )
                    )
            except (OSError, RuntimeError, TimeoutError, ValueError):
                unavailable.append(connector_id)
        return documents, unavailable

    async def prepare_assurance_sources(
        self, connector_ids: list[str]
    ) -> tuple[list[ContextDocument], list[str]]:
        """Refresh each selected local source independently, then fetch live remote files."""

        unavailable: list[str] = []
        for connector_id in connector_ids:
            definition = self._definitions.get(connector_id)
            if definition is None:
                unavailable.append(connector_id)
                continue
            if definition.kind is not ConnectorKind.local_files:
                continue
            try:
                await self.sync(connector_id)
            except (KeyError, OSError, RuntimeError, TimeoutError, ValueError):
                unavailable.append(connector_id)
        documents, remote_unavailable = await self.assurance_documents(connector_ids)
        return documents, list(dict.fromkeys([*unavailable, *remote_unavailable]))

    async def question_context(
        self,
        state: MeetingState | None,
        request: ConnectedQuestionContextRequest,
    ) -> ConnectedQuestionBrief:
        resolution = resolve_question_context(
            state, request, principal_id=self._principal_id
        )
        current_version = state.version if state is not None else 0
        if resolution is None:
            return ConnectedQuestionBrief(
                status="stale",
                state_version=current_version,
                suggestion_id=request.suggestion_id,
            )
        result = await self._broker.search(resolution.query)
        status = (
            "unavailable"
            if result.unavailable_connector_ids and not result.citations
            else "current"
        )
        return ConnectedQuestionBrief(
            status=status,
            state_version=current_version,
            suggestion_id=request.suggestion_id,
            citations=result.citations,
            unavailable_connector_ids=result.unavailable_connector_ids,
        )

    async def problem_context(
        self,
        state: MeetingState | None,
        request: ConnectedProblemContextRequest,
    ) -> ConnectedProblemBrief:
        current_version = state.version if state is not None else 0
        if state is None or state.version != request.state_version:
            return ConnectedProblemBrief(
                status="stale",
                state_version=current_version,
                pain_id=request.pain_id,
            )
        resolution = resolve_problem_context(
            state, request, principal_id=self._principal_id
        )
        if resolution is None:
            return ConnectedProblemBrief(
                status="problem_not_found",
                state_version=current_version,
                pain_id=request.pain_id,
            )
        result = await self.retrieve_evidence(
            RetrievalRequest(
                text=resolution.query.text,
                principal_id=self._principal_id,
                connector_ids=resolution.query.connector_ids,
                entity_hints=resolution.query.preferred_entity_types,
                business_keys=[
                    value
                    for values in resolution.query.known_values.values()
                    for value in values
                ][:32],
                limit=request.limit,
            )
        )
        if result.status == "timeout":
            return ConnectedProblemBrief(
                status="timeout",
                state_version=current_version,
                pain_id=request.pain_id,
            )
        citations = [
            PublicContextCitation(
                citation_id=citation.citation_id,
                connector_id=citation.connector_id,
                source_kind=citation.source_kind,
                source_label=citation.source_label,
                source_reference=citation.source_reference,
                entity_type=citation.document_kind.value,
                excerpt=citation.excerpt,
                uri=citation.uri,
                retrieved_at=citation.retrieved_at,
                updated_at=citation.updated_at,
                relation=citation.relation,
                rank=citation.rank,
            )
            for citation in result.citations
        ]
        status = (
            "unavailable"
            if result.unavailable_connector_ids and not result.citations
            else "current"
        )
        return ConnectedProblemBrief(
            status=status,
            state_version=current_version,
            pain_id=request.pain_id,
            citations=citations,
            unavailable_connector_ids=result.unavailable_connector_ids,
        )

    async def record_citation_feedback(self, feedback: dict[str, object]) -> None:
        await asyncio.to_thread(
            self._index.record_citation_feedback,
            feedback,
        )

    async def diagnostics(self) -> ContextDiagnostics:
        persisted = await asyncio.to_thread(self._index.diagnostics)
        return ContextDiagnostics(**self._broker.diagnostics(), **persisted)

    def _retry_pending_credential_cleanup_sync(
        self, connector_id: str | None = None
    ) -> tuple[bool, bool]:
        deleted = False
        failed = False
        try:
            targets = self._index.pending_credential_cleanup(connector_id)
        except Exception:
            return False, True
        for target_connector_id, target in targets:
            try:
                deleted = self._secret_store.delete(target) or deleted
                self._index.clear_credential_cleanup(target_connector_id, target)
            except Exception:
                failed = True
        return deleted, failed

    async def remove(self, connector_id: str) -> bool:
        async with self._mutation_lock:
            definition = await asyncio.to_thread(
                self._index.connector_definition, connector_id
            )
            self._microsoft_auth.cancel(connector_id)
            if self._enterprise_oidc is not None:
                self._enterprise_oidc.cancel(connector_id)
            targets = [self.legacy_credential_target(connector_id)]
            if definition is not None:
                targets.append(self.credential_target(definition))
            await asyncio.to_thread(
                self._index.remember_credential_cleanup, connector_id, targets
            )
            removed = await self._broker.remove(connector_id)
            indexed = await asyncio.to_thread(
                self._index.delete_connector, connector_id
            )
            artifacts = await asyncio.to_thread(
                self._evidence_repository.purge_connector_artifacts, connector_id
            )
            self._definitions.pop(connector_id, None)
            self._rebuild_mapped_glossary()
            deleted, credential_error = await asyncio.to_thread(
                self._retry_pending_credential_cleanup_sync, connector_id
            )
            if credential_error:
                raise OSError("connector credential could not be deleted")
            return removed or indexed or artifacts > 0 or deleted

    async def delete_all(self) -> None:
        async with self._mutation_lock:
            definitions = await asyncio.to_thread(self._index.connector_definitions)
            for definition in definitions:
                await asyncio.to_thread(
                    self._index.remember_credential_cleanup,
                    definition.connector_id,
                    [
                        self.credential_target(definition),
                        self.legacy_credential_target(definition.connector_id),
                    ],
                )
            await self._broker.close()
            self._definitions.clear()
            self._mapped_glossary.clear()
            for definition in definitions:
                self._microsoft_auth.cancel(definition.connector_id)
                if self._enterprise_oidc is not None:
                    self._enterprise_oidc.cancel(definition.connector_id)
            await asyncio.to_thread(self._index.delete_all)
            for definition in definitions:
                await asyncio.to_thread(
                    self._evidence_repository.purge_connector_artifacts,
                    definition.connector_id,
                )
            _, credential_error = await asyncio.to_thread(
                self._retry_pending_credential_cleanup_sync
            )
            if credential_error:
                raise OSError("one or more connector credentials could not be deleted")

    async def close(self) -> None:
        await self._broker.close()
