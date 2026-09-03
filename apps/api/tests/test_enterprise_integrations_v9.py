from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import io
import json
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from pydantic import ValidationError

from app.adapters.assurance.rule_packs import signing_key
from app.adapters.context import collaboration
from app.adapters.context.collaboration import (
    MicrosoftTeamsContextProvider,
    SlackContextProvider,
    SlackPkceManager,
)
from app.adapters.context.encrypted_index import EncryptedContextIndex
from app.adapters.context.netsuite import (
    NetSuiteContextProvider,
    build_client_assertion,
    build_suiteql,
    netsuite_account_host,
)
from app.adapters.context.service import ContextConnectorService
from app.adapters.integrations.rule_packs import verify_data_quality_rule_pack
from app.adapters.integrations.store import EncryptedIntegrationRepository
from app.adapters.integrations.workspace import IntegrationWorkspace
from app.domain.context import (
    ConnectorDefinition,
    ConnectorKind,
    ContextSearchQuery,
    ExternalSpaceKind,
    ExternalSpaceSelection,
    MicrosoftTeamsConnectorConfig,
    NetSuiteConnectorConfig,
    NetSuiteEntityMapping,
    NetSuiteReviewTargetMapping,
    SlackConnectorConfig,
)
from app.domain.integrations import (
    DataQualityFinding,
    DeliveryStatus,
    DocumentRoutingDestination,
    ExternalActionCreateRequest,
    ExternalDeliveryResult,
)


class XorProtector:
    def protect(self, value: bytes) -> bytes:
        return bytes(byte ^ 0xA5 for byte in value)

    def unprotect(self, value: bytes) -> bytes:
        return self.protect(value)


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def put(self, target: str, secret: str) -> None:
        self.values[target] = secret

    def get(self, target: str) -> str | None:
        return self.values.get(target)

    def delete(self, target: str) -> bool:
        return self.values.pop(target, None) is not None


class FakeTransport:
    def __init__(self, responses: list[tuple[int, dict[str, object]]]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, bytes | None]] = []

    def request_json(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, dict[str, object]]:
        self.requests.append((url, method, body))
        return self.responses.pop(0)


def mapping() -> NetSuiteEntityMapping:
    return NetSuiteEntityMapping(
        record_type="customer",
        key_field="entityid",
        display_field="companyname",
        searchable_fields=["entityid", "companyname"],
        projected_fields=["entityid", "companyname", "lastmodifieddate"],
    )


def netsuite_configuration() -> NetSuiteConnectorConfig:
    return NetSuiteConnectorConfig(
        kind="netsuite",
        account_id="1234567_SB1",
        client_id="client-identifier",
        certificate_id="certificate-1",
        entity_mappings=[mapping()],
        review_target=NetSuiteReviewTargetMapping(
            record_type="customrecord_copilot_review",
            external_id_field="custrecord_copilot_fingerprint",
            title_field="custrecord_copilot_title",
            severity_field="custrecord_copilot_severity",
            source_reference_field="custrecord_copilot_reference",
        ),
    )


def test_netsuite_host_and_suiteql_are_strict_and_literal_safe() -> None:
    assert netsuite_account_host("1234567_SB1") == (
        "1234567-sb1.suitetalk.api.netsuite.com"
    )
    with pytest.raises(ValueError):
        netsuite_account_host("example.com/path")
    query = build_suiteql(mapping(), ["O'Brien%' OR 1=1 --"])
    assert "O''BRIEN" in query.upper()
    assert "FROM customer" in query
    with pytest.raises(ValidationError):
        NetSuiteEntityMapping(
            record_type="customer; DELETE",
            key_field="id",
            display_field="id",
            searchable_fields=["id"],
            projected_fields=["id", "lastmodifieddate"],
        )


def test_netsuite_assertion_uses_ps256_and_account_audience() -> None:
    key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    assertion = build_client_assertion(netsuite_configuration(), pem, now=100)
    header_part, claims_part, _ = assertion.split(".")
    def decode(value: str) -> dict:
        return json.loads(
            base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        )
    assert decode(header_part)["alg"] == "PS256"
    assert decode(claims_part)["aud"] == (
        "https://1234567-sb1.suitetalk.api.netsuite.com"
        "/services/rest/auth/oauth2/v1/token"
    )
    assert decode(claims_part)["exp"] - decode(claims_part)["iat"] == 300


def test_v8_connector_definition_migrates_to_discriminated_configuration() -> None:
    definition = ConnectorDefinition(
        connector_id="legacy-erp",
        kind=ConnectorKind.odata,
        display_name="Legacy ERP",
        principal_id="windows-user:alice",
        base_url="https://erp.example.test/odata",
        entities={"Orders": ["OrderId", "Status"]},
    )
    configuration = definition.resolved_configuration()
    assert configuration.kind == "odata"
    assert definition.resolved_entity_mappings()[0].key_field == "OrderId"


def test_external_space_provider_ids_are_encrypted_and_scoped(tmp_path: Path) -> None:
    index = EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector())
    service = ContextConnectorService(
        index=index,
        secret_store=MemorySecretStore(),
        principal_id="windows-user:alice",
    )
    definition = ConnectorDefinition(
        connector_id="slack",
        kind=ConnectorKind.slack,
        display_name="Selected Slack",
        principal_id="ignored",
        configuration=SlackConnectorConfig(kind="slack", client_id="client-1"),
    )

    async def scenario() -> None:
        await service.upsert(definition, json.dumps({"user_token": "secret-user"}))
        selection = ExternalSpaceSelection(
            selection_id="finance-alerts",
            label="Finance alerts",
            kind=ExternalSpaceKind.slack_private_channel,
        )
        await service.save_external_space("slack", selection, "C012RAWCHANNEL")
        assert service.external_spaces("slack") == [selection]
        assert index.resolve_external_spaces("slack") == {
            "finance-alerts": "C012RAWCHANNEL"
        }
        await service.close()

    asyncio.run(scenario())
    raw = (tmp_path / "context.sqlite3").read_bytes()
    assert b"C012RAWCHANNEL" not in raw
    assert b"secret-user" not in raw


def test_slack_pkce_state_is_one_time_and_redirect_is_exact() -> None:
    manager = SlackPkceManager()
    with pytest.raises(ValueError):
        manager.start("slack", "client", "meeting-intelligence://oauth/slack/attacker")
    authorization = manager.start("slack", "client", "meeting-intelligence://oauth/slack")
    assert "code_challenge_method=S256" in authorization.authorization_url
    query = parse_qs(urlsplit(authorization.authorization_url).query, keep_blank_values=True)
    assert query["scope"] == [""]
    assert query["user_scope"] == ["search:read.public,search:read.private"]
    assert "chat:write" not in authorization.authorization_url
    verifier = manager.consume("slack", authorization.state)
    assert len(verifier) >= 43
    with pytest.raises(ValueError):
        manager.consume("slack", authorization.state)


def test_slack_has_no_bot_credential_or_notification_primitive() -> None:
    service_source = inspect.getsource(ContextConnectorService)
    collaboration_source = inspect.getsource(collaboration)

    assert "save_slack_bot_credential" not in service_source
    assert "chat.postMessage" not in collaboration_source
    assert "SlackNotificationExecutor" not in collaboration_source
    assert "TeamsNotificationExecutor" not in collaboration_source


def test_unsigned_or_tampered_data_quality_rules_are_rejected() -> None:
    private_key = Ed25519PrivateKey.generate()
    public = private_key.public_key().public_bytes_raw()
    trusted = signing_key(
        key_id="company-quality",
        label="Company Quality Office",
        public_key_base64=base64.b64encode(public).decode(),
    )
    rules = json.dumps(
        [
            {
                "rule_id": "company.customer.required",
                "pack_id": "company-quality",
                "kind": "required_value",
                "severity": "critical",
                "record_type": "customer",
                "fields": ["entityid"],
                "parameters": {"key_field": "entityid"},
            }
        ],
        separators=(",", ":"),
    ).encode()
    manifest = {
        "pack_id": "company-quality",
        "version": "1.0.0",
        "issuer": "Company Quality Office",
        "key_id": trusted.key_id,
        "rules_sha256": hashlib.sha256(rules).hexdigest(),
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("rules.json", rules)
        archive.writestr(
            "signature.ed25519",
            base64.b64encode(private_key.sign(canonical)),
        )
    pack = verify_data_quality_rule_pack(archive_bytes.getvalue(), [trusted])
    assert pack.trusted is True
    assert pack.rules[0].trusted is True
    with pytest.raises(PermissionError):
        verify_data_quality_rule_pack(archive_bytes.getvalue(), [])


def test_netsuite_and_chat_results_are_live_only_and_space_scoped(
    tmp_path: Path,
) -> None:
    key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    secrets_store = MemorySecretStore()
    secrets_store.put("netsuite-secret", json.dumps({"private_key_pem": pem}))
    netsuite_transport = FakeTransport(
        [
            (200, {"access_token": "token", "expires_in": 300}),
            (
                200,
                {
                    "items": [
                        {
                            "entityid": "CUSTOMER-100",
                            "companyname": "Approved customer",
                            "lastmodifieddate": "2026-08-30T00:00:00Z",
                        }
                    ]
                },
            ),
        ]
    )
    netsuite = NetSuiteContextProvider(
        connector_id="netsuite",
        display_name="NetSuite",
        principal_id="windows-user:alice",
        configuration=netsuite_configuration(),
        credential_target="netsuite-secret",
        secret_store=secrets_store,
        transport=netsuite_transport,
    )
    query = ContextSearchQuery(
        text="CUSTOMER-100",
        principal_id="windows-user:alice",
        connector_ids=["netsuite"],
    )
    documents = asyncio.run(netsuite.search(query))
    assert [document.source_reference for document in documents] == ["CUSTOMER-100"]
    assert netsuite_transport.requests[1][1] == "POST"
    assert b"customer-100" in (netsuite_transport.requests[1][2] or b"")

    secrets_store.put("slack-secret", json.dumps({"user_token": "user-token"}))
    slack_transport = FakeTransport(
        [
            (
                200,
                {
                    "ok": True,
                    "results": {
                        "messages": [
                            {"channel_id": "C-SELECTED", "ts": "1", "text": "selected"},
                            {"channel_id": "D-DIRECT", "ts": "2", "text": "dm"},
                        ]
                    },
                },
            )
        ]
    )
    selection = ExternalSpaceSelection(
        selection_id="selected",
        label="Selected channel",
        kind=ExternalSpaceKind.slack_private_channel,
    )
    slack = SlackContextProvider(
        connector_id="slack",
        display_name="Slack",
        principal_id="windows-user:alice",
        configuration=SlackConnectorConfig(
            kind="slack", client_id="client", selected_space_ids=["selected"]
        ),
        spaces=[selection],
        external_space_ids={"selected": "C-SELECTED"},
        credential_target="slack-secret",
        secret_store=secrets_store,
        transport=slack_transport,
    )
    slack_documents = asyncio.run(slack.search(query))
    assert [document.content for document in slack_documents] == ["selected"]
    assert b"C-SELECTED" in (slack_transport.requests[0][2] or b"")
    assert b"D-DIRECT" not in (slack_transport.requests[0][2] or b"")

    index = EncryptedContextIndex(tmp_path / "live-only.sqlite3", XorProtector())
    assert index.diagnostics()["local_index_documents"] == 0


def test_teams_paths_are_limited_to_selected_channels() -> None:
    key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    store = MemorySecretStore()
    store.put("teams-secret", json.dumps({"private_key_pem": pem}))
    transport = FakeTransport(
        [
            (200, {"access_token": "token", "expires_in": 300}),
            (
                200,
                {
                    "value": [
                        {
                            "id": "message-1",
                            "body": {"content": "Selected team update"},
                        }
                    ]
                },
            ),
        ]
    )
    selection = ExternalSpaceSelection(
        selection_id="architecture",
        label="Architecture",
        kind=ExternalSpaceKind.teams_channel,
    )
    provider = MicrosoftTeamsContextProvider(
        connector_id="teams",
        display_name="Teams",
        principal_id="windows-user:alice",
        configuration=MicrosoftTeamsConnectorConfig(
            kind="microsoft_teams",
            tenant_id="tenant.example",
            client_id="00000000-0000-0000-0000-000000000001",
            certificate_id="certificate",
            selected_space_ids=["architecture"],
        ),
        spaces=[selection],
        external_space_ids={"architecture": "team-id/channel-id"},
        credential_target="teams-secret",
        secret_store=store,
        transport=transport,
    )
    results = asyncio.run(
        provider.search(
            ContextSearchQuery(text="architecture", principal_id="windows-user:alice")
        )
    )
    assert [document.content for document in results] == ["Selected team update"]
    assert "/teams/team-id/channels/channel-id/messages" in transport.requests[1][0]
    assert "/chats/" not in transport.requests[1][0]


def test_findings_and_actions_are_revisioned_and_encrypted(tmp_path: Path) -> None:
    protector = XorProtector()
    repository = EncryptedIntegrationRepository(
        tmp_path / "integrations.sqlite3", protector
    )
    finding = DataQualityFinding(
        finding_id="finding-1",
        fingerprint="fingerprint-secret",
        run_id="run-1",
        connector_id="netsuite",
        rule_id="required.customer",
        severity="high",
        title="Required value missing",
        description="A configured field is empty.",
        source_reference="CUSTOMER-100",
    )
    repository.save_findings([finding])
    reviewed = finding.model_copy(update={"status": "confirmed", "revision": 1})
    repository.review_finding(reviewed, 0)
    with pytest.raises(RuntimeError):
        repository.review_finding(reviewed, 0)
    repository.assert_no_plaintext("CUSTOMER-100")


def test_netsuite_action_never_auto_approves(tmp_path: Path) -> None:
    index = EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector())
    service = ContextConnectorService(
        index=index,
        secret_store=MemorySecretStore(),
        principal_id="windows-user:alice",
    )
    definition = ConnectorDefinition(
        connector_id="netsuite",
        kind=ConnectorKind.netsuite,
        display_name="NetSuite review",
        principal_id="ignored",
        configuration=netsuite_configuration(),
    )
    repository = EncryptedIntegrationRepository(
        tmp_path / "integrations.sqlite3", XorProtector()
    )
    workspace = IntegrationWorkspace(context=service, repository=repository)
    finding = DataQualityFinding(
        finding_id="finding-1",
        fingerprint="fingerprint-1",
        run_id="run-1",
        connector_id="netsuite",
        rule_id="rule-1",
        severity="critical",
        title="Schema drift detected",
        description="A field changed.",
        source_reference="CUSTOMER-100",
        status="confirmed",
    )

    async def scenario() -> None:
        await service.upsert(definition, json.dumps({"private_key_pem": "not-used"}))
        repository.save_findings([finding])
        action = workspace.create_action(
            ExternalActionCreateRequest(
                connector_id="netsuite",
                destination_ref="netsuite_review",
                finding_id=finding.finding_id,
            )
        )
        assert action.status is DeliveryStatus.pending_confirmation
        with pytest.raises(PermissionError):
            await workspace.approve_action(
                action.action_id, action.revision, origin="trusted_rule"
            )
        await service.close()

    asyncio.run(scenario())


def test_routing_destination_path_is_secret_and_must_exist(tmp_path: Path) -> None:
    repository = EncryptedIntegrationRepository(
        tmp_path / "integrations.sqlite3", XorProtector()
    )
    root = tmp_path / "approved"
    root.mkdir()
    destination = DocumentRoutingDestination(
        destination_ref="approved-partners",
        label="Approved partner folder",
        partner_references=["CUSTOMER-100"],
    )
    repository.save_routing_destination(destination, str(root))
    assert repository.routing_destinations() == [destination]
    assert repository.routing_root("approved-partners") == root
    repository.assert_no_plaintext(str(root))


def test_ambiguous_delivery_is_terminal_and_not_retried(tmp_path: Path) -> None:
    class UnknownExecutor:
        calls = 0

        async def execute(self, action) -> ExternalDeliveryResult:
            self.calls += 1
            return ExternalDeliveryResult(
                status="delivery_unknown", detail_code="delivery_unknown"
            )

    class ActionContext:
        executor = UnknownExecutor()

        def connector_definition(self, connector_id: str) -> ConnectorDefinition:
            assert connector_id == "slack"
            return ConnectorDefinition(
                connector_id="slack",
                kind=ConnectorKind.slack,
                display_name="Slack",
                principal_id="windows-user:alice",
                configuration=SlackConnectorConfig(
                    kind="slack",
                    client_id="client",
                    selected_space_ids=["alerts"],
                ),
            )

        def external_spaces(self, connector_id: str) -> list[ExternalSpaceSelection]:
            assert connector_id == "slack"
            return [
                ExternalSpaceSelection(
                    selection_id="alerts",
                    label="Alerts",
                    kind=ExternalSpaceKind.slack_private_channel,
                )
            ]

        def external_action_executor(self, connector_id: str):
            assert connector_id == "slack"
            return self.executor

    repository = EncryptedIntegrationRepository(
        tmp_path / "integrations.sqlite3", XorProtector()
    )
    context = ActionContext()
    workspace = IntegrationWorkspace(context=context, repository=repository)  # type: ignore[arg-type]
    finding = DataQualityFinding(
        finding_id="finding",
        fingerprint="fingerprint",
        run_id="run",
        connector_id="netsuite",
        rule_id="trusted.rule",
        severity="critical",
        title="Critical data conflict",
        description="A deterministic rule found a conflict.",
        source_reference="CUSTOMER-100",
        status="confirmed",
    )
    repository.save_findings([finding])
    action = workspace.create_action(
        ExternalActionCreateRequest(
            connector_id="slack",
            destination_ref="alerts",
            finding_id="finding",
        )
    )
    with pytest.raises(PermissionError, match="direct external writes are disabled"):
        asyncio.run(workspace.approve_action(action.action_id, 0))
    assert action.status is DeliveryStatus.pending_confirmation
    assert context.executor.calls == 0
