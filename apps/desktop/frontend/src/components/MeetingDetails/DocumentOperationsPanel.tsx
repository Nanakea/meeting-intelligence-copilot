import { RefreshCw } from 'lucide-react';

import { Button } from '@/components/ui/button';
import type {
  DocumentInbox,
  DocumentRoutingDestination,
  DocumentScanStatus,
  ManagedDocumentClassification,
  ManagedDocumentDisposition,
} from '@/services/intelligenceContracts';

interface DocumentOperationsPanelProps {
  inbox: DocumentInbox;
  scan: DocumentScanStatus;
  destinations: DocumentRoutingDestination[];
  isJapanese: boolean;
  onScan: () => void;
  onReviewClassification: (
    classification: ManagedDocumentClassification,
    action: 'approve' | 'archive',
  ) => void;
  onReviewDisposition: (
    disposition: ManagedDocumentDisposition,
    action: 'approve' | 'cancel',
  ) => void;
  onExecute: (disposition: ManagedDocumentDisposition, destinationRef: string) => void;
}

export function DocumentOperationsPanel({
  inbox,
  scan,
  destinations,
  isJapanese,
  onScan,
  onReviewClassification,
  onReviewDisposition,
  onExecute,
}: DocumentOperationsPanelProps) {
  const proposed = inbox.dispositions.filter((value) => value.status === 'proposed');
  const reviewed = inbox.dispositions.filter((value) => value.status === 'reviewed');

  return (
    <div className="rounded-2xl border border-sky-200 bg-sky-50 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold text-sky-950">
          {isJapanese ? '管理文書受信箱' : 'Managed document inbox'}
        </h3>
        <Button size="sm" variant="outline" onClick={onScan}>
          <RefreshCw size={14} /> {isJapanese ? '変更を確認' : 'Scan changes'}
        </Button>
      </div>
      <p className="mt-1 text-sm text-sky-900">
        {inbox.duplicate_groups.length}{' '}
        {isJapanese
          ? '件の重複候補。承認してもファイルは自動移動されません。'
          : 'duplicate groups. Review never moves a file automatically.'}
      </p>
      <p className="mt-1 text-xs text-sky-800" role="status" aria-live="polite">
        {scan.phase} · {scan.indexed_revisions}{' '}
        {isJapanese ? '版索引済み' : 'revisions indexed'} · OCR {scan.ocr_status}
      </p>
      <div className="mt-3 max-h-72 space-y-2 overflow-y-auto">
        {inbox.classifications.slice(0, 30).map((classification) => (
          <article
            key={classification.classification_id}
            className="rounded-lg border border-sky-200 bg-white p-3 text-sm"
          >
            <p className="font-semibold text-slate-950">{classification.source_reference}</p>
            <p className="text-xs text-slate-500">
              {classification.document_kind} · {classification.confidence}% ·{' '}
              {classification.lifecycle}
            </p>
            {classification.lifecycle === 'proposed' && (
              <div className="mt-2 flex gap-2">
                <Button size="sm" onClick={() => onReviewClassification(classification, 'approve')}>
                  {isJapanese ? '分類を承認' : 'Approve classification'}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => onReviewClassification(classification, 'archive')}
                >
                  {isJapanese ? '保留' : 'Archive'}
                </Button>
              </div>
            )}
          </article>
        ))}
        {inbox.classifications.length === 0 && (
          <p className="text-sm text-sky-800">
            {isJapanese ? '索引済みローカル文書はありません。' : 'No indexed local documents.'}
          </p>
        )}
      </div>
      {proposed.length > 0 && (
        <div className="mt-3 border-t border-sky-200 pt-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-sky-800">
            {isJapanese ? '整理案' : 'Disposition proposals'}
          </p>
          {proposed.slice(0, 10).map((disposition) => (
            <div
              key={disposition.disposition_id}
              className="mt-2 flex items-center justify-between gap-2 text-sm"
            >
              <span className="truncate">
                {disposition.source_reference} → {disposition.collection}
              </span>
              <div className="flex gap-1">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => onReviewDisposition(disposition, 'approve')}
                >
                  {isJapanese ? '採用' : 'Keep'}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => onReviewDisposition(disposition, 'cancel')}
                >
                  {isJapanese ? '取消' : 'Cancel'}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
      {reviewed.length > 0 && (
        <div className="mt-3 border-t border-sky-200 pt-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-sky-800">
            {isJapanese ? '確認済みコピー' : 'Reviewed copy operations'}
          </p>
          {reviewed.slice(0, 10).map((disposition) => (
            <div
              key={disposition.disposition_id}
              className="mt-2 flex flex-wrap items-center justify-between gap-2 text-sm"
            >
              <span className="truncate">{disposition.safe_filename}</span>
              <div className="flex flex-wrap gap-1">
                {destinations.map((destination) => (
                  <Button
                    key={destination.destination_ref}
                    size="sm"
                    variant="outline"
                    onClick={() => onExecute(disposition, destination.destination_ref)}
                  >
                    {isJapanese ? 'コピー先' : 'Copy to'} {destination.label}
                  </Button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
