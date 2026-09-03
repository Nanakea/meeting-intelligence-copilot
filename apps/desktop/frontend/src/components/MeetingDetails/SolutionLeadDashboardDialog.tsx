'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  BookOpenCheck,
  CheckCircle2,
  Database,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { DocumentOperationsPanel } from './DocumentOperationsPanel';
import { ImprovementWorkflowCard } from './ImprovementWorkflowCard';
import { NetSuiteGovernancePanel } from './NetSuiteGovernancePanel';
import { SystemDocumentationConsistency } from './SystemDocumentationConsistency';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import {
  assuranceCapableConnectorIds,
  checkErpMetadata,
  exportExternalAction,
  getAssuranceFindings,
  getAssuranceSchedules,
  getDataQualityFindings,
  getDocumentRoutingProposals,
  getDocumentRoutingDestinations,
  getDocumentInbox,
  getDocumentScanStatus,
  getErpGovernanceDashboard,
  getExternalActions,
  getImprovementProfiles,
  getImprovementProposals,
  getImprovementSettings,
  getSuggestedTraceabilityLinks,
  getSolutionLeadDashboard,
  installAssuranceRulePack,
  installDataQualityRulePack,
  importImprovementPack,
  queryEvidence,
  reviewAssuranceFinding,
  reviewDataQualityFinding,
  reviewImprovementProposal,
  reviewManagedDocumentClassification,
  reviewManagedDocumentDisposition,
  runImprovementShadowCycle,
  reviewDocumentRoutingProposal,
  reviewTraceabilityLink,
  runDocumentAssurance,
  runErpGovernance,
  runNetSuiteDataQuality,
  scanManagedDocuments,
  saveAssuranceSchedule,
  saveDocumentRoutingDestination,
  saveImprovementSettings,
  trustAssuranceSigningKey,
  evaluateImprovementProposal,
  exportImprovementPack,
  rollbackImprovementProfile,
  executeManagedDocumentDisposition,
  validateNetSuiteMappings,
} from '@/services/assuranceService';
import { listContextConnectors } from '@/services/contextConnectorService';
import type {
  AssuranceFinding,
  AssuranceSchedule,
  ConnectorHealth,
  DataQualityFinding,
  DocumentRoutingProposal,
  DocumentRoutingDestination,
  DocumentScanStatus,
  DocumentInbox,
  ERPGovernanceDashboard,
  EvidenceBundle,
  ExternalActionDraft,
  ImprovementProfile,
  ImprovementProposal,
  ImprovementSettings,
  NetSuiteMappingValidation,
  SolutionLeadDashboard,
  SolutionThreadEdgeProposal,
} from '@/services/intelligenceContracts';

const EMPTY_DASHBOARD: SolutionLeadDashboard = {
  generated_at: '',
  new_findings: [],
  governance_alerts: [],
  governance_records: [],
  missing_traceability: { rows: [], missing_design_count: 0, missing_test_count: 0 },
  erp_schema_drift_count: 0,
  architecture_conflict_count: 0,
  meetings_requiring_review: 0,
  assurance_status: 'ready',
};

const EMPTY_DOCUMENT_INBOX: DocumentInbox = {
  classifications: [],
  duplicate_groups: [],
  dispositions: [],
};

const EMPTY_ERP_GOVERNANCE: ERPGovernanceDashboard = {
  runs: [],
  findings_by_severity: {},
  findings_by_status: {},
  active_processes: [],
  ownerless_findings: 0,
  generated_at: '',
};

const EMPTY_SCAN_STATUS: DocumentScanStatus = {
  phase: 'idle',
  scanned_sources: 0,
  indexed_revisions: 0,
  failed_sources: 0,
  extraction_methods: [],
  ocr_status: 'disabled',
};

const SEVERITY_STYLE = {
  info: 'border-sky-200 bg-sky-50 text-sky-900',
  warning: 'border-amber-200 bg-amber-50 text-amber-950',
  high: 'border-orange-200 bg-orange-50 text-orange-950',
  critical: 'border-rose-300 bg-rose-50 text-rose-950',
} as const;

function locatorLabel(finding: AssuranceFinding): string | null {
  const { heading, page, sheet, cell_range: cellRange } = finding.locator;
  if (page) return `Page ${page}`;
  if (sheet) return `${sheet}${cellRange ? ` · ${cellRange}` : ''}`;
  return heading || null;
}

export function SolutionLeadDashboardDialog() {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [dashboard, setDashboard] = useState(EMPTY_DASHBOARD);
  const [findings, setFindings] = useState<AssuranceFinding[]>([]);
  const [connectors, setConnectors] = useState<ConnectorHealth[]>([]);
  const [schedules, setSchedules] = useState<AssuranceSchedule[]>([]);
  const [traceabilityLinks, setTraceabilityLinks] = useState<SolutionThreadEdgeProposal[]>([]);
  const [dataFindings, setDataFindings] = useState<DataQualityFinding[]>([]);
  const [externalActions, setExternalActions] = useState<ExternalActionDraft[]>([]);
  const [routingProposals, setRoutingProposals] = useState<DocumentRoutingProposal[]>([]);
  const [improvementProposals, setImprovementProposals] = useState<ImprovementProposal[]>([]);
  const [improvementProfiles, setImprovementProfiles] = useState<ImprovementProfile[]>([]);
  const [improvementSettings, setImprovementSettings] = useState<ImprovementSettings>({
    paused: false,
    excerpt_retention_days: 90,
    revision: 0,
  });
  const [documentInbox, setDocumentInbox] = useState(EMPTY_DOCUMENT_INBOX);
  const [erpGovernance, setErpGovernance] = useState(EMPTY_ERP_GOVERNANCE);
  const [documentScan, setDocumentScan] = useState(EMPTY_SCAN_STATUS);
  const [routingDestinations, setRoutingDestinations] = useState<DocumentRoutingDestination[]>([]);
  const [mappingValidation, setMappingValidation] = useState<NetSuiteMappingValidation | null>(null);
  const [query, setQuery] = useState('');
  const [evidence, setEvidence] = useState<EvidenceBundle | null>(null);
  const [scheduleTime, setScheduleTime] = useState('08:00');
  const [routeRoot, setRouteRoot] = useState('');
  const [routeLabel, setRouteLabel] = useState('');
  const [routePartnerReference, setRoutePartnerReference] = useState('');
  const [ruleKeyId, setRuleKeyId] = useState('company-architecture');
  const [ruleKeyLabel, setRuleKeyLabel] = useState('Company Architecture Office');
  const [rulePublicKey, setRulePublicKey] = useState('');
  const [activeView, setActiveView] = useState<
    'overview' | 'findings' | 'consistency' | 'evidence' | 'improvements' | 'automation'
  >('overview');
  const isJapanese = typeof navigator !== 'undefined' && navigator.language.toLowerCase().startsWith('ja');

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [nextDashboard, nextFindings, nextConnectors, nextSchedules, nextLinks,
        nextDataFindings, nextActions, nextRoutes, nextProposals,
        nextProfiles, nextImprovementSettings, nextDocumentInbox, nextErpGovernance] =
        await Promise.all([
        getSolutionLeadDashboard(),
        getAssuranceFindings(),
        listContextConnectors(),
        getAssuranceSchedules(),
        getSuggestedTraceabilityLinks(),
        getDataQualityFindings().catch(() => []),
        getExternalActions().catch(() => []),
        getDocumentRoutingProposals().catch(() => []),
        getImprovementProposals().catch(() => []),
        getImprovementProfiles().catch(() => []),
        getImprovementSettings().catch(() => ({
          paused: false, excerpt_retention_days: 90 as const, revision: 0,
        })),
        getDocumentInbox().catch(() => EMPTY_DOCUMENT_INBOX),
        getErpGovernanceDashboard().catch(() => EMPTY_ERP_GOVERNANCE),
      ]);
      setDashboard(nextDashboard);
      setFindings(nextFindings.filter((finding) => finding.status === 'open'));
      setConnectors(nextConnectors);
      setSchedules(nextSchedules);
      setTraceabilityLinks(nextLinks);
      setDataFindings(nextDataFindings.filter((finding) => finding.status !== 'dismissed'));
      setExternalActions(nextActions);
      setRoutingProposals(nextRoutes.filter((proposal) => proposal.status === 'proposed'));
      setImprovementProposals(nextProposals);
      setImprovementProfiles(nextProfiles);
      setImprovementSettings(nextImprovementSettings);
      setDocumentInbox(nextDocumentInbox);
      setErpGovernance(nextErpGovernance);
      const [nextRoutingDestinations, nextScanStatus] = await Promise.all([
        getDocumentRoutingDestinations().catch(() => []),
        getDocumentScanStatus().catch(() => EMPTY_SCAN_STATUS),
      ]);
      setRoutingDestinations(nextRoutingDestinations);
      setDocumentScan(nextScanStatus);
      if (nextSchedules[0]) setScheduleTime(nextSchedules[0].local_time);
    } catch {
      toast.error(isJapanese ? 'ソリューション状況を読み込めませんでした' : 'Could not load the solution workspace');
    } finally {
      setLoading(false);
    }
  }, [isJapanese]);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  const runChecks = async () => {
    const connectorIds = assuranceCapableConnectorIds(connectors);
    if (connectorIds.length === 0) {
      toast.error(isJapanese ? '文書ソースを設定してください' : 'Configure a document source first');
      return;
    }
    setLoading(true);
    try {
      const run = await runDocumentAssurance(connectorIds);
      if (run.status === 'degraded') {
        toast.warning(isJapanese ? '文書チェックは一部のみ完了しました' : 'Document checks completed with limited coverage');
      }
      await refresh();
      setActiveView('findings');
    } catch {
      toast.error(isJapanese ? '文書チェックを実行できませんでした' : 'Document checks are currently unavailable');
      setLoading(false);
    }
  };

  const review = async (finding: AssuranceFinding, action: 'confirm' | 'dismiss') => {
    try {
      await reviewAssuranceFinding(finding, action);
      await refresh();
    } catch {
      toast.error(isJapanese ? '項目が更新されました。再読み込みしてください' : 'This finding changed. Refresh and try again.');
    }
  };

  const reviewLink = async (
    proposal: SolutionThreadEdgeProposal,
    action: 'confirm' | 'dismiss',
  ) => {
    try {
      await reviewTraceabilityLink(proposal, action);
      await refresh();
    } catch {
      toast.error(isJapanese ? 'リンクが更新されました。再読み込みしてください' : 'This link changed. Refresh and try again.');
    }
  };

  const searchEvidence = async () => {
    if (!query.trim()) return;
    setLoading(true);
    try {
      setEvidence(await queryEvidence(query.trim(), connectors.map((value) => value.connector_id)));
    } catch {
      setEvidence({ status: 'unavailable', citations: [], unavailable_connector_ids: [], lexical_only: true, elapsed_ms: 0 });
    } finally {
      setLoading(false);
    }
  };

  const saveSchedule = async () => {
    const connectorIds = assuranceCapableConnectorIds(connectors);
    if (connectorIds.length === 0) return;
    try {
      const saved = await saveAssuranceSchedule({
        schedule_id: 'daily-solution-assurance',
        connector_ids: connectorIds,
        rule_pack_ids: [],
        cadence: 'weekdays',
        local_time: scheduleTime,
        enabled: true,
        run_if_missed: true,
      });
      setSchedules([saved]);
      toast.success(isJapanese ? 'チェック予定を保存しました' : 'Assurance schedule saved locally');
    } catch {
      toast.error(isJapanese ? '予定を保存できませんでした' : 'Could not save the assurance schedule');
    }
  };

  const validateErp = async (connector: ConnectorHealth) => {
    try {
      const count = await checkErpMetadata(connector.connector_id);
      toast.success(count > 0
        ? `${count} ${isJapanese ? '件のメタデータ差異を検出しました' : 'metadata differences found'}`
        : isJapanese ? 'ERPメタデータは一致しています' : 'ERP metadata matches selected documents');
      await refresh();
    } catch {
      toast.error(isJapanese ? 'ERPメタデータを確認できませんでした' : 'ERP metadata is currently unavailable');
    }
  };

  const inspectNetSuite = async (connector: ConnectorHealth) => {
    try {
      const run = await runNetSuiteDataQuality(connector.connector_id);
      toast.success(`${run.finding_count} ${isJapanese ? '件のデータ品質指摘' : 'data-quality findings'}`);
      await refresh();
    } catch {
      toast.error(isJapanese ? 'NetSuiteを確認できませんでした' : 'NetSuite is currently unavailable');
    }
  };

  const inspectErpGovernance = async (connector: ConnectorHealth) => {
    try {
      const run = await runErpGovernance(connector.connector_id);
      toast.success(run.status === 'completed'
        ? (isJapanese ? 'ERPガバナンス確認が完了しました' : 'ERP governance check completed')
        : (isJapanese ? 'ERP確認は一部のみ完了しました' : 'ERP check completed with limited coverage'));
      await refresh();
    } catch {
      toast.error(isJapanese ? 'ERPガバナンスを確認できませんでした' : 'ERP governance is unavailable');
    }
  };

  const evaluateProposal = async (proposal: ImprovementProposal) => {
    try {
      const evaluation = await evaluateImprovementProposal(proposal);
      toast[evaluation.eligible ? 'success' : 'warning'](
        evaluation.eligible
          ? (isJapanese ? '評価基準を満たしました' : 'Proposal passed deterministic evaluation')
          : (isJapanese ? '品質基準を満たしていません' : 'Proposal did not meet quality gates'),
      );
      await refresh();
    } catch {
      toast.error(isJapanese ? '改善案を評価できませんでした' : 'Could not evaluate the proposal');
    }
  };

  const reviewProposal = async (
    proposal: ImprovementProposal,
    action: 'approve' | 'reject',
  ) => {
    try {
      await reviewImprovementProposal(proposal, action);
      await refresh();
    } catch {
      toast.error(isJapanese ? '改善案が更新されました' : 'The proposal changed; refresh and retry');
    }
  };

  const runShadowCycle = async (proposal: ImprovementProposal) => {
    try {
      const run = await runImprovementShadowCycle(proposal);
      toast[run.status === 'passed' ? 'success' : 'warning'](
        run.status === 'passed'
          ? `${isJapanese ? 'シャドー評価' : 'Shadow cycle'} ${run.cycle}/3`
          : (isJapanese ? 'シャドー評価で回帰を検出しました' : 'Shadow comparison found a regression'),
      );
      await refresh();
    } catch {
      toast.error(isJapanese ? 'シャドー評価を実行できませんでした' : 'Shadow cycle is not ready');
    }
  };

  const scanDocuments = async () => {
    try {
      const status = await scanManagedDocuments(assuranceCapableConnectorIds(connectors));
      setDocumentScan(status);
      await refresh();
    } catch {
      toast.error(isJapanese ? '文書を確認できませんでした' : 'Document scan is unavailable');
    }
  };

  const validateMappings = async (connector: ConnectorHealth) => {
    try {
      const result = await validateNetSuiteMappings(connector.connector_id);
      setMappingValidation(result);
      toast[result.status === 'valid' ? 'success' : 'warning'](
        result.status === 'valid'
          ? (isJapanese ? 'NetSuiteマッピングは有効です' : 'NetSuite mappings are valid')
          : (isJapanese ? 'NetSuiteマッピングを確認してください' : 'NetSuite mappings require review'),
      );
    } catch {
      toast.error(isJapanese ? 'マッピングを検証できませんでした' : 'Mapping validation is unavailable');
    }
  };

  const executeDisposition = async (
    disposition: DocumentInbox['dispositions'][number],
    destinationRef: string,
  ) => {
    try {
      await executeManagedDocumentDisposition(disposition, destinationRef, 'copy');
      toast.success(isJapanese ? '確認済みコピーが完了しました' : 'Reviewed copy completed');
      await refresh();
    } catch {
      toast.error(isJapanese ? '文書をコピーできませんでした' : 'The reviewed copy could not be completed');
    }
  };

  const toggleLearning = async () => {
    try {
      const saved = await saveImprovementSettings({
        ...improvementSettings,
        paused: !improvementSettings.paused,
      });
      setImprovementSettings(saved);
    } catch {
      toast.error(isJapanese ? '学習設定が更新されました' : 'Learning settings changed; refresh and retry');
    }
  };

  const downloadImprovementPack = async () => {
    try {
      const pack = await exportImprovementPack();
      const binary = Uint8Array.from(atob(pack.content_base64), (value) => value.charCodeAt(0));
      const href = URL.createObjectURL(new Blob([binary], { type: 'application/zip' }));
      const anchor = document.createElement('a');
      anchor.href = href;
      anchor.download = pack.filename;
      anchor.click();
      URL.revokeObjectURL(href);
    } catch {
      toast.error(isJapanese ? '改善パックを出力できませんでした' : 'Could not export the improvement pack');
    }
  };

  const uploadImprovementPack = async (file: File) => {
    try {
      if (file.size > 4 * 1024 * 1024) throw new Error('pack_too_large');
      const contentBase64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error('pack_read_failed'));
        reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
        reader.readAsDataURL(file);
      });
      await importImprovementPack(contentBase64);
      await refresh();
    } catch {
      toast.error(isJapanese ? '改善パックを確認できませんでした' : 'The improvement pack is invalid');
    }
  };

  const enrollRuleKey = async () => {
    try {
      await trustAssuranceSigningKey({
        key_id: ruleKeyId.trim(),
        label: ruleKeyLabel.trim(),
        public_key_base64: rulePublicKey.trim(),
      });
      setRulePublicKey('');
      toast.success(isJapanese ? '署名公開鍵を登録しました' : 'Rule-pack signing key enrolled');
    } catch {
      toast.error(isJapanese ? '署名公開鍵を登録できませんでした' : 'Could not enroll the signing key');
    }
  };

  const installRulePack = async (file: File) => {
    try {
      if (file.size > 2 * 1024 * 1024) throw new Error('rule_pack_too_large');
      const packageBase64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error('rule_pack_read_failed'));
        reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
        reader.readAsDataURL(file);
      });
      const installed = await installAssuranceRulePack(packageBase64);
      toast.success(`${installed.pack_id} ${installed.version}`);
    } catch {
      toast.error(isJapanese ? '署名済みルールパックを確認できませんでした' : 'The signed rule pack could not be verified');
    }
  };

  const installQualityRulePack = async (file: File) => {
    try {
      if (file.size > 2 * 1024 * 1024) throw new Error('rule_pack_too_large');
      const packageBase64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error('rule_pack_read_failed'));
        reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
        reader.readAsDataURL(file);
      });
      const installed = await installDataQualityRulePack(packageBase64);
      toast.success(`${installed.pack_id} ${installed.version}`);
    } catch {
      toast.error(isJapanese ? 'データ品質ルールの署名を確認できませんでした' : 'The data-quality rule signature could not be verified');
    }
  };

  const saveRoute = async () => {
    try {
      await saveDocumentRoutingDestination({
        destination_ref: `route-${crypto.randomUUID().replaceAll('-', '')}`,
        label: routeLabel.trim(),
        partner_references: [routePartnerReference.trim()],
        aliases: [],
      }, routeRoot.trim());
      setRouteRoot('');
      setRouteLabel('');
      setRoutePartnerReference('');
      await getDocumentRoutingDestinations();
      toast.success(isJapanese ? '承認済み振り分け先を保存しました' : 'Approved routing destination saved');
    } catch {
      toast.error(isJapanese ? '振り分け先を保存できませんでした' : 'Could not save the routing destination');
    }
  };

  const labels = isJapanese
    ? { title: 'ソリューション・リード', overview: '概要', findings: '文書チェック', consistency: 'システム対文書', evidence: '根拠検索', improvements: '改善', automation: '自動化' }
    : { title: 'Solution Lead', overview: 'Overview', findings: 'Document checks', consistency: 'System vs Docs', evidence: 'Evidence search', improvements: 'Improvement', automation: 'Automation' };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="border-teal-300 bg-teal-50 text-teal-950 hover:bg-teal-100">
          <BookOpenCheck size={16} /> {labels.title}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[88vh] max-w-5xl overflow-y-auto border-teal-200 bg-[#f7faf7]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-xl text-slate-950">
            <ShieldCheck className="text-teal-700" /> {labels.title}
          </DialogTitle>
        </DialogHeader>
        <p className="text-sm text-slate-600">
          {isJapanese
            ? '会議、文書、ERPの根拠を確認します。確認前の指摘は会議の事実やガバナンス記録を変更しません。'
            : 'Review meeting, document, and ERP evidence. Unreviewed findings never change meeting truth or governance records.'}
        </p>

        <div className="flex flex-wrap gap-2" role="tablist" aria-label="Solution lead views">
          {(['overview', 'findings', 'consistency', 'evidence', 'improvements', 'automation'] as const).map((view) => (
            <button
              key={view}
              type="button"
              role="tab"
              aria-selected={activeView === view}
              onClick={() => setActiveView(view)}
              className={`rounded-full px-3 py-1.5 text-sm font-semibold ${activeView === view ? 'bg-slate-950 text-white' : 'border border-slate-300 bg-white text-slate-700'}`}
            >
              {labels[view]}
            </button>
          ))}
          <Button variant="ghost" size="sm" onClick={() => void refresh()} disabled={loading}>
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
            {isJapanese ? '更新' : 'Refresh'}
          </Button>
        </div>

        {activeView === 'overview' && (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {[
                [findings.length, isJapanese ? '未確認の指摘' : 'Open findings'],
                [dashboard.missing_traceability.missing_design_count, isJapanese ? '設計未紐付け' : 'Missing design links'],
                [dashboard.missing_traceability.missing_test_count, isJapanese ? 'テスト未紐付け' : 'Missing test links'],
                [dashboard.governance_alerts.length, isJapanese ? 'ガバナンス注意' : 'Governance alerts'],
              ].map(([value, label]) => (
                <div key={label} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                  <div className="text-3xl font-black tabular-nums text-slate-950">{value}</div>
                  <div className="mt-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</div>
                </div>
              ))}
            </div>
            <div className="rounded-2xl border border-teal-200 bg-teal-950 p-5 text-white">
              <h3 className="font-semibold">{isJapanese ? '次の確実な確認' : 'Next assurance action'}</h3>
              <p className="mt-1 text-sm text-teal-100">
                {isJapanese ? '変更されたローカル文書だけを再索引し、決定論的ルールで確認します。' : 'Reindex changed local documents and run deterministic checks only.'}
              </p>
              <Button className="mt-4 bg-white text-teal-950 hover:bg-teal-50" onClick={() => void runChecks()} disabled={loading}>
                <CheckCircle2 size={16} /> {isJapanese ? '文書チェックを実行' : 'Run document checks'}
              </Button>
            </div>
          </div>
        )}

        {activeView === 'findings' && (
          <section className="space-y-3" aria-live="polite">
            {traceabilityLinks.length > 0 && (
              <div className="space-y-2 rounded-2xl border border-sky-200 bg-sky-50 p-4">
                <h3 className="font-semibold text-sky-950">
                  {isJapanese ? '確認待ちのトレーサビリティ' : 'Traceability links to review'}
                </h3>
                {traceabilityLinks.map((link) => (
                  <div key={link.edge_id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-white p-3">
                    <div>
                      <p className="font-medium text-slate-950">
                        {link.source_label} → {link.target_label}
                      </p>
                      <p className="text-xs text-slate-500">
                        {link.source_reference} · {link.relation.replaceAll('_', ' ')}
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" onClick={() => void reviewLink(link, 'confirm')}>
                        {isJapanese ? 'リンクを確認' : 'Confirm link'}
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => void reviewLink(link, 'dismiss')}>
                        {isJapanese ? '関連なし' : 'Not related'}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {findings.length === 0 ? (
              <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5 text-sm text-emerald-900">
                {isJapanese ? '確認待ちの指摘はありません。' : 'No document findings are waiting for review.'}
              </div>
            ) : findings.map((finding) => (
              <article key={finding.finding_id} className={`rounded-2xl border p-4 ${SEVERITY_STYLE[finding.severity]}`}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="font-semibold">{finding.title}</h3>
                    <p className="mt-1 text-sm leading-relaxed">{finding.description}</p>
                    <p className="mt-2 text-xs font-medium opacity-75">
                      {finding.source_reference}{locatorLabel(finding) ? ` · ${locatorLabel(finding)}` : ''}
                    </p>
                  </div>
                  <AlertTriangle size={18} aria-label={finding.severity} />
                </div>
                <div className="mt-3 flex gap-2">
                  <Button size="sm" onClick={() => void review(finding, 'confirm')}>
                    {isJapanese ? '確認済みにする' : 'Confirm finding'}
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => void review(finding, 'dismiss')}>
                    {isJapanese ? '該当なし' : 'Not applicable'}
                  </Button>
                </div>
              </article>
            ))}
          </section>
        )}

        {activeView === 'evidence' && (
          <section className="space-y-4">
            <div className="flex gap-2">
              <label className="sr-only" htmlFor="solution-evidence-query">Evidence query</label>
              <input
                id="solution-evidence-query"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => { if (event.key === 'Enter') void searchEvidence(); }}
                className="min-w-0 flex-1 rounded-xl border border-slate-300 bg-white px-4 py-2"
                placeholder={isJapanese ? 'システム、要件、業務キーを検索' : 'Search systems, requirements, or business keys'}
              />
              <Button onClick={() => void searchEvidence()} disabled={loading || !query.trim()}>
                <Search size={16} /> {isJapanese ? '検索' : 'Search'}
              </Button>
            </div>
            {evidence?.status === 'timeout' && <p className="text-sm text-amber-800">{isJapanese ? '検索が時間切れになりました。会議機能には影響しません。' : 'Evidence search timed out. Meeting intelligence remains available.'}</p>}
            <div className="space-y-3">
              {evidence?.citations.map((citation) => (
                <article key={citation.citation_id} className="rounded-xl border border-slate-200 bg-white p-4">
                  <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-teal-700">
                    <Database size={14} /> {citation.source_label} · {citation.source_reference}
                  </div>
                  <p className="mt-2 text-sm leading-relaxed text-slate-800">{citation.excerpt}</p>
                  <p className="mt-2 text-xs text-slate-500">{isJapanese ? '会議で述べられた内容ではありません' : 'Not stated in this meeting'}</p>
                </article>
              ))}
            </div>
          </section>
        )}

        {activeView === 'consistency' && (
          <SystemDocumentationConsistency isJapanese={isJapanese} />
        )}

        {activeView === 'improvements' && (
          <section className="space-y-4">
            <div className="rounded-2xl border border-teal-200 bg-gradient-to-br from-teal-950 to-slate-900 p-5 text-white">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-teal-200">
                    <Sparkles size={15} /> {isJapanese ? '監督付き改善' : 'Supervised improvement'}
                  </div>
                  <h3 className="mt-2 text-xl font-semibold">
                    {improvementProposals.length} {isJapanese ? '件の改善案' : 'proposals'} · {' '}
                    {improvementProfiles.filter((profile) => profile.status === 'active').length} {' '}
                    {isJapanese ? '件有効' : 'active'}
                  </h3>
                  <p className="mt-2 max-w-2xl text-sm text-slate-200">
                    {isJapanese
                      ? '明示的な確認だけから学習し、評価、承認、シャドー実行後に有効化します。コード、権限、ERP書込方針は変更できません。'
                      : 'Learns only from explicit reviews. Changes require isolated holdout evaluation, approval, and three measured shadow cycles, and can never alter code, authorization, or ERP write policy.'}
                  </p>
                </div>
                <Button variant="outline" className="border-white/30 bg-white/10 text-white hover:bg-white/20" onClick={() => void toggleLearning()}>
                  {improvementSettings.paused
                    ? (isJapanese ? '学習を再開' : 'Resume learning')
                    : (isJapanese ? '学習を一時停止' : 'Pause learning')}
                </Button>
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
                <label className="flex items-center gap-2 rounded-lg bg-white/10 px-3 py-2">
                  {isJapanese ? '例の保持' : 'Example retention'}
                  <select
                    value={improvementSettings.excerpt_retention_days}
                    onChange={(event) => {
                      const days = Number(event.target.value) as 30 | 90 | 365 | -1;
                      void saveImprovementSettings({
                        ...improvementSettings,
                        excerpt_retention_days: days,
                      }).then(setImprovementSettings).catch(() => {
                        toast.error(isJapanese ? '保持設定を更新できませんでした' : 'Could not update retention');
                      });
                    }}
                    className="rounded bg-slate-950 px-2 py-1 text-white"
                  >
                    <option value={30}>30 days</option>
                    <option value={90}>90 days</option>
                    <option value={365}>365 days</option>
                    <option value={-1}>{isJapanese ? '無期限' : 'Forever'}</option>
                  </select>
                </label>
                <Button size="sm" variant="outline" className="border-white/30 bg-white/10 text-white hover:bg-white/20" onClick={() => void downloadImprovementPack()}>
                  {isJapanese ? '署名パックを出力' : 'Export signed pack'}
                </Button>
                <label className="cursor-pointer rounded-md border border-white/30 bg-white/10 px-3 py-1.5 text-sm font-semibold focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-white">
                  {isJapanese ? 'パックを確認して取込' : 'Review and import pack'}
                  <input type="file" accept=".zip,application/zip" className="sr-only" onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadImprovementPack(file); event.target.value = ''; }} />
                </label>
              </div>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <div className="rounded-2xl border border-slate-200 bg-white p-4">
                <h3 className="font-semibold text-slate-950">{isJapanese ? '評価待ちの改善案' : 'Improvement proposals'}</h3>
                <div className="mt-3 space-y-3">
                  {improvementProposals.map((proposal) => (
                    <ImprovementWorkflowCard
                      key={proposal.proposal_id}
                      proposal={proposal}
                      isJapanese={isJapanese}
                      onEvaluate={(value) => void evaluateProposal(value)}
                      onApprove={(value) => void reviewProposal(value, 'approve')}
                      onRunShadow={(value) => void runShadowCycle(value)}
                      onReject={(value) => void reviewProposal(value, 'reject')}
                    />
                  ))}
                  {improvementProposals.length === 0 && <p className="text-sm text-slate-500">{isJapanese ? '十分な確認データが集まると改善案が表示されます。' : 'Proposals appear only after enough reviewed evidence is collected.'}</p>}
                </div>
              </div>

              <div className="rounded-2xl border border-slate-200 bg-white p-4">
                <h3 className="font-semibold text-slate-950">{isJapanese ? 'プロファイル履歴' : 'Profile history and rollback'}</h3>
                <div className="mt-3 space-y-2">
                  {improvementProfiles.map((profile) => (
                    <article key={profile.profile_id} className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 p-3 text-sm">
                      <div>
                        <p className="font-medium text-slate-900">{profile.kind.replaceAll('_', ' ')}</p>
                        <p className="text-xs text-slate-500">Revision {profile.revision} · {profile.status} · {profile.shadow_cycles_completed} shadow cycles</p>
                      </div>
                      {profile.status === 'previous' && (
                        <Button size="sm" variant="outline" onClick={() => void rollbackImprovementProfile(profile).then(refresh)}>{isJapanese ? '戻す' : 'Rollback'}</Button>
                      )}
                    </article>
                  ))}
                  {improvementProfiles.length === 0 && <p className="text-sm text-slate-500">{isJapanese ? '有効化された改善はありません。' : 'No approved profiles yet.'}</p>}
                </div>
              </div>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <DocumentOperationsPanel
                inbox={documentInbox}
                scan={documentScan}
                destinations={routingDestinations}
                isJapanese={isJapanese}
                onScan={() => void scanDocuments()}
                onReviewClassification={(classification, action) => {
                  void reviewManagedDocumentClassification(classification, action).then(refresh);
                }}
                onReviewDisposition={(disposition, action) => {
                  void reviewManagedDocumentDisposition(disposition, action).then(refresh);
                }}
                onExecute={(disposition, destinationRef) => {
                  void executeDisposition(disposition, destinationRef);
                }}
              />
              <NetSuiteGovernancePanel
                connectors={connectors}
                dashboard={erpGovernance}
                validation={mappingValidation}
                isJapanese={isJapanese}
                onValidate={(connector) => void validateMappings(connector)}
                onRun={(connector) => void inspectErpGovernance(connector)}
              />
            </div>
          </section>
        )}

        {activeView === 'automation' && (
          <section className="space-y-4">
            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <h3 className="font-semibold">{isJapanese ? '平日の自動チェック' : 'Weekday assurance schedule'}</h3>
              <p className="mt-1 text-sm text-slate-600">{isJapanese ? '明示的に設定したローカル文書とMicrosoft文書ソースのみが対象です。' : 'Only explicitly configured local and Microsoft document sources are included.'}</p>
              <div className="mt-3 flex items-end gap-2">
                <label className="text-sm font-medium text-slate-700">
                  {isJapanese ? '実行時刻' : 'Local time'}
                  <input type="time" value={scheduleTime} onChange={(event) => setScheduleTime(event.target.value)} className="mt-1 block rounded-lg border border-slate-300 px-3 py-2" />
                </label>
                <Button onClick={() => void saveSchedule()}>{isJapanese ? '予定を保存' : 'Save schedule'}</Button>
              </div>
              {schedules[0] && <p className="mt-2 text-xs text-emerald-700">{isJapanese ? 'ローカル予定が有効です' : 'Local schedule is enabled'} · {schedules[0].local_time}</p>}
            </div>
            <div className="rounded-2xl border border-teal-200 bg-teal-50 p-4">
              <h3 className="font-semibold text-teal-950">{isJapanese ? 'レビュー済み引き継ぎのみ' : 'Reviewed handoff only'}</h3>
              <p className="mt-1 text-sm text-teal-900">{isJapanese ? 'API v13では外部システムへの直接送信を停止しています。確認済みのMarkdown、JSON、Jira、Azure Boards、ServiceNow、NetSuite形式をエクスポートして、会社の承認手順で登録してください。' : 'API v13 keeps direct external delivery disabled. Export reviewed Markdown, JSON, Jira, Azure Boards, ServiceNow, or NetSuite drafts and submit them through your company approval workflow.'}</p>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <h3 className="font-semibold">{isJapanese ? '会社ルールパック' : 'Company rule packs'}</h3>
              <p className="mt-1 text-sm text-slate-600">
                {isJapanese ? '会社ITから受領したEd25519公開鍵と署名済みZIPのみを登録します。' : 'Enroll only an Ed25519 public key and signed ZIP supplied by company IT.'}
              </p>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <input value={ruleKeyId} onChange={(event) => setRuleKeyId(event.target.value)} aria-label="Rule signing key ID" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                <input value={ruleKeyLabel} onChange={(event) => setRuleKeyLabel(event.target.value)} aria-label="Rule signing key label" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                <input type="password" autoComplete="off" value={rulePublicKey} onChange={(event) => setRulePublicKey(event.target.value)} aria-label="Ed25519 public key in Base64" placeholder="Base64 public key" className="rounded-lg border border-slate-300 px-3 py-2 text-sm sm:col-span-2" />
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => void enrollRuleKey()} disabled={!ruleKeyId.trim() || !ruleKeyLabel.trim() || !rulePublicKey.trim()}>
                  {isJapanese ? '公開鍵を登録' : 'Enroll public key'}
                </Button>
                <label className="cursor-pointer rounded-md bg-slate-950 px-4 py-2 text-sm font-semibold text-white focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-teal-700">
                  {isJapanese ? '署名済みZIPを確認' : 'Verify signed ZIP'}
                  <input type="file" accept=".zip,application/zip" className="sr-only" onChange={(event) => { const file = event.target.files?.[0]; if (file) void installRulePack(file); event.target.value = ''; }} />
                </label>
                <label className="cursor-pointer rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-900 focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-teal-700">
                  {isJapanese ? '署名済みデータ品質ZIP' : 'Signed data-quality ZIP'}
                  <input type="file" accept=".zip,application/zip" className="sr-only" onChange={(event) => { const file = event.target.files?.[0]; if (file) void installQualityRulePack(file); event.target.value = ''; }} />
                </label>
              </div>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <h3 className="font-semibold">{isJapanese ? 'ERPメタデータ確認' : 'ERP metadata validation'}</h3>
              <div className="mt-3 flex flex-wrap gap-2">
                {connectors.filter((value) => ['odata', 'sap', 'dynamics365', 'netsuite'].includes(value.kind)).map((connector) => (
                  <Button key={connector.connector_id} variant="outline" size="sm" onClick={() => void validateErp(connector)}>
                    <Database size={15} /> {connector.display_name}
                  </Button>
                ))}
                {!connectors.some((value) => ['odata', 'sap', 'dynamics365', 'netsuite'].includes(value.kind)) && <p className="text-sm text-slate-500">{isJapanese ? '読み取り専用ERPコネクターは未設定です。' : 'No read-only ERP connector is configured.'}</p>}
              </div>
            </div>
            <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4">
              <h3 className="font-semibold text-amber-950">{isJapanese ? 'NetSuite異常受信箱' : 'NetSuite anomaly inbox'}</h3>
              <div className="mt-3 flex flex-wrap gap-2">
                {connectors.filter((value) => value.kind === 'netsuite').map((connector) => (
                  <Button key={connector.connector_id} variant="outline" size="sm" onClick={() => void inspectNetSuite(connector)}>
                    <RefreshCw size={15} /> {isJapanese ? '変更を確認' : 'Inspect changes'} · {connector.display_name}
                  </Button>
                ))}
              </div>
              <div className="mt-3 space-y-2">
                {dataFindings.map((finding) => (
                  <article key={finding.finding_id} className="rounded-xl border border-amber-200 bg-white p-3">
                    <p className="font-semibold text-slate-950">{finding.title}</p>
                    <p className="text-xs text-slate-600">{finding.severity.toUpperCase()} · {finding.source_reference}</p>
                    <p className="mt-1 text-sm text-slate-700">{finding.description}</p>
                    {finding.status === 'open' && (
                      <div className="mt-2 flex gap-2">
                        <Button size="sm" onClick={() => void reviewDataQualityFinding(finding, 'confirm').then(refresh)}>{isJapanese ? '確認' : 'Confirm'}</Button>
                        <Button size="sm" variant="outline" onClick={() => void reviewDataQualityFinding(finding, 'dismiss').then(refresh)}>{isJapanese ? '除外' : 'Dismiss'}</Button>
                      </div>
                    )}
                  </article>
                ))}
                {dataFindings.length === 0 && <p className="text-sm text-slate-600">{isJapanese ? '未確認の異常はありません。' : 'No unreviewed anomalies.'}</p>}
              </div>
            </div>
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="rounded-2xl border border-slate-200 bg-white p-4">
                <h3 className="font-semibold">{isJapanese ? '引き継ぎドラフト' : 'Reviewed handoff drafts'}</h3>
                <div className="mt-2 space-y-2">
                  {externalActions.slice(0, 10).map((action) => (
                    <article key={action.action_id} className="rounded-lg bg-slate-50 p-2 text-sm">
                      <p>{action.destination_label} · {action.status.replace('_', ' ')} · {action.source_reference}</p>
                      {['pending_confirmation', 'drafted', 'approved'].includes(action.status) && (
                        <>
                          <p className="mt-2 whitespace-pre-wrap rounded bg-white p-2 text-xs text-slate-700">{action.rendered_preview}</p>
                          <div className="mt-2 flex flex-wrap gap-2">
                            <Button size="sm" onClick={() => void exportExternalAction(action, 'combined_zip').catch(() => toast.error(isJapanese ? 'エクスポートできませんでした' : 'Could not export the reviewed draft'))}>{isJapanese ? 'レビュー用ZIP' : 'Export review ZIP'}</Button>
                            <Button size="sm" variant="outline" onClick={() => void exportExternalAction(action, 'canonical_json').catch(() => toast.error(isJapanese ? 'エクスポートできませんでした' : 'Could not export the reviewed draft'))}>JSON</Button>
                          </div>
                        </>
                      )}
                    </article>
                  ))}
                  {externalActions.length === 0 && <p className="text-sm text-slate-500">{isJapanese ? '引き継ぎドラフトはありません。' : 'No reviewed handoff drafts.'}</p>}
                </div>
              </div>
              <div className="rounded-2xl border border-slate-200 bg-white p-4">
                <h3 className="font-semibold">{isJapanese ? '文書振り分け候補' : 'Document routing review'}</h3>
                <div className="mt-2 grid gap-2 rounded-lg border border-slate-200 p-3">
                  <input value={routeLabel} onChange={(event) => setRouteLabel(event.target.value)} placeholder={isJapanese ? '振り分け先ラベル' : 'Destination label'} className="rounded border border-slate-300 px-2 py-1 text-sm" />
                  <input value={routePartnerReference} onChange={(event) => setRoutePartnerReference(event.target.value)} placeholder={isJapanese ? 'NetSuite取引先キー' : 'NetSuite partner business key'} className="rounded border border-slate-300 px-2 py-1 text-sm" />
                  <input type="password" autoComplete="off" value={routeRoot} onChange={(event) => setRouteRoot(event.target.value)} placeholder={isJapanese ? '承認済みローカルフォルダー' : 'Approved local folder'} className="rounded border border-slate-300 px-2 py-1 text-sm" />
                  <Button size="sm" variant="outline" disabled={!routeLabel.trim() || !routePartnerReference.trim() || !routeRoot.trim()} onClick={() => void saveRoute()}>{isJapanese ? '振り分け先を保存' : 'Save approved destination'}</Button>
                </div>
                <div className="mt-2 space-y-2">
                  {routingProposals.map((proposal) => (
                    <article key={proposal.proposal_id} className="rounded-lg bg-slate-50 p-2 text-sm">
                      <p className="font-medium">{proposal.source_reference} → {proposal.destination_label}</p>
                      <p className="text-xs text-slate-500">{proposal.partner_label} · {proposal.confidence}%</p>
                      <div className="mt-2 flex gap-2">
                        <Button size="sm" onClick={() => void reviewDocumentRoutingProposal(proposal, 'execute', 'copy').then(refresh)}>{isJapanese ? 'コピー' : 'Copy'}</Button>
                        <Button size="sm" variant="outline" onClick={() => void reviewDocumentRoutingProposal(proposal, 'cancel').then(refresh)}>{isJapanese ? '取消' : 'Cancel'}</Button>
                      </div>
                    </article>
                  ))}
                  {routingProposals.length === 0 && <p className="text-sm text-slate-500">{isJapanese ? '候補はありません。' : 'No routing proposals.'}</p>}
                </div>
              </div>
            </div>
          </section>
        )}
      </DialogContent>
    </Dialog>
  );
}
