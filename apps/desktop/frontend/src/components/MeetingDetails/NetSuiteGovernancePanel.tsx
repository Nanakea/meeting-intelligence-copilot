import { Database, ShieldCheck } from 'lucide-react';

import { Button } from '@/components/ui/button';
import type {
  ConnectorHealth,
  ERPGovernanceDashboard,
  NetSuiteMappingValidation,
} from '@/services/intelligenceContracts';

interface NetSuiteGovernancePanelProps {
  connectors: ConnectorHealth[];
  dashboard: ERPGovernanceDashboard;
  validation: NetSuiteMappingValidation | null;
  isJapanese: boolean;
  onValidate: (connector: ConnectorHealth) => void;
  onRun: (connector: ConnectorHealth) => void;
}

export function NetSuiteGovernancePanel({
  connectors,
  dashboard,
  validation,
  isJapanese,
  onValidate,
  onRun,
}: NetSuiteGovernancePanelProps) {
  const netsuite = connectors.filter((connector) => connector.kind === 'netsuite');

  return (
    <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4">
      <h3 className="font-semibold text-amber-950">
        {isJapanese ? 'NetSuite業務プロセス管理' : 'NetSuite process governance'}
      </h3>
      <p className="mt-1 text-sm text-amber-900">
        {dashboard.active_processes.length} {isJapanese ? 'プロセス有効' : 'active processes'} ·{' '}
        {dashboard.ownerless_findings} {isJapanese ? '件の所有者未設定' : 'ownerless findings'}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {netsuite.map((connector) => (
          <div key={connector.connector_id} className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={() => onValidate(connector)}>
              <ShieldCheck size={15} /> {isJapanese ? 'マッピング検証' : 'Validate mappings'}
            </Button>
            <Button size="sm" disabled={validation?.status !== 'valid'} onClick={() => onRun(connector)}>
              <Database size={15} /> {isJapanese ? '全プロセス確認' : 'Check all processes'} ·{' '}
              {connector.display_name}
            </Button>
          </div>
        ))}
      </div>
      {validation && (
        <p className="mt-2 text-xs text-amber-900" role="status" aria-live="polite">
          {validation.status} · {validation.active_templates.length}{' '}
          {isJapanese ? 'テンプレート有効' : 'templates active'} · {validation.issues.length}{' '}
          {isJapanese ? '件要確認' : 'issues'}
        </p>
      )}
      <div className="mt-3 space-y-2">
        {dashboard.runs[0]?.recommendations.slice(0, 20).map((recommendation) => (
          <article
            key={recommendation.recommendation_id}
            className="rounded-lg border border-amber-200 bg-white p-3 text-sm"
          >
            <p className="font-semibold text-slate-950">{recommendation.title}</p>
            <p className="text-xs text-amber-800">
              {recommendation.kind.replaceAll('_', ' ')} · {recommendation.source_reference}
            </p>
            <p className="mt-1 text-slate-600">{recommendation.reason}</p>
            <p className="mt-1 text-xs text-slate-500">
              {isJapanese
                ? '確認が必要。NetSuite運用レコードは変更されません。'
                : 'Review required. No operational NetSuite record is changed.'}
            </p>
          </article>
        ))}
        {!dashboard.runs[0] && (
          <p className="text-sm text-amber-800">
            {isJapanese
              ? 'NetSuiteマッピング検証後に実行できます。'
              : 'Available after NetSuite mappings are validated.'}
          </p>
        )}
      </div>
    </div>
  );
}
