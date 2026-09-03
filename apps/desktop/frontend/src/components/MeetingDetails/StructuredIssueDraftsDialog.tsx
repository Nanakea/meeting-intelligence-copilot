'use client';

import { useEffect, useRef, useState } from 'react';
import { Files, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import type { IssueDraftReviewOverlay, StructuredIssueDraft } from '@/services/intelligenceContracts';
import type { Transcript } from '@/types';
import {
  DEFAULT_ISSUE_EXPORT_SETTINGS,
  beginIssueDraftRegeneration,
  closeIssueDraftRegeneration,
  decideRegeneratedProblemIdentity,
  exportIssueDraftBatch,
  getIssueDraftReviewOverlays,
  getIssueExportSettings,
  saveMeetingIssueDrafts,
  saveIssueDraftReviewOverlay,
  setIssueExportSettings,
  type IssueExportSettings,
  type IssueDraftRegenerationSession,
  type StoredStructuredIssueDraft,
} from '@/services/intelligenceIssueDraft';
import {
  applyIssueDraftReviewOverlay,
  bindRegeneratedDraftToMeeting,
  createIssueDraftReviewOverlay,
  rebaseIssueDraftReviewOverlay,
  reviewFieldHash,
  REVIEWABLE_DRAFT_FIELDS,
  type ReviewableDraftField,
} from '@/services/issueDraftReview';

function parseDrafts(records: StoredStructuredIssueDraft[]): StructuredIssueDraft[] {
  return records.flatMap((record) => {
    try { return [JSON.parse(record.draft_json) as StructuredIssueDraft]; } catch { return []; }
  });
}

export function StructuredIssueDraftsDialog({
  meetingId,
  records,
  transcripts,
  onRegenerated,
}: {
  meetingId: string;
  records: StoredStructuredIssueDraft[];
  transcripts: Transcript[];
  onRegenerated: (records: StoredStructuredIssueDraft[]) => void;
}) {
  const [drafts, setDrafts] = useState(() => parseDrafts(records));
  const [selected, setSelected] = useState<string[]>([]);
  const [exportSettings, setExportSettings] = useState<IssueExportSettings>(DEFAULT_ISSUE_EXPORT_SETTINGS);
  const [language, setLanguage] = useState<'ja' | 'en' | 'ko'>(
    () => parseDrafts(records)[0]?.language ?? 'en',
  );
  const [isRegenerating, setIsRegenerating] = useState(false);
  const [overlays, setOverlays] = useState<Record<string, IssueDraftReviewOverlay>>({});
  const [reviewPainId, setReviewPainId] = useState<string | null>(null);
  const [identityRegeneration, setIdentityRegeneration] = useState<IssueDraftRegenerationSession | null>(null);
  const identityRegenerationRef = useRef<IssueDraftRegenerationSession | null>(null);
  const [identityActionPending, setIdentityActionPending] = useState(false);
  const selectedDrafts = drafts
    .filter((draft) => selected.includes(draft.pain_id))
    .map((draft) => applyIssueDraftReviewOverlay(draft, overlays[draft.pain_id]));
  const selectedAreReviewed = selected.every((painId) => {
    const overlay = overlays[painId];
    return overlay
      && (overlay.status === 'reviewed' || overlay.status === 'exported')
      && Object.keys(overlay.rebase_conflicts).length === 0;
  });
  const reviewDraft = drafts.find((draft) => draft.pain_id === reviewPainId) ?? null;
  const reviewOverlay = reviewDraft ? overlays[reviewDraft.pain_id] : undefined;

  useEffect(() => setDrafts(parseDrafts(records)), [records]);

  useEffect(() => {
    void getIssueDraftReviewOverlays(meetingId).then((stored) => {
      setOverlays(Object.fromEntries(stored.flatMap((record) => {
        try {
          return [[record.pain_id, JSON.parse(record.overlay_json) as IssueDraftReviewOverlay]];
        } catch {
          return [];
        }
      })));
    }).catch(() => setOverlays({}));
  }, [meetingId, records]);

  useEffect(() => {
    void getIssueExportSettings().then(setExportSettings).catch(() => undefined);
  }, []);

  useEffect(() => () => {
    const active = identityRegenerationRef.current;
    if (active) void closeIssueDraftRegeneration(active);
  }, []);

  const updateIdentityRegeneration = (value: IssueDraftRegenerationSession | null) => {
    identityRegenerationRef.current = value;
    setIdentityRegeneration(value);
  };

  const exportBatch = async () => {
    if (!selectedAreReviewed) {
      toast.error('Resolve conflicts and mark every selected draft reviewed before export');
      return;
    }
    try {
      const saved = await setIssueExportSettings(exportSettings);
      setExportSettings(saved);
      await exportIssueDraftBatch(selectedDrafts, {
        ...saved,
        include_connected_excerpts: false,
      });
      for (const draft of selectedDrafts) {
        const overlay = overlays[draft.pain_id];
        if (!overlay) continue;
        const exported = { ...overlay, status: 'exported' as const, updated_at: new Date().toISOString() };
        await saveIssueDraftReviewOverlay(meetingId, draft.pain_id, exported);
        setOverlays((current) => ({ ...current, [draft.pain_id]: exported }));
      }
    } catch {
      toast.error('Could not export the reviewed issue bundle');
    }
  };

  const saveRegenerated = async (regeneration: IssueDraftRegenerationSession) => {
    const regenerated = regeneration.drafts.map((draft) => bindRegeneratedDraftToMeeting(
      draft,
      regeneration.sessionId,
      meetingId,
    ));
    try {
      await saveMeetingIssueDrafts(meetingId, regenerated);
      const rebased = Object.fromEntries(regenerated.flatMap((draft) => {
        const current = overlays[draft.pain_id];
        return current ? [[draft.pain_id, rebaseIssueDraftReviewOverlay(current, draft)]] : [];
      }));
      for (const [painId, overlay] of Object.entries(rebased)) {
        await saveIssueDraftReviewOverlay(meetingId, painId, overlay);
      }
      setOverlays(rebased);
      setDrafts(regenerated);
      setSelected([]);
      const now = new Date().toISOString();
      onRegenerated(regenerated.map((draft) => ({
        meeting_id: meetingId,
        pain_id: draft.pain_id,
        draft_json: JSON.stringify(draft),
        readiness: draft.readiness,
        language: draft.language,
        state_version: draft.state_version,
        updated_at: now,
      })));
      toast.success(
        regenerated.length === 0
          ? 'No supported problems were found in this meeting'
          : `Regenerated ${regenerated.length} issue draft${regenerated.length === 1 ? '' : 's'}`,
      );
    } finally {
      updateIdentityRegeneration(null);
      await closeIssueDraftRegeneration(regeneration);
    }
  };

  const regenerate = async () => {
    setIsRegenerating(true);
    try {
      const regeneration = await beginIssueDraftRegeneration(transcripts, language);
      if (regeneration === null) {
        toast.success('No supported problems were found in this meeting');
        return;
      }
      if (regeneration.snapshot.identity_review_candidates.length > 0) {
        updateIdentityRegeneration(regeneration);
      } else {
        await saveRegenerated(regeneration);
      }
    } catch {
      toast.error('Could not regenerate issue drafts', {
        description: 'The saved meeting and transcript were not changed.',
      });
    } finally {
      setIsRegenerating(false);
    }
  };

  const decideIdentity = async (
    painIds: [string, string],
    action: 'merge' | 'keep_separate',
  ) => {
    if (!identityRegeneration || identityActionPending) return;
    setIdentityActionPending(true);
    try {
      const updated = await decideRegeneratedProblemIdentity(
        identityRegeneration,
        painIds,
        action,
      );
      if (updated.snapshot.identity_review_candidates.length > 0) {
        updateIdentityRegeneration(updated);
      } else {
        await saveRegenerated(updated);
      }
    } catch {
      toast.error('Could not save the problem grouping decision');
    } finally {
      setIdentityActionPending(false);
    }
  };

  const saveCurrentGrouping = async () => {
    if (!identityRegeneration || identityActionPending) return;
    setIdentityActionPending(true);
    try {
      await saveRegenerated(identityRegeneration);
    } catch {
      toast.error('Could not save the regenerated issue drafts');
    } finally {
      setIdentityActionPending(false);
    }
  };

  const persistOverlay = async (overlay: IssueDraftReviewOverlay) => {
    setOverlays((current) => ({ ...current, [overlay.pain_id]: overlay }));
    try {
      await saveIssueDraftReviewOverlay(meetingId, overlay.pain_id, overlay);
    } catch {
      toast.error('Could not save the local draft review');
    }
  };

  const startReview = (draft: StructuredIssueDraft) => {
    const overlay = overlays[draft.pain_id]
      ?? createIssueDraftReviewOverlay(meetingId, draft);
    setReviewPainId(draft.pain_id);
    if (!overlays[draft.pain_id]) void persistOverlay(overlay);
  };

  const editField = (field: ReviewableDraftField, text: string) => {
    if (!reviewDraft || !reviewOverlay) return;
    const original = reviewDraft[field];
    const value = Array.isArray(original)
      ? text.split('\n').map((item) => item.trim()).filter(Boolean)
      : text.trim() || null;
    void persistOverlay({
      ...reviewOverlay,
      status: 'in_review',
      field_edits: { ...reviewOverlay.field_edits, [field]: value },
      updated_at: new Date().toISOString(),
    });
  };

  const resolveConflict = (field: ReviewableDraftField, keepEdit: boolean) => {
    if (!reviewDraft || !reviewOverlay) return;
    const fieldEdits = { ...reviewOverlay.field_edits };
    if (!keepEdit) delete fieldEdits[field];
    const conflicts = { ...reviewOverlay.rebase_conflicts };
    delete conflicts[field];
    void persistOverlay({
      ...reviewOverlay,
      status: Object.keys(conflicts).length > 0 ? 'needs_rebase' : 'in_review',
      field_edits: fieldEdits,
      base_field_hashes: {
        ...reviewOverlay.base_field_hashes,
        [field]: reviewFieldHash(reviewDraft[field] as string | string[] | null),
      },
      rebase_conflicts: conflicts,
      updated_at: new Date().toISOString(),
    });
  };

  const markReviewed = () => {
    if (!reviewOverlay || Object.keys(reviewOverlay.rebase_conflicts).length > 0) return;
    void persistOverlay({
      ...reviewOverlay,
      status: 'reviewed',
      updated_at: new Date().toISOString(),
    });
  };

  return (
    <Dialog onOpenChange={(open) => {
      if (open) {
        setSelected([]);
        return;
      }
      const active = identityRegenerationRef.current;
      updateIdentityRegeneration(null);
      if (active) void closeIssueDraftRegeneration(active);
    }}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm"><Files size={16} />Issue drafts ({drafts.length})</Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl bg-slate-50">
        <DialogHeader><DialogTitle>Reviewed issue export</DialogTitle></DialogHeader>
        <p className="text-sm text-slate-600">Select problems to export. Nothing is submitted automatically.</p>
        <div className="flex flex-wrap items-end gap-2 rounded-lg border bg-white p-3">
          <label className="grid gap-1 text-xs text-slate-600">
            Replay language
            <select value={language} onChange={(event) => setLanguage(event.target.value as 'ja' | 'en' | 'ko')} className="rounded-md border bg-white px-3 py-2">
              <option value="en">English</option>
              <option value="ja">Japanese</option>
              <option value="ko">Korean</option>
            </select>
          </label>
          <Button variant="outline" disabled={isRegenerating || identityRegeneration !== null || transcripts.length === 0} onClick={() => void regenerate()}>
            <RefreshCw size={15} className={isRegenerating ? 'animate-spin' : ''} />
            Regenerate issue drafts
          </Button>
        </div>
        {identityRegeneration && (
          <section className="space-y-2 rounded-lg border border-sky-200 bg-sky-50 p-3" aria-label="Post-meeting problem identity review">
            <div>
              <h3 className="text-sm font-semibold text-sky-950">Review possible duplicate problems</h3>
              <p className="text-xs text-sky-800">Choose whether each pair is the same problem before regenerated drafts are saved.</p>
            </div>
            {identityRegeneration.snapshot.identity_review_candidates.map((candidate) => (
              <div key={candidate.review_id} className="rounded-md border border-sky-200 bg-white p-3">
                <p className="text-sm font-medium text-slate-900">{candidate.first_subject}</p>
                <p className="text-sm font-medium text-slate-900">{candidate.second_subject}</p>
                <div className="mt-2 flex gap-2">
                  <Button
                    size="sm"
                    disabled={identityActionPending}
                    onClick={() => void decideIdentity(
                      [candidate.first_pain_id, candidate.second_pain_id],
                      'merge',
                    )}
                  >
                    Same problem
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={identityActionPending}
                    onClick={() => void decideIdentity(
                      [candidate.first_pain_id, candidate.second_pain_id],
                      'keep_separate',
                    )}
                  >
                    Keep separate
                  </Button>
                </div>
              </div>
            ))}
            <Button
              size="sm"
              variant="outline"
              disabled={identityActionPending}
              onClick={() => void saveCurrentGrouping()}
            >
              Save current grouping
            </Button>
          </section>
        )}
        <div className="max-h-72 space-y-2 overflow-auto">
          {drafts.length === 0 && <p className="rounded-lg border border-dashed p-4 text-sm text-slate-500">No structured issue drafts are saved yet.</p>}
          {drafts.map((draft) => (
            <div key={draft.pain_id} className="flex items-start gap-3 rounded-lg border bg-white p-3">
              <input
                type="checkbox"
                aria-label={`Select ${draft.title} for export`}
                checked={selected.includes(draft.pain_id)}
                onChange={(event) => setSelected((current) => event.target.checked
                  ? [...current, draft.pain_id]
                  : current.filter((id) => id !== draft.pain_id))}
              />
              <span className="min-w-0 flex-1">
                <strong className="block break-words text-sm">{draft.title}</strong>
                <span className="text-xs text-slate-500">
                  {draft.readiness.replaceAll('_', ' ')} · {overlays[draft.pain_id]?.status.replaceAll('_', ' ') ?? 'unreviewed'}
                </span>
              </span>
              <Button variant="outline" size="sm" onClick={() => startReview(draft)}>Review</Button>
            </div>
          ))}
        </div>
        {reviewDraft && reviewOverlay && (
          <section className="max-h-80 space-y-3 overflow-auto rounded-lg border border-sky-200 bg-white p-4" aria-label="Issue draft review editor">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-900">Review: {reviewDraft.title}</h3>
                <p className="text-xs text-slate-500">
                  Generated fields remain unchanged. Edits below are stored as a local reviewer overlay.
                </p>
              </div>
              <span className="rounded-full bg-sky-50 px-2 py-1 text-[10px] font-semibold text-sky-800">
                {reviewOverlay.status.replaceAll('_', ' ')}
              </span>
            </div>
            {REVIEWABLE_DRAFT_FIELDS.map((field) => {
              const original = reviewDraft[field] as string | string[] | null;
              const edited = reviewOverlay.field_edits[field];
              const display = edited ?? original;
              const conflict = reviewOverlay.rebase_conflicts[field];
              return (
                <label key={field} className="block text-xs font-medium text-slate-700">
                  <span className="flex items-center justify-between gap-2">
                    <span>{field.replaceAll('_', ' ')}</span>
                    <span className="font-normal text-slate-400">
                      {field in reviewOverlay.field_edits ? 'user-authored overlay' : 'transcript-derived generated'}
                    </span>
                  </span>
                  <textarea
                    value={Array.isArray(display) ? display.join('\n') : display ?? ''}
                    onChange={(event) => editField(field, event.target.value)}
                    rows={field === 'title' || field === 'owner' ? 2 : 3}
                    className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm font-normal"
                  />
                  {conflict && (
                    <span className="mt-1 block rounded-md border border-amber-200 bg-amber-50 p-2 text-amber-900">
                      The generated value changed after this edit.
                      <span className="mt-2 flex gap-2">
                        <button type="button" onClick={() => resolveConflict(field, true)} className="rounded border border-amber-300 bg-white px-2 py-1 font-semibold">Keep my edit</button>
                        <button type="button" onClick={() => resolveConflict(field, false)} className="rounded border border-amber-300 bg-white px-2 py-1 font-semibold">Use regenerated</button>
                      </span>
                    </span>
                  )}
                </label>
              );
            })}
            <Button
              variant="outline"
              disabled={Object.keys(reviewOverlay.rebase_conflicts).length > 0}
              onClick={markReviewed}
            >
              Mark reviewed
            </Button>
          </section>
        )}
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="grid gap-1 text-xs text-slate-600">
            Jira work type
            <input value={exportSettings.jira_work_type} onChange={(event) => setExportSettings((current) => ({ ...current, jira_work_type: event.target.value.slice(0, 80) }))} className="rounded-md border bg-white px-3 py-2" />
          </label>
          <label className="grid gap-1 text-xs text-slate-600">
            Azure work item type
            <input value={exportSettings.azure_work_item_type} onChange={(event) => setExportSettings((current) => ({ ...current, azure_work_item_type: event.target.value.slice(0, 80) }))} className="rounded-md border bg-white px-3 py-2" />
          </label>
        </div>
        <Button disabled={selectedDrafts.length === 0 || !selectedAreReviewed} onClick={() => void exportBatch()}>
          Export selected ZIP
        </Button>
        {selectedDrafts.length > 0 && !selectedAreReviewed && (
          <p className="text-xs text-amber-700" role="status">
            Mark every selected draft reviewed and resolve all field conflicts before export.
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}
