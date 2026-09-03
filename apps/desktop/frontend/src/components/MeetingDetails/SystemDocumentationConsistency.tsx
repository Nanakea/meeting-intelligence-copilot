'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ArrowLeftRight,
  CheckCircle2,
  FileDown,
  RefreshCw,
  ShieldQuestion,
} from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  exportConsistencyFinding,
  getAuthorityPolicies,
  getConsistencyDashboard,
  getConsistencyFindings,
  reviewConsistencyClaim,
  reviewConsistencyFinding,
  runConsistencyCheck,
  saveAuthorityPolicy,
} from '@/services/assuranceService';
import { listContextConnectors } from '@/services/contextConnectorService';
import type {
  AuthorityPolicy,
  ConnectorHealth,
  ConsistencyAttribution,
  ConsistencyDashboard,
  ConsistencyFinding,
  ConsistencyFindingStatus,
  ConsistencyMismatchKind,
} from '@/services/intelligenceContracts';

const DOCUMENT_KINDS = new Set(['local_files', 'microsoft_graph', 'confluence', 'sftp']);
const OBSERVED_KINDS = new Set([
  'github', 'gitlab', 'openapi', 'azure_devops', 'servicenow',
  'netsuite', 'odata', 'sap', 'dynamics365',
]);

const EMPTY_DASHBOARD: ConsistencyDashboard = {
  generated_at: '',
  open_findings: 0,
  confirmed_findings: 0,
  approved_exceptions: 0,
  ambiguous_findings: 0,
  by_mismatch_kind: {},
  by_severity: {},
  by_attribution: {},
  proposed_semantic_claims: [],
  recent_findings: [],
};

const ATTRIBUTIONS: ConsistencyAttribution[] = [
  'neutral', 'documentation_drift', 'implementation_drift',
  'configuration_drift', 'release_drift', 'ambiguous',
];

function displayValue(value: string | null | undefined, isJapanese: boolean): string {
  return value ?? (isJapanese ? '再認証して値を更新' : 'Refresh authorization to view');
}

function revisionLabel(value: string): string {
  return value.length > 20 ? `${value.slice(0, 12)}…` : value;
}

function locatorLabel(finding: ConsistencyFinding, side: 'document' | 'system'): string {
  const citation = side === 'document'
    ? finding.document_claim.citation
    : finding.observed_fact.citation;
  const locator = citation.locator;
  if (locator.page) return `p.${locator.page}`;
  if (locator.sheet) return `${locator.sheet}${locator.cell_range ? ` ${locator.cell_range}` : ''}`;
  if (locator.start_line) return `L${locator.start_line}${locator.end_line ? `–${locator.end_line}` : ''}`;
  return locator.heading ?? '';
}

export function SystemDocumentationConsistency({ isJapanese }: { isJapanese: boolean }) {
  const [dashboard, setDashboard] = useState(EMPTY_DASHBOARD);
  const [findings, setFindings] = useState<ConsistencyFinding[]>([]);
  const [connectors, setConnectors] = useState<ConnectorHealth[]>([]);
  const [policies, setPolicies] = useState<AuthorityPolicy[]>([]);
  const [documentIds, setDocumentIds] = useState<string[]>([]);
  const [observedIds, setObservedIds] = useState<string[]>([]);
  const [environment, setEnvironment] = useState('');
  const [statusFilter, setStatusFilter] = useState<ConsistencyFindingStatus | ''>('open');
  const [kindFilter, setKindFilter] = useState<ConsistencyMismatchKind | ''>('');
  const [severityFilter, setSeverityFilter] = useState('');
  const [systemFilter, setSystemFilter] = useState('');
  const [projectFilter, setProjectFilter] = useState('');
  const [repositoryFilter, setRepositoryFilter] = useState('');
  const [documentFilter, setDocumentFilter] = useState('');
  const [ownerFilter, setOwnerFilter] = useState('');
  const [ownerDrafts, setOwnerDrafts] = useState<Record<string, string>>({});
  const [exportFormat, setExportFormat] = useState<'markdown' | 'canonical_json' | 'jira_csv' | 'azure_boards_csv'>('markdown');
  const [draftKind, setDraftKind] = useState<'issue' | 'risk' | 'action' | 'adr' | 'mapping_change'>('issue');
  const [working, setWorking] = useState(false);
  const [exceptionId, setExceptionId] = useState<string | null>(null);
  const [exceptionReason, setExceptionReason] = useState('');
  const [exceptionExpiry, setExceptionExpiry] = useState('');
  const [policySubject, setPolicySubject] = useState('*');
  const [policyProperty, setPolicyProperty] = useState('*');
  const [policyAuthority, setPolicyAuthority] = useState<AuthorityPolicy['authority']>('neutral');

  const refresh = useCallback(async () => {
    const [nextDashboard, nextFindings, nextConnectors, nextPolicies] = await Promise.all([
      getConsistencyDashboard().catch(() => EMPTY_DASHBOARD),
      getConsistencyFindings().catch(() => []),
      listContextConnectors().catch(() => []),
      getAuthorityPolicies().catch(() => []),
    ]);
    setDashboard(nextDashboard);
    setFindings(nextFindings);
    setConnectors(nextConnectors);
    setPolicies(nextPolicies);
    setDocumentIds((current) => current.length ? current : nextConnectors
      .filter((value) => DOCUMENT_KINDS.has(value.kind))
      .slice(0, 1)
      .map((value) => value.connector_id));
    setObservedIds((current) => current.length ? current : nextConnectors
      .filter((value) => OBSERVED_KINDS.has(value.kind))
      .slice(0, 1)
      .map((value) => value.connector_id));
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const visibleFindings = useMemo(() => findings.filter((finding) => (
    (!statusFilter || finding.status === statusFilter)
    && (!kindFilter || finding.mismatch_kind === kindFilter)
    && (!environment
      || finding.document_claim.environment === environment
      || finding.observed_fact.environment === environment)
    && (!severityFilter || finding.severity === severityFilter)
    && (!systemFilter || finding.subject.toLocaleLowerCase().includes(systemFilter.toLocaleLowerCase()))
    && (!projectFilter || `${finding.document_claim.citation.source_label} ${finding.observed_fact.citation.source_label}`.toLocaleLowerCase().includes(projectFilter.toLocaleLowerCase()))
    && (!repositoryFilter || finding.observed_fact.citation.source_reference.toLocaleLowerCase().includes(repositoryFilter.toLocaleLowerCase()))
    && (!documentFilter || `${finding.document_claim.citation.source_label} ${finding.document_claim.citation.source_reference}`.toLocaleLowerCase().includes(documentFilter.toLocaleLowerCase()))
    && (!ownerFilter || (finding.owner_role ?? '').toLocaleLowerCase().includes(ownerFilter.toLocaleLowerCase()))
  )), [documentFilter, environment, findings, kindFilter, ownerFilter, projectFilter, repositoryFilter, severityFilter, statusFilter, systemFilter]);

  const toggle = (id: string, values: string[], setValues: (next: string[]) => void) => {
    setValues(values.includes(id) ? values.filter((value) => value !== id) : [...values, id]);
  };

  const run = async () => {
    if (!documentIds.length || !observedIds.length) {
      toast.error(isJapanese ? '文書とシステムの接続を選択してください' : 'Select documentation and system connections');
      return;
    }
    setWorking(true);
    try {
      const result = await runConsistencyCheck({
        document_connector_ids: documentIds,
        observed_connector_ids: observedIds,
        language: isJapanese ? 'ja' : 'en',
        environment: environment || undefined,
      });
      toast.success(isJapanese
        ? `${result.finding_count}件の差異を検出しました`
        : `${result.finding_count} discrepancies found`);
      await refresh();
    } catch {
      toast.error(isJapanese ? '比較を完了できませんでした' : 'Could not complete the comparison');
    } finally {
      setWorking(false);
    }
  };

  const review = async (
    finding: ConsistencyFinding,
    action: 'confirm' | 'dismiss' | 'resolve' | 'reopen' | 'request_refresh',
  ) => {
    try {
      await reviewConsistencyFinding(finding, { action });
      await refresh();
    } catch {
      toast.error(isJapanese ? '項目が更新されています。再読み込みしてください' : 'The finding changed. Refresh and try again.');
    }
  };

  const assignOwner = async (finding: ConsistencyFinding) => {
    const ownerRole = (ownerDrafts[finding.finding_id] ?? finding.owner_role ?? '').trim();
    if (!ownerRole) return;
    try {
      await reviewConsistencyFinding(finding, { action: 'assign', owner_role: ownerRole });
      await refresh();
    } catch {
      toast.error(isJapanese ? '担当ロールを保存できませんでした' : 'Could not save the owner role');
    }
  };

  const classify = async (finding: ConsistencyFinding, attribution: ConsistencyAttribution) => {
    try {
      await reviewConsistencyFinding(finding, { action: 'classify', attribution });
      await refresh();
    } catch {
      toast.error(isJapanese ? '分類を保存できませんでした' : 'Could not save the classification');
    }
  };

  const approveException = async (finding: ConsistencyFinding) => {
    if (!exceptionReason.trim() || !exceptionExpiry) return;
    try {
      await reviewConsistencyFinding(finding, {
        action: 'mark_exception',
        exception_reason: exceptionReason.trim(),
        exception_expires_at: new Date(`${exceptionExpiry}T23:59:59`).toISOString(),
      });
      setExceptionId(null);
      setExceptionReason('');
      setExceptionExpiry('');
      await refresh();
    } catch {
      toast.error(isJapanese ? '例外を保存できませんでした' : 'Could not save the exception');
    }
  };

  const addPolicy = async () => {
    const now = new Date().toISOString();
    try {
      await saveAuthorityPolicy({
        policy_id: `authority-${crypto.randomUUID().replaceAll('-', '')}`,
        label: `${policySubject} · ${policyProperty}`,
        subject_pattern: policySubject,
        property_pattern: policyProperty,
        environment: environment || null,
        authority: policyAuthority,
        priority: 100,
        enabled: true,
        revision: 1,
        reviewed_at: now,
      });
      await refresh();
    } catch {
      toast.error(isJapanese ? '権威ルールを保存できませんでした' : 'Could not save the authority policy');
    }
  };

  const documentConnectors = connectors.filter((value) => DOCUMENT_KINDS.has(value.kind));
  const observedConnectors = connectors.filter((value) => OBSERVED_KINDS.has(value.kind));

  return (
    <section className="space-y-5" aria-live="polite">
      <div className="overflow-hidden rounded-2xl border border-cyan-200 bg-[linear-gradient(135deg,#082f49_0%,#134e4a_55%,#422006_100%)] p-5 text-white">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.18em] text-cyan-200">
              <ArrowLeftRight size={16} /> {isJapanese ? 'システム対文書' : 'System vs Documentation'}
            </div>
            <h3 className="mt-2 text-xl font-bold">
              {isJapanese ? '何が、どこで、なぜ違うか' : 'What differs, where, and why'}
            </h3>
            <p className="mt-2 max-w-3xl text-sm leading-relaxed text-cyan-50">
              {isJapanese
                ? '承認済み文書の主張と、選択したコード・API・ERP・CMDBの観測値を版付きで比較します。既定ではどちらも正と決めません。'
                : 'Compare approved document claims with versioned code, API, ERP, and CMDB observations. Neither side wins by default.'}
            </p>
          </div>
          <Button onClick={() => void run()} disabled={working} className="bg-white text-slate-950 hover:bg-cyan-50">
            {working ? <RefreshCw size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}
            {isJapanese ? '差異チェック' : 'Run comparison'}
          </Button>
        </div>
        <div className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {[
            [dashboard.open_findings, isJapanese ? '未確認' : 'Open'],
            [dashboard.confirmed_findings, isJapanese ? '確認済み' : 'Confirmed'],
            [dashboard.approved_exceptions, isJapanese ? '承認例外' : 'Exceptions'],
            [dashboard.ambiguous_findings, isJapanese ? '照合不明' : 'Ambiguous'],
          ].map(([value, label]) => (
            <div key={label} className="rounded-xl bg-white/10 px-3 py-2 backdrop-blur">
              <div className="text-2xl font-black tabular-nums">{value}</div>
              <div className="text-xs text-cyan-100">{label}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <fieldset className="rounded-2xl border border-slate-200 bg-white p-4">
          <legend className="px-1 text-sm font-bold text-slate-900">{isJapanese ? '承認済み文書' : 'Approved documentation'}</legend>
          <div className="mt-2 grid gap-2">
            {documentConnectors.map((connector) => (
              <label key={connector.connector_id} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm">
                <input type="checkbox" checked={documentIds.includes(connector.connector_id)} onChange={() => toggle(connector.connector_id, documentIds, setDocumentIds)} />
                <span>{connector.display_name}</span>
              </label>
            ))}
            {!documentConnectors.length && <p className="text-sm text-amber-800">{isJapanese ? '文書接続を設定してください。' : 'Configure a documentation connection.'}</p>}
          </div>
        </fieldset>
        <fieldset className="rounded-2xl border border-slate-200 bg-white p-4">
          <legend className="px-1 text-sm font-bold text-slate-900">{isJapanese ? '観測するシステム' : 'Observed systems'}</legend>
          <div className="mt-2 grid gap-2">
            {observedConnectors.map((connector) => (
              <label key={connector.connector_id} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm">
                <input type="checkbox" checked={observedIds.includes(connector.connector_id)} onChange={() => toggle(connector.connector_id, observedIds, setObservedIds)} />
                <span>{connector.display_name}</span>
              </label>
            ))}
            {!observedConnectors.length && <p className="text-sm text-amber-800">{isJapanese ? 'システム接続を設定してください。' : 'Configure a system connection.'}</p>}
          </div>
        </fieldset>
      </div>

      <div className="grid gap-3 rounded-2xl border border-slate-200 bg-white p-4 sm:grid-cols-2 xl:grid-cols-4">
        <label className="text-xs font-semibold text-slate-600">{isJapanese ? '環境' : 'Environment'}
          <input value={environment} onChange={(event) => setEnvironment(event.target.value)} placeholder="production" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        </label>
        <label className="text-xs font-semibold text-slate-600">{isJapanese ? '状態' : 'Status'}
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as ConsistencyFindingStatus | '')} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="">{isJapanese ? 'すべて' : 'All'}</option>
            {['open', 'confirmed', 'dismissed', 'resolved', 'approved_exception', 'superseded'].map((value) => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}
          </select>
        </label>
        <label className="text-xs font-semibold text-slate-600">{isJapanese ? '差異種別' : 'Mismatch type'}
          <select value={kindFilter} onChange={(event) => setKindFilter(event.target.value as ConsistencyMismatchKind | '')} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="">{isJapanese ? 'すべて' : 'All'}</option>
            {Object.keys(dashboard.by_mismatch_kind).map((value) => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}
          </select>
        </label>
        <label className="text-xs font-semibold text-slate-600">{isJapanese ? '重大度' : 'Severity'}
          <select value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="">{isJapanese ? 'すべて' : 'All'}</option>
            {['info', 'warning', 'high', 'critical'].map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label className="text-xs font-semibold text-slate-600">{isJapanese ? 'システム' : 'System'}
          <input value={systemFilter} onChange={(event) => setSystemFilter(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        </label>
        <label className="text-xs font-semibold text-slate-600">{isJapanese ? 'プロジェクト' : 'Project'}
          <input value={projectFilter} onChange={(event) => setProjectFilter(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        </label>
        <label className="text-xs font-semibold text-slate-600">{isJapanese ? 'リポジトリ' : 'Repository'}
          <input value={repositoryFilter} onChange={(event) => setRepositoryFilter(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        </label>
        <label className="text-xs font-semibold text-slate-600">{isJapanese ? '文書' : 'Document'}
          <input value={documentFilter} onChange={(event) => setDocumentFilter(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        </label>
        <label className="text-xs font-semibold text-slate-600">{isJapanese ? '担当ロール／チーム' : 'Owner role/team'}
          <input value={ownerFilter} onChange={(event) => setOwnerFilter(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        </label>
      </div>

      {dashboard.proposed_semantic_claims.length > 0 && (
        <div className="space-y-2 rounded-2xl border border-sky-200 bg-sky-50 p-4">
          <h3 className="font-bold text-sky-950">{isJapanese ? 'AIが提案した文書主張' : 'AI-proposed document claims'}</h3>
          <p className="text-xs text-sky-800">{isJapanese ? '確認するまで比較には使用されません。' : 'These are excluded from comparisons until you confirm them.'}</p>
          {dashboard.proposed_semantic_claims.map((claim) => (
            <div key={claim.claim_id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-white p-3">
              <div>
                <p className="font-semibold text-slate-950">{claim.canonical_subject} · {claim.canonical_property}</p>
                <p className="text-sm text-slate-700">{claim.expected_value}</p>
                <p className="text-xs text-slate-500">{claim.citation.source_reference}</p>
              </div>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => void reviewConsistencyClaim(claim, 'confirm').then(refresh)}>{isJapanese ? '主張を確認' : 'Confirm claim'}</Button>
                <Button size="sm" variant="outline" onClick={() => void reviewConsistencyClaim(claim, 'dismiss').then(refresh)}>{isJapanese ? '除外' : 'Dismiss'}</Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-4">
        {!visibleFindings.length && <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5 text-sm text-emerald-900">{isJapanese ? 'この条件で確認待ちの差異はありません。' : 'No discrepancies match these filters.'}</div>}
        {visibleFindings.map((finding) => (
          <article key={finding.finding_id} className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 bg-slate-50 px-4 py-3">
              <div>
                <div className="flex flex-wrap items-center gap-2 text-xs font-bold uppercase tracking-wide text-slate-500">
                  <AlertTriangle size={14} /> {finding.severity} · {finding.mismatch_kind.replaceAll('_', ' ')}
                </div>
                <h3 className="mt-1 text-lg font-bold text-slate-950">{finding.subject} · {finding.property_name}</h3>
              </div>
              <span className="rounded-full border border-slate-300 bg-white px-3 py-1 text-xs font-semibold text-slate-700">{finding.comparison.attribution.replaceAll('_', ' ')}</span>
            </header>
            <div className="grid lg:grid-cols-2">
              {([
                ['document', isJapanese ? '文書の主張' : 'Documentation states', finding.comparison.expected_value, finding.document_claim.citation],
                ['system', isJapanese ? 'システムの観測値' : 'System exposes', finding.comparison.observed_value, finding.observed_fact.citation],
              ] as const).map(([side, title, value, citation]) => (
                <section key={side} className={`p-4 ${side === 'document' ? 'border-b border-slate-200 lg:border-b-0 lg:border-r' : ''}`}>
                  <p className="text-xs font-bold uppercase tracking-wide text-slate-500">{title}</p>
                  <p className="mt-2 break-words text-lg font-bold text-slate-950">{displayValue(value, isJapanese)}</p>
                  <p className="mt-3 text-xs leading-relaxed text-slate-600">
                    {citation.source_label} · {citation.source_reference}
                    {locatorLabel(finding, side) ? ` · ${locatorLabel(finding, side)}` : ''}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    {isJapanese ? '版' : 'Revision'} {revisionLabel(citation.source_revision)} · {citation.freshness}
                    {citation.environment ? ` · ${citation.environment}` : ''}
                  </p>
                </section>
              ))}
            </div>
            <div className="border-t border-slate-200 p-4">
              <p className="text-sm leading-relaxed text-slate-800">{finding.comparison.explanation}</p>
              {finding.affected_solution_node_ids.length > 0 && (
                <p className="mt-2 text-xs text-slate-500">
                  {isJapanese
                    ? `${finding.affected_solution_node_ids.length}件のソリューション項目に影響（リンクは確認待ち）`
                    : `${finding.affected_solution_node_ids.length} affected solution items (links await review)`}
                </p>
              )}
              <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                <p className="rounded-lg bg-amber-50 p-3 text-amber-950"><strong>{isJapanese ? '影響' : 'Likely impact'}:</strong> {finding.comparison.likely_impact}</p>
                <p className="rounded-lg bg-cyan-50 p-3 text-cyan-950"><strong>{isJapanese ? '確認方法' : 'Verify'}:</strong> {finding.comparison.recommended_verification}</p>
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-2">
                {finding.status === 'open' && <>
                  <Button size="sm" onClick={() => void review(finding, 'confirm')}>{isJapanese ? '差異を確認' : 'Confirm discrepancy'}</Button>
                  <Button size="sm" variant="outline" onClick={() => void review(finding, 'dismiss')}>{isJapanese ? '該当なし' : 'Not applicable'}</Button>
                </>}
                <label className="text-xs font-semibold text-slate-600">
                  <span className="sr-only">{isJapanese ? '原因分類' : 'Cause classification'}</span>
                  <select value={finding.comparison.attribution} onChange={(event) => void classify(finding, event.target.value as ConsistencyAttribution)} className="rounded-lg border border-slate-300 px-2 py-1.5">
                    {ATTRIBUTIONS.map((value) => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}
                  </select>
                </label>
                <Button size="sm" variant="outline" onClick={() => setExceptionId(finding.finding_id)}><ShieldQuestion size={14} />{isJapanese ? '期限付き例外' : 'Timed exception'}</Button>
                <Button size="sm" variant="outline" onClick={() => void review(finding, 'request_refresh')}><RefreshCw size={14} />{isJapanese ? '情報源を更新' : 'Request refresh'}</Button>
                <input
                  aria-label={isJapanese ? '担当ロールまたはチーム' : 'Owner role or team'}
                  value={ownerDrafts[finding.finding_id] ?? finding.owner_role ?? ''}
                  onChange={(event) => setOwnerDrafts((current) => ({ ...current, [finding.finding_id]: event.target.value }))}
                  placeholder={isJapanese ? '担当ロール／チーム' : 'Owner role/team'}
                  className="w-40 rounded-lg border border-slate-300 px-2 py-1.5 text-xs"
                />
                <Button size="sm" variant="outline" onClick={() => void assignOwner(finding)}>{isJapanese ? '担当を保存' : 'Save owner'}</Button>
                {(finding.status === 'confirmed' || finding.status === 'approved_exception') && <>
                  <select aria-label={isJapanese ? '出力内容' : 'Draft kind'} value={draftKind} onChange={(event) => setDraftKind(event.target.value as typeof draftKind)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
                    {['issue', 'risk', 'action', 'adr', 'mapping_change'].map((value) => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}
                  </select>
                  <select aria-label={isJapanese ? '出力形式' : 'Export format'} value={exportFormat} onChange={(event) => setExportFormat(event.target.value as typeof exportFormat)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
                    <option value="markdown">Markdown</option><option value="canonical_json">JSON</option><option value="jira_csv">Jira CSV</option><option value="azure_boards_csv">Azure CSV</option>
                  </select>
                  <Button size="sm" variant="outline" onClick={() => void exportConsistencyFinding(finding, exportFormat, draftKind)}><FileDown size={14} />{isJapanese ? 'レビュー済み出力' : 'Reviewed export'}</Button>
                </>}
              </div>
              {exceptionId === finding.finding_id && <div className="mt-3 grid gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 sm:grid-cols-[1fr_auto_auto]">
                <label className="text-xs font-semibold text-amber-950">{isJapanese ? '例外理由' : 'Exception reason'}<input value={exceptionReason} onChange={(event) => setExceptionReason(event.target.value)} className="mt-1 w-full rounded-lg border border-amber-300 px-2 py-1.5" /></label>
                <label className="text-xs font-semibold text-amber-950">{isJapanese ? '期限' : 'Expires'}<input type="date" value={exceptionExpiry} onChange={(event) => setExceptionExpiry(event.target.value)} className="mt-1 block rounded-lg border border-amber-300 px-2 py-1.5" /></label>
                <Button size="sm" className="self-end" disabled={!exceptionReason.trim() || !exceptionExpiry} onClick={() => void approveException(finding)}>{isJapanese ? '例外を承認' : 'Approve exception'}</Button>
              </div>}
            </div>
          </article>
        ))}
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white p-4">
        <h3 className="font-bold text-slate-950">{isJapanese ? '情報源の権威ルール' : 'Source authority policies'}</h3>
        <p className="mt-1 text-xs text-slate-600">{isJapanese ? 'ルールがなければ差異は中立です。' : 'Without a reviewed policy, every discrepancy remains neutral.'}</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-4">
          <input aria-label="Authority subject pattern" value={policySubject} onChange={(event) => setPolicySubject(event.target.value)} placeholder="customer*" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <input aria-label="Authority property pattern" value={policyProperty} onChange={(event) => setPolicyProperty(event.target.value)} placeholder="field.*" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <select aria-label="Authoritative side" value={policyAuthority} onChange={(event) => setPolicyAuthority(event.target.value as AuthorityPolicy['authority'])} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="neutral">Neutral</option><option value="documentation">Documentation</option><option value="observed_system">Observed system</option>
          </select>
          <Button onClick={() => void addPolicy()}>{isJapanese ? 'ルールを追加' : 'Add reviewed policy'}</Button>
        </div>
        {policies.length > 0 && <ul className="mt-3 grid gap-2 text-xs text-slate-700 sm:grid-cols-2">{policies.map((policy) => <li key={policy.policy_id} className="rounded-lg border border-slate-200 p-2">{policy.label} · {policy.authority}{policy.environment ? ` · ${policy.environment}` : ''}</li>)}</ul>}
      </div>
    </section>
  );
}
