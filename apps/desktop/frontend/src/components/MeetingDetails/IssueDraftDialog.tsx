'use client';

import { useEffect, useState } from 'react';
import { FileText } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import type { StoredMeetingIssueDraft } from '@/services/intelligenceIssueDraft';

const COPY = {
  ja: {
    button: '起票の下書き',
    title: '起票の下書き',
    description: '会議中に確認できた事実と、まだ確認が必要な点です。',
    ready: '未確認事項はありません',
    unresolved: (count: number) => `未確認事項: ${count}件`,
    copy: '下書きをコピー',
    copied: 'コピーしました',
    failed: 'コピーできませんでした',
  },
  en: {
    button: 'Issue draft',
    title: 'Issue draft',
    description: 'Active facts and questions that still need clarification from the meeting.',
    ready: 'No open questions remain',
    unresolved: (count: number) => `${count} open ${count === 1 ? 'question' : 'questions'}`,
    copy: 'Copy issue draft',
    copied: 'Copied',
    failed: 'Could not copy',
  },
  ko: {
    button: '이슈 초안',
    title: '이슈 초안',
    description: '회의에서 확인된 사실과 추가로 확인할 질문입니다.',
    ready: '열린 질문이 없습니다',
    unresolved: (count: number) => `열린 질문 ${count}개`,
    copy: '이슈 초안 복사',
    copied: '복사했습니다',
    failed: '복사하지 못했습니다',
  },
} as const;

export function IssueDraftDialog({ draft }: { draft: StoredMeetingIssueDraft }) {
  const [copyStatus, setCopyStatus] = useState<'idle' | 'copied' | 'failed'>('idle');
  const t = COPY[draft.language];

  useEffect(() => {
    setCopyStatus('idle');
  }, [draft.draft_markdown, draft.updated_at]);

  const copyDraft = async () => {
    try {
      await navigator.clipboard.writeText(draft.draft_markdown);
      setCopyStatus('copied');
      toast.success(t.copied);
    } catch {
      setCopyStatus('failed');
      toast.error(t.failed);
    }
  };

  return (
    <Dialog onOpenChange={(open) => !open && setCopyStatus('idle')}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" aria-label={t.button}>
          <FileText size={16} />
          <span>{t.button}</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl bg-slate-50">
        <DialogHeader>
          <DialogTitle>{t.title}</DialogTitle>
          <DialogDescription>{t.description}</DialogDescription>
        </DialogHeader>
        <div
          className={`rounded-lg border px-3 py-2 text-xs font-semibold ${
            draft.unresolved_count === 0
              ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
              : 'border-amber-200 bg-amber-50 text-amber-800'
          }`}
          role="status"
        >
          {draft.unresolved_count === 0
            ? t.ready
            : t.unresolved(draft.unresolved_count)}
        </div>
        <pre className="max-h-[55vh] overflow-auto whitespace-pre-wrap break-words rounded-xl border border-slate-200 bg-white p-4 font-sans text-sm leading-relaxed text-slate-700">
          {draft.draft_markdown}
        </pre>
        <Button type="button" onClick={() => void copyDraft()} aria-live="polite">
          {copyStatus === 'copied'
            ? t.copied
            : copyStatus === 'failed'
              ? t.failed
              : t.copy}
        </Button>
      </DialogContent>
    </Dialog>
  );
}
