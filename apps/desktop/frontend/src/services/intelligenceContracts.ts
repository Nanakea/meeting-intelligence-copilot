// Wire contracts mirrored from the Meeting Intelligence Copilot backend
// (apps/api/app/domain/contracts.py). Field names are snake_case on purpose:
// they must match the JSON the backend sends verbatim. Ported from the
// standalone dev UI (apps/web/src/types/contracts.ts) for the real Meetily
// integration (Phase 2/8).

export type Lang = "ja" | "en" | "ko";
export type FactKind = "evidence" | "inference";
export type FactStatus = "active" | "superseded" | "contradicted";
export type ExtractionOrigin = "deterministic" | "semantic_confirmed";
export type PainStatus = "open" | "mitigated";
export type PainIdentityStatus = "anchored" | "provisional";
export type GapStatus = "open" | "answered";
export type SuggestionRole = "ask_now" | "follow_up";
export type SuggestionStatus = "active" | "retracted";
export type ConnectorKind =
  | "local_files"
  | "microsoft_graph"
  | "odata"
  | "sap"
  | "dynamics365"
  | "netsuite"
  | "microsoft_teams"
  | "slack"
  | "notion"
  | "wms"
  | "amazon_fba"
  | "github"
  | "gitlab"
  | "jira"
  | "confluence"
  | "azure_devops"
  | "servicenow"
  | "sql"
  | "sftp"
  | "openapi";
export type ConnectorPhase =
  | "disconnected"
  | "connecting"
  | "ready"
  | "degraded"
  | "auth_required"
  | "unavailable";
export type EntitySearchMode = "server_filter" | "bounded_scan";
export type ContextRelation = "supporting" | "conflicting" | "reference";
export type EntityGlossaryKind =
  | "system"
  | "business_object"
  | "process"
  | "interface"
  | "business_capability"
  | "team"
  | "region"
  | "environment"
  | "standard"
  | "architecture_pattern";

export interface EntityGlossaryEntry {
  entry_id: string;
  kind: EntityGlossaryKind;
  canonical_name: string;
  aliases: string[];
  language?: Lang | null;
  enabled: boolean;
}

export interface EntityGlossary {
  revision: number;
  entries: EntityGlossaryEntry[];
}

export interface EntityMapping {
  entity_name: string;
  key_field: string;
  display_field: string;
  searchable_fields: string[];
  projected_fields: string[];
  search_mode: EntitySearchMode;
}

export type ExternalSpaceKind =
  | 'teams_channel'
  | 'teams_meeting_chat'
  | 'slack_public_channel'
  | 'slack_private_channel';

export interface ExternalSpaceSelection {
  selection_id: string;
  label: string;
  kind: ExternalSpaceKind;
  enabled: boolean;
}

export interface NetSuiteEntityMapping {
  record_type: string;
  key_field: string;
  display_field: string;
  searchable_fields: string[];
  projected_fields: string[];
  modified_field?: string | null;
}

export interface NetSuiteReviewTargetMapping {
  record_type: string;
  external_id_field: string;
  title_field: string;
  severity_field: string;
  source_reference_field: string;
  status_field?: string | null;
}

export interface WmsEntityMapping {
  entity_name: string;
  endpoint_path: string;
  key_field: string;
  display_field: string;
  searchable_fields: string[];
  projected_fields: string[];
  field_types: Record<string, string>;
  items_field: string;
  next_cursor_field: string;
  cursor_parameter: string;
  limit_parameter: string;
  search_parameter?: string | null;
  modified_since_parameter?: string | null;
  page_size: number;
  max_pages: number;
}

export type ConnectorConfiguration =
  | { kind: 'local_files'; root_path: string }
  | { kind: 'microsoft_graph'; tenant_id: string; client_id?: string | null; drive_ids: string[] }
  | { kind: 'odata' | 'sap' | 'dynamics365'; base_url: string; entity_mappings: EntityMapping[] }
  | { kind: 'netsuite'; account_id: string; client_id: string; certificate_id: string; entity_mappings: NetSuiteEntityMapping[]; review_target?: NetSuiteReviewTargetMapping | null }
  | { kind: 'microsoft_teams'; tenant_id: string; client_id: string; certificate_id: string; selected_space_ids: string[] }
  | { kind: 'slack'; client_id: string; team_id?: string | null; selected_space_ids: string[] }
  | { kind: 'notion'; workspace_label: string }
  | { kind: 'wms'; base_url: string; environment: string; entity_mappings: WmsEntityMapping[]; analytics_mappings: []; fulfillment_mappings: [] }
  | { kind: 'amazon_fba'; region: 'na' | 'eu' | 'fe'; lwa_client_id: string; marketplace_ids: string[]; include_inventory: boolean; include_inbound_shipments: boolean; inbound_lookback_days: number; max_pages: number; max_inbound_shipments: number; analytics_mappings: []; fulfillment_mappings: [] }
  | { kind: 'github' | 'gitlab'; base_url: string; client_id: string; gateway_url?: string | null; selected_source_ids: string[]; include_code: boolean; include_delivery_data: boolean }
  | { kind: 'jira' | 'confluence' | 'azure_devops' | 'servicenow'; base_url: string; client_id: string; gateway_url?: string | null; selected_source_ids: string[] }
  | { kind: 'sql'; server_label: string; approved_views: string[]; query_template_ids: string[]; gateway_url?: string | null }
  | { kind: 'sftp'; host: string; port: number; username: string; host_key_sha256: string; approved_roots: string[]; gateway_url?: string | null }
  | { kind: 'openapi'; base_url: string; specification_sha256: string; approved_operation_ids: string[]; gateway_url?: string | null };

export type SourceSelectionKind = 'repository' | 'project' | 'space' | 'table' | 'folder' | 'service';

export interface SourceSelection {
  selection_id: string;
  connector_id: string;
  kind: SourceSelectionKind;
  label: string;
  enabled: boolean;
}

export interface AccessLease {
  lease_id: string;
  connector_id: string;
  principal_hash: string;
  acl_digest: string;
  selection_ids: string[];
  issued_at: string;
  expires_at: string;
  status: 'active' | 'expired' | 'revoked';
}

export interface ConnectorCatalogEntry {
  kind: ConnectorKind;
  label: string;
  availability: 'built_in' | 'gateway' | 'signed_extension';
  authentication: 'none' | 'oauth_pkce' | 'github_app' | 'oidc' | 'certificate' | 'integrated' | 'secret';
  persistence: 'encrypted_lease' | 'metadata_only' | 'live_only';
  supports_webhooks: boolean;
  read_only: true;
}

export interface EnterpriseSearchRequest {
  text: string;
  connector_ids: string[];
  source_kinds: ConnectorKind[];
  source_selection_ids: string[];
  entity_types: string[];
  date_from?: string | null;
  date_to?: string | null;
  limit: number;
  deadline_ms: number;
}

export interface EnterpriseSearchCitation {
  citation_id: string;
  source_kind: ConnectorKind;
  source_label: string;
  source_reference: string;
  entity_type: string;
  excerpt: string;
  uri?: string | null;
  relation: ContextRelation;
  freshness: 'current' | 'stale' | 'unknown';
  retrieved_at: string;
  source_updated_at?: string | null;
  rank: number;
  score_reasons: string[];
  access_expires_at?: string | null;
  local_indexed: boolean;
  not_stated_in_meeting: true;
}

export interface EnterpriseSearchResult {
  status: 'current' | 'partial' | 'timeout' | 'unavailable';
  citations: EnterpriseSearchCitation[];
  unavailable_connector_ids: string[];
  expired_lease_connector_ids: string[];
  elapsed_ms: number;
}

export interface Speaker {
  id: string;
  display_name?: string | null;
  role?: string | null;
}

export interface TranscriptEvent {
  event_id: string;
  meeting_id: string;
  seq: number;
  speaker: Speaker;
  text: string;
  lang: Lang;
  ts_start: number;
  ts_end: number;
  is_final: boolean;
  source: string;
}

export interface MeetingFact {
  fact_id: string;
  meeting_id: string;
  pain_id: string;
  slot: string;
  value: string;
  kind: FactKind;
  extraction_origin: ExtractionOrigin;
  status: FactStatus;
  evidence_event_ids: string[];
  supersedes?: string | null;
  superseded_by?: string | null;
  contradicts: string[];
  confidence: number;
  created_seq: number;
}

export interface PainPoint {
  pain_id: string;
  meeting_id: string;
  template_id: string;
  title: string;
  instance_key: string;
  identity_aliases: string[];
  identity_revision: number;
  identity_status: PainIdentityStatus;
  subject?: string | null;
  merged_into_pain_id?: string | null;
  status: PainStatus;
  kind: FactKind;
  evidence_event_ids: string[];
  created_seq: number;
}

export interface InformationGap {
  gap_id: string;
  meeting_id: string;
  pain_id: string;
  slot: string;
  status: GapStatus;
  resolved_by_fact_id?: string | null;
  base_priority: number;
  reopen_count: number;
}

export interface QuestionSuggestion {
  suggestion_id: string;
  gap_id: string;
  role: SuggestionRole;
  text: string;
  reason: string;
  status: SuggestionStatus;
  priority: number;
}

export interface MeetingState {
  meeting_id: string;
  version: number;
  transcript: TranscriptEvent[];
  pain_points: PainPoint[];
  facts: MeetingFact[];
  gaps: InformationGap[];
  suggestions: QuestionSuggestion[];
  last_event_seq: number;
  ambiguous_routing_count: number;
  identity_decisions: ProblemIdentityDecision[];
  confirmed_semantic_hint_ids: string[];
}

export interface ProblemIdentityDecision {
  decision_id: string;
  action: "merge" | "keep_separate";
  pain_ids: string[];
  survivor_pain_id?: string | null;
  created_version: number;
}

export interface SemanticHintFact {
  slot: string;
  value: string;
  kind: FactKind;
  evidence_event_ids: string[];
}

export interface SemanticProblemHint {
  hint_id: string;
  session_id: string;
  category: string;
  subject: string;
  language: Lang;
  evidence_event_ids: string[];
  facts: SemanticHintFact[];
  confidence: number;
  state_version: number;
}

export interface ProblemIdentityReviewCandidate {
  review_id: string;
  category: string;
  first_pain_id: string;
  second_pain_id: string;
  first_subject: string;
  second_subject: string;
}

export type GovernanceKind =
  | 'decision'
  | 'risk'
  | 'dependency'
  | 'action'
  | 'assumption'
  | 'constraint'
  | 'architecture_impact'
  | 'responsibility';
export type GovernanceCandidateStatus = 'pending' | 'confirmed' | 'dismissed';
export type GovernanceRecordStatus = 'open' | 'monitoring' | 'resolved' | 'superseded';
export type GovernanceFieldProvenance = 'transcript' | 'user';

export type GovernancePayload =
  | { payload_type: 'decision'; statement: string; rationale?: string | null; alternatives: string[]; approver?: string | null }
  | { payload_type: 'risk'; statement: string; cause?: string | null; impact?: string | null; likelihood?: 'low' | 'medium' | 'high' | null; severity?: 'low' | 'medium' | 'high' | 'critical' | null; mitigation?: string | null; owner?: string | null; target_date?: string | null }
  | { payload_type: 'dependency'; statement: string; provider?: string | null; consumer?: string | null; deliverable?: string | null; needed_by?: string | null; owner?: string | null; fallback?: string | null }
  | { payload_type: 'action'; task: string; owner?: string | null; due_date?: string | null }
  | { payload_type: 'assumption'; statement: string; validation_owner?: string | null; validation_due?: string | null }
  | { payload_type: 'constraint'; statement: string; source?: string | null; affected_scope: string[]; exception_path?: string | null }
  | { payload_type: 'architecture_impact'; change: string; affected_entities: string[]; impact_areas: string[]; decision_required: boolean }
  | { payload_type: 'responsibility'; subject: string; responsible: string[]; accountable: string[]; consulted: string[]; informed: string[] };

export interface GovernanceEvidenceRef {
  session_id: string;
  evidence_event_ids: string[];
}

export interface GovernanceCandidate {
  candidate_id: string;
  workspace_id: string;
  session_id: string;
  state_version: number;
  kind: GovernanceKind;
  title: string;
  payload: GovernancePayload;
  language: Lang;
  fingerprint: string;
  evidence_event_ids: string[];
  linked_pain_ids: string[];
  linked_entity_ids: string[];
  status: GovernanceCandidateStatus;
  created_seq: number;
}

export interface GovernanceRecord {
  record_id: string;
  workspace_id: string;
  kind: GovernanceKind;
  title: string;
  payload: GovernancePayload;
  status: GovernanceRecordStatus;
  revision: number;
  field_provenance: Record<string, GovernanceFieldProvenance>;
  evidence: GovernanceEvidenceRef[];
  source_session_ids: string[];
  linked_pain_ids: string[];
  linked_entity_ids: string[];
  superseded_by?: string | null;
  needs_provenance_review: boolean;
  created_at: string;
  updated_at: string;
}

export interface GovernanceAlert {
  alert_id: string;
  record_id: string;
  kind: 'ownerless' | 'overdue' | 'aging_decision' | 'critical_dependency' | 'provenance_review';
  severity: 'info' | 'warning' | 'critical';
  label: string;
}

export interface SolutionPortfolio {
  records: GovernanceRecord[];
  alerts: GovernanceAlert[];
  counts: Record<string, number>;
}

export type SystemEntityKind =
  | 'system'
  | 'interface'
  | 'process'
  | 'data_object'
  | 'business_capability'
  | 'team'
  | 'region'
  | 'environment'
  | 'standard'
  | 'architecture_pattern';

export interface SystemEntity {
  entity_id: string;
  kind: SystemEntityKind;
  label: string;
  aliases: string[];
  confirmed: boolean;
}

export interface SystemRelationship {
  relationship_id: string;
  source_entity_id: string;
  target_entity_id: string;
  relation: 'integrates_with' | 'depends_on' | 'owns' | 'source_of_truth' | 'exchanges' | 'supports' | 'governed_by';
  confirmed: boolean;
  evidence: GovernanceEvidenceRef[];
}

export interface SolutionBrief {
  language: Lang;
  records: GovernanceRecord[];
  alerts: GovernanceAlert[];
  context_citations: ContextCitation[];
  context_status: 'not_requested' | 'current' | 'unavailable' | 'timeout';
}

export interface ImpactAnalysis {
  root_entity_id: string;
  entities: SystemEntity[];
  relationships: SystemRelationship[];
  paths: Array<{ entity_ids: string[]; relationship_ids: string[] }>;
  context_citations: ContextCitation[];
  context_status: 'not_requested' | 'current' | 'unavailable' | 'timeout';
}

export interface LiveIntelligenceSnapshot {
  meeting_id: string;
  version: number;
  pain_points: PainPoint[];
  facts: MeetingFact[];
  gaps: InformationGap[];
  suggestions: QuestionSuggestion[];
  last_event_seq: number;
  ambiguous_routing_count: number;
  semantic_hints: SemanticProblemHint[];
  identity_review_candidates: ProblemIdentityReviewCandidate[];
  governance_candidates: GovernanceCandidate[];
  governance_alerts: GovernanceAlert[];
}

export interface ConnectorDefinition {
  connector_id: string;
  kind: ConnectorKind;
  display_name: string;
  source_authority: number;
  principal_id: string;
  configuration: ConnectorConfiguration;
}

export type DataQualitySeverity = 'low' | 'medium' | 'high' | 'critical';

export interface DataQualityRun {
  run_id: string;
  connector_id: string;
  status: 'running' | 'completed' | 'degraded';
  inspected_records: number;
  finding_count: number;
  detail_code?: string | null;
  started_at: string;
  completed_at?: string | null;
}

export interface DataQualityRulePack {
  pack_id: string;
  version: string;
  issuer: string;
  manifest_sha256: string;
  signature: string;
  trusted: true;
  installed_at: string;
}

export interface DataQualityFinding {
  finding_id: string;
  fingerprint: string;
  run_id: string;
  connector_id: string;
  rule_id: string;
  severity: DataQualitySeverity;
  title: string;
  description: string;
  source_reference: string;
  status: 'open' | 'confirmed' | 'dismissed';
  revision: number;
  detected_at: string;
  retrieved_at: string;
}

export type DeliveryStatus =
  | 'drafted'
  | 'pending_confirmation'
  | 'approved'
  | 'executing'
  | 'succeeded'
  | 'failed'
  | 'delivery_unknown'
  | 'cancelled';

export interface ExternalActionDraft {
  action_id: string;
  kind: 'teams_notification' | 'slack_notification' | 'netsuite_review_record';
  connector_id: string;
  destination_ref: string;
  destination_label: string;
  finding_id: string;
  finding_revision: number;
  severity: DataQualitySeverity;
  source_reference: string;
  rendered_preview: string;
  contains_sensitive_content: boolean;
  status: DeliveryStatus;
  approval_origin?: 'user' | 'trusted_rule' | null;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface NotificationPolicy {
  policy_id: string;
  connector_id: string;
  destination_ref: string;
  trusted_rule_ids: string[];
  minimum_severity: 'high' | 'critical';
  enabled: boolean;
  cooldown_minutes: number;
  revision: number;
}

export interface DocumentRoutingProposal {
  proposal_id: string;
  artifact_id: string;
  source_reference: string;
  partner_reference: string;
  partner_label: string;
  destination_ref: string;
  destination_label: string;
  action: 'copy' | 'move';
  confidence: number;
  status: 'proposed' | 'completed' | 'failed' | 'cancelled';
  revision: number;
  created_at: string;
}

export interface DocumentRoutingDestination {
  destination_ref: string;
  label: string;
  partner_references: string[];
  aliases: string[];
}

export interface ConnectorHealth {
  connector_id: string;
  kind: ConnectorKind;
  display_name: string;
  phase: ConnectorPhase;
  auth_enabled: boolean;
  last_success_at?: string | null;
  detail_code?: string | null;
  scope_summary?: string | null;
  retry_at?: string | null;
  indexed_documents: number;
}

export interface ContextCitation {
  citation_id: string;
  connector_id: string;
  source_kind: ConnectorKind;
  source_label: string;
  source_reference: string;
  entity_type: string;
  excerpt: string;
  uri?: string | null;
  retrieved_at: string;
  updated_at?: string | null;
  stale: boolean;
  relation: ContextRelation;
  rank: number;
}

export interface ContextSearchResult {
  citations: ContextCitation[];
  unavailable_connector_ids: string[];
}

export interface ConnectedQuestionContextRequest {
  session_id: string;
  suggestion_id: string;
  state_version: number;
  connector_ids: string[];
  limit: number;
}

export interface ConnectedQuestionBrief extends ContextSearchResult {
  status: "current" | "stale" | "unavailable";
  state_version: number;
  suggestion_id: string;
}

export interface ConnectedProblemBrief extends ContextSearchResult {
  status: "current" | "stale" | "unavailable" | "timeout" | "problem_not_found";
  state_version: number;
  pain_id: string;
}

export interface ConnectedProblemContextRequest {
  session_id: string;
  pain_id: string;
  state_version: number;
  connector_ids: string[];
  limit: number;
}

export interface IssueFact {
  slot: string;
  value: string;
  kind: FactKind;
  extraction_origin: ExtractionOrigin;
  evidence_event_ids: string[];
}

export interface IssueHistoryWarning {
  slot: string;
  value: string;
  status: "superseded" | "contradicted";
  evidence_event_ids: string[];
}

export interface IssueQuestion {
  slot: string;
  question: string;
  priority: number;
}

export type IssueReadiness = 'needs_clarification' | 'ready_for_review';

export interface IssueExportOptions {
  jira_work_type: string;
  azure_work_item_type: string;
  include_connected_excerpts: boolean;
}

export interface StructuredIssueDraft {
  draft_id: string;
  session_id: string;
  pain_id: string;
  state_version: number;
  template_id: string;
  language: Lang;
  title: string;
  problem: string[];
  impact: string[];
  affected_systems: string[];
  scope: string[];
  owner?: string | null;
  workaround: string[];
  acceptance_criteria: string[];
  facts: IssueFact[];
  history_warnings: IssueHistoryWarning[];
  unresolved_questions: IssueQuestion[];
  context_citations: ContextCitation[];
  context_status: "not_requested" | "current" | "unavailable" | "timeout";
  readiness: IssueReadiness;
  missing_required_fields: string[];
}

export interface StructuredIssueDraftBatch {
  state_version: number;
  drafts: StructuredIssueDraft[];
}

export type DocumentKind =
  | 'requirement'
  | 'architecture'
  | 'adr'
  | 'interface'
  | 'data_mapping'
  | 'nfr_security'
  | 'test'
  | 'cutover'
  | 'operations'
  | 'general';

export interface ArtifactLocator {
  heading?: string | null;
  page?: number | null;
  sheet?: string | null;
  cell_range?: string | null;
  paragraph?: number | null;
  start_line?: number | null;
  end_line?: number | null;
}

export interface RetrievalScore {
  lexical_rank?: number | null;
  semantic_rank?: number | null;
  entity_matches: number;
  business_key_matches: number;
  title_match: boolean;
  freshness_boost: number;
  source_authority_boost: number;
  reciprocal_rank_score: number;
}

export interface EvidenceCitation {
  citation_id: string;
  source_kind: ConnectorKind;
  source_label: string;
  source_reference: string;
  document_kind: DocumentKind;
  excerpt: string;
  uri?: string | null;
  locator: ArtifactLocator;
  retrieved_at: string;
  updated_at?: string | null;
  relation: 'supporting' | 'conflicting' | 'reference';
  rank: number;
  score: RetrievalScore;
}

export interface EvidenceBundle {
  status: 'current' | 'unavailable' | 'timeout';
  citations: EvidenceCitation[];
  unavailable_connector_ids: string[];
  lexical_only: boolean;
  elapsed_ms: number;
}

export type AssuranceSeverity = 'info' | 'warning' | 'high' | 'critical';
export type AssuranceFindingStatus =
  | 'open'
  | 'confirmed'
  | 'dismissed'
  | 'resolved'
  | 'superseded';

export interface AssuranceFinding {
  finding_id: string;
  fingerprint: string;
  run_id: string;
  rule_id: string;
  artifact_id: string;
  revision_id: string;
  source_reference: string;
  document_kind: DocumentKind;
  title: string;
  description: string;
  severity: AssuranceSeverity;
  locator: ArtifactLocator;
  status: AssuranceFindingStatus;
  revision: number;
  suggested_governance_kind?: GovernanceKind | null;
  created_at: string;
  updated_at: string;
}

export interface AssuranceRun {
  run_id: string;
  connector_ids: string[];
  rule_pack_ids: string[];
  status: 'queued' | 'running' | 'completed' | 'degraded' | 'failed';
  started_at: string;
  completed_at?: string | null;
  artifact_count: number;
  changed_revision_count: number;
  finding_count: number;
  detail_code?: string | null;
}

export type ConsistencyMismatchKind =
  | 'missing_entity'
  | 'missing_field'
  | 'undocumented_implementation'
  | 'value_mismatch'
  | 'type_mismatch'
  | 'requiredness_mismatch'
  | 'key_mismatch'
  | 'code_list_mismatch'
  | 'api_version_drift'
  | 'schema_version_drift'
  | 'environment_drift'
  | 'configuration_drift'
  | 'missing_test'
  | 'missing_deployment'
  | 'deployment_lag'
  | 'ownership_conflict'
  | 'control_conflict'
  | 'ambiguous_match';

export type ConsistencyAttribution =
  | 'neutral'
  | 'documentation_drift'
  | 'implementation_drift'
  | 'configuration_drift'
  | 'release_drift'
  | 'approved_exception'
  | 'ambiguous'
  | 'access_incomplete';

export type ConsistencyFindingStatus =
  | 'open'
  | 'confirmed'
  | 'dismissed'
  | 'resolved'
  | 'approved_exception'
  | 'superseded';

export interface ConsistencyCitation {
  connector_id: string;
  source_kind: ConnectorKind;
  source_label: string;
  source_reference: string;
  source_revision: string;
  locator: ArtifactLocator;
  environment?: string | null;
  retrieved_at: string;
  freshness: 'current' | 'stale' | 'unknown';
}

export interface DocumentClaim {
  claim_id: string;
  fingerprint: string;
  canonical_subject: string;
  canonical_property: string;
  expected_value?: string | null;
  expected_value_sha256: string;
  value_type?: string | null;
  environment?: string | null;
  version?: string | null;
  language: 'ja' | 'en' | 'ko';
  document_kind: DocumentKind;
  origin: 'deterministic' | 'semantic_suggested' | 'semantic_confirmed';
  status: 'proposed' | 'confirmed' | 'dismissed';
  confidence: number;
  impact_flags: Array<'production' | 'security' | 'regulatory' | 'customer' | 'financial_close'>;
  citation: ConsistencyCitation;
  revision: number;
  created_at: string;
}

export interface ObservedSystemFact {
  fact_id: string;
  canonical_subject: string;
  canonical_property: string;
  observed_value?: string | null;
  observed_value_sha256: string;
  value_type?: string | null;
  environment?: string | null;
  version?: string | null;
  persistence: 'metadata_safe' | 'encrypted_lease' | 'live_only';
  scope_complete: boolean;
  citation: ConsistencyCitation;
}

export interface ConsistencyComparison {
  expected_value?: string | null;
  expected_value_sha256: string;
  observed_value?: string | null;
  observed_value_sha256: string;
  comparison_rule: string;
  authority_policy_id?: string | null;
  attribution: ConsistencyAttribution;
  explanation: string;
  likely_impact: string;
  recommended_verification: string;
}

export interface ConsistencyFinding {
  finding_id: string;
  fingerprint: string;
  run_id: string;
  mismatch_kind: ConsistencyMismatchKind;
  severity: AssuranceSeverity;
  status: ConsistencyFindingStatus;
  subject: string;
  property_name: string;
  document_claim: DocumentClaim;
  observed_fact: ObservedSystemFact;
  comparison: ConsistencyComparison;
  affected_solution_node_ids: string[];
  owner_role?: string | null;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface ConsistencyRun {
  run_id: string;
  document_connector_ids: string[];
  observed_connector_ids: string[];
  status: 'running' | 'completed' | 'degraded' | 'failed';
  language: 'ja' | 'en' | 'ko';
  started_at: string;
  completed_at?: string | null;
  claim_count: number;
  observation_count: number;
  finding_count: number;
  ambiguous_count: number;
  source_fingerprint?: string | null;
  reused_run_id?: string | null;
  unavailable_connector_ids: string[];
  detail_code?: string | null;
}

export interface AuthorityPolicy {
  policy_id: string;
  label: string;
  subject_pattern: string;
  property_pattern: string;
  environment?: string | null;
  authority: 'neutral' | 'documentation' | 'observed_system';
  priority: number;
  enabled: boolean;
  revision: number;
  reviewed_at: string;
}

export interface ConsistencyDashboard {
  generated_at: string;
  open_findings: number;
  confirmed_findings: number;
  approved_exceptions: number;
  ambiguous_findings: number;
  by_mismatch_kind: Record<string, number>;
  by_severity: Record<string, number>;
  by_attribution: Record<string, number>;
  proposed_semantic_claims: DocumentClaim[];
  recent_findings: ConsistencyFinding[];
}

export interface AssuranceSchedule {
  schedule_id: string;
  connector_ids: string[];
  rule_pack_ids: string[];
  cadence: 'daily' | 'weekdays' | 'weekly';
  local_time: string;
  enabled: boolean;
  run_if_missed: boolean;
}

export interface TrustedRuleSigningKey {
  key_id: string;
  label: string;
  public_key_base64: string;
  fingerprint_sha256: string;
  trusted_at: string;
}

export interface AssuranceRulePack {
  pack_id: string;
  version: string;
  issuer: string;
  trusted: boolean;
  installed_at: string;
}

export interface TraceabilityRow {
  requirement_node_id: string;
  design_node_ids: string[];
  test_node_ids: string[];
  finding_ids: string[];
}

export interface TraceabilityMatrix {
  rows: TraceabilityRow[];
  missing_design_count: number;
  missing_test_count: number;
}

export interface SolutionThreadEdgeProposal {
  edge_id: string;
  source_label: string;
  target_label: string;
  source_reference: string;
  relation:
    | 'documents'
    | 'implements'
    | 'implemented_by'
    | 'deployed_as'
    | 'depends_on'
    | 'validated_by'
    | 'conflicts_with'
    | 'supersedes'
    | 'affects'
    | 'owned_by'
    | 'references';
  revision: number;
}

export interface SolutionLeadDashboard {
  generated_at: string;
  new_findings: AssuranceFinding[];
  governance_alerts: GovernanceAlert[];
  governance_records: GovernanceRecord[];
  missing_traceability: TraceabilityMatrix;
  erp_schema_drift_count: number;
  architecture_conflict_count: number;
  meetings_requiring_review: number;
  assurance_status: 'ready' | 'degraded' | 'unavailable';
}

export type IssueDraftReviewStatus =
  | 'unreviewed'
  | 'in_review'
  | 'reviewed'
  | 'needs_rebase'
  | 'exported';

export interface IssueDraftRebaseConflict {
  generated_value: string | string[] | null;
  reviewed_value: string | string[] | null;
}

export interface IssueDraftReviewOverlay {
  meeting_id: string;
  pain_id: string;
  base_state_version: number;
  status: IssueDraftReviewStatus;
  field_edits: Record<string, string | string[] | null>;
  accepted_semantic_suggestions: string[];
  base_field_hashes: Record<string, string>;
  rebase_conflicts: Record<string, IssueDraftRebaseConflict>;
  created_at: string;
  updated_at: string;
}

export type ImprovementProposalKind =
  | 'retrieval_weights'
  | 'document_classification'
  | 'assurance_rule'
  | 'glossary_alias'
  | 'routing_mapping'
  | 'netsuite_mapping';

export type ImprovementProposalStatus =
  | 'pending_evaluation'
  | 'evaluated'
  | 'approved_shadow'
  | 'active'
  | 'rejected'
  | 'blocked'
  | 'retired';

export interface ImprovementMetrics {
  sample_count: number;
  train_count: number;
  holdout_count: number;
  precision: number;
  recall: number;
  baseline_ndcg_at_5: number;
  candidate_ndcg_at_5: number;
  ndcg_improvement: number;
  maximum_cohort_regression: number;
  unauthorized_results: number;
}

export interface ImprovementCohortMetric {
  cohort: string;
  sample_count: number;
  baseline_score: number;
  candidate_score: number;
  regression: number;
}

export interface ImprovementEvaluation {
  evaluation_id: string;
  proposal_id: string;
  corpus_hash: string;
  train_snapshot_hash: string;
  holdout_snapshot_hash: string;
  active_profile_hash: string;
  independent_corpus_hash: string;
  metrics: ImprovementMetrics;
  cohort_metrics: ImprovementCohortMetric[];
  independent_case_count: number;
  independent_pass_rate: number;
  blocking_reasons: string[];
  eligible: boolean;
  detail_code: string;
  evaluated_at: string;
}

export interface ImprovementProposal {
  proposal_id: string;
  fingerprint: string;
  kind: ImprovementProposalKind;
  title: string;
  rationale: string;
  changes: Record<string, unknown>;
  signal_count: number;
  signal_snapshot_hash: string;
  train_snapshot_hash: string;
  holdout_snapshot_hash: string;
  independent_corpus_hash: string;
  base_profile_revision: number;
  status: ImprovementProposalStatus;
  revision: number;
  evaluation_id?: string | null;
  rollback_profile_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ImprovementProfile {
  profile_id: string;
  proposal_id: string;
  kind: ImprovementProposalKind;
  revision: number;
  status: 'shadow' | 'active' | 'previous' | 'needs_revalidation' | 'blocked_shadow' | 'retired';
  configuration: Record<string, unknown>;
  shadow_cycles_completed: number;
  activated_at?: string | null;
  created_at: string;
}

export interface ImprovementShadowRun {
  run_id: string;
  proposal_id: string;
  profile_id: string;
  cycle: number;
  corpus_hash: string;
  comparison: {
    applicable_cases: number;
    active_score: number;
    candidate_score: number;
    latency_regression_ms: number;
    privacy_violations: number;
    authorization_violations: number;
    invariant_violations: number;
    passed: boolean;
  };
  status: 'passed' | 'blocked';
  completed_at: string;
}

export interface ImprovementSettings {
  paused: boolean;
  excerpt_retention_days: 30 | 90 | 365 | -1;
  revision: number;
}

export interface ImprovementPackExport {
  filename: string;
  sha256: string;
  content_base64: string;
}

export interface ManagedDocumentClassification {
  classification_id: string;
  source_reference: string;
  document_kind: DocumentKind;
  labels: Record<string, string[]>;
  confidence: number;
  reason_codes: string[];
  lifecycle: 'unclassified' | 'proposed' | 'reviewed' | 'approved' | 'superseded' | 'archived';
  revision: number;
  reviewed_at?: string | null;
}

export interface DuplicateDocumentGroup {
  group_id: string;
  source_references: string[];
  relation: 'exact_duplicate' | 'possible_revision';
  confidence: number;
  reviewed: boolean;
}

export interface ManagedDocumentDisposition {
  disposition_id: string;
  source_reference: string;
  safe_filename: string;
  collection: string;
  tags: string[];
  destination_ref?: string | null;
  routing_action: 'copy' | 'move';
  status: 'proposed' | 'reviewed' | 'executing' | 'completed' | 'failed' | 'cancelled';
  revision: number;
}

export interface DocumentScanStatus {
  phase: 'idle' | 'scanning' | 'ready' | 'degraded' | 'unavailable';
  scanned_sources: number;
  indexed_revisions: number;
  failed_sources: number;
  extraction_methods: Array<'native' | 'ocr'>;
  ocr_status: 'disabled' | 'ready' | 'degraded' | 'unavailable';
  detail_code?: string | null;
  last_success_at?: string | null;
}

export interface DocumentOperationPlan {
  operation_id: string;
  disposition_id: string;
  source_reference: string;
  destination_ref: string;
  action: 'copy' | 'move';
  source_hash: string;
  status: 'ready' | 'completed' | 'failed' | 'rolled_back';
  revision: number;
  completed_at?: string | null;
}

export interface DocumentInbox {
  classifications: ManagedDocumentClassification[];
  duplicate_groups: DuplicateDocumentGroup[];
  dispositions: ManagedDocumentDisposition[];
}

export type ERPProcess =
  | 'order_to_cash'
  | 'procure_to_pay'
  | 'inventory'
  | 'project_to_cash'
  | 'financial_close';

export interface ERPControlTemplate {
  template_id: string;
  process: ERPProcess;
  title: string;
  required_record_types: string[];
  control_codes: string[];
}

export interface ERPGovernanceRecommendation {
  recommendation_id: string;
  kind: 'risk' | 'action' | 'issue_draft' | 'mapping_change';
  title: string;
  source_reference: string;
  reason: string;
  requires_review: true;
}

export interface ERPGovernanceRun {
  run_id: string;
  connector_id: string;
  status: 'completed' | 'degraded';
  active_templates: string[];
  inactive_templates: string[];
  finding_count: number;
  recommendations: ERPGovernanceRecommendation[];
  detail_code?: string | null;
  completed_at: string;
}

export interface ERPGovernanceDashboard {
  runs: ERPGovernanceRun[];
  findings_by_severity: Record<string, number>;
  findings_by_status: Record<string, number>;
  active_processes: ERPProcess[];
  ownerless_findings: number;
  generated_at: string;
}

export interface NetSuiteMappingValidation {
  connector_id: string;
  status: 'valid' | 'invalid' | 'unavailable';
  metadata_hash?: string | null;
  active_templates: string[];
  issues: Array<{
    code: string;
    record_type: string;
    field?: string | null;
    severity: 'info' | 'warning' | 'error';
  }>;
  persisted_remote_records: 0;
  validated_at: string;
}

export interface MicrosoftDeviceCodeAuthorization {
  verification_uri: string;
  user_code: string;
  expires_at: string;
  interval_seconds: number;
}

export type MicrosoftAuthorizationStatus =
  | "pending"
  | "complete"
  | "declined"
  | "expired"
  | "not_started";

export function emptyMeetingState(meetingId: string): MeetingState {
  return {
    meeting_id: meetingId,
    version: 0,
    transcript: [],
    pain_points: [],
    facts: [],
    gaps: [],
    suggestions: [],
    last_event_seq: -1,
    ambiguous_routing_count: 0,
    identity_decisions: [],
    confirmed_semantic_hint_ids: [],
  };
}

export function emptyLiveIntelligenceSnapshot(meetingId: string): LiveIntelligenceSnapshot {
  return {
    meeting_id: meetingId,
    version: 0,
    pain_points: [],
    facts: [],
    gaps: [],
    suggestions: [],
    last_event_seq: -1,
    ambiguous_routing_count: 0,
    semantic_hints: [],
    identity_review_candidates: [],
    governance_candidates: [],
    governance_alerts: [],
  };
}
