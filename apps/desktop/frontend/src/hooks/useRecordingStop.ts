import { useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { listen } from '@tauri-apps/api/event';
import { toast } from 'sonner';
import { useTranscripts } from '@/contexts/TranscriptContext';
import { useSidebar } from '@/components/Sidebar/SidebarProvider';
import { useRecordingState, RecordingStatus } from '@/contexts/RecordingStateContext';
import { storageService } from '@/services/storageService';
import { transcriptService } from '@/services/transcriptService';
import Analytics from '@/lib/analytics';
import {
  applyPinnedSummaryLanguageToMeeting,
  detectAndCacheSummaryLanguage,
} from '@/lib/summary-language-preferences';
import type { RecordingStoppedPayload } from '@/services/recordingService';
import {
  saveMeetingIssueDraft,
  saveMeetingIssueDrafts,
} from '@/services/intelligenceIssueDraft';
import {
  clearCachedLiveIssueDraft,
  getCachedLiveIssueDraft,
} from '@/services/liveIssueDraftCache';
import {
  clearCachedLiveStructuredIssueDrafts,
  getCachedLiveStructuredIssueDrafts,
} from '@/services/liveStructuredIssueDraftCache';
import {
  acknowledgeNativeRecordingStoppedMetadata,
  claimRecordingPostProcessing,
  clearRecordingStoppedMetadata,
  publishRecordingStoppedMetadata,
  mergeFinalizedTranscriptHistory,
  recoverNativeRecordingStoppedMetadata,
  recoverNativeRecordingStoppedTranscripts,
  recoverLatestNativeRecordingStoppedMetadata,
  releaseRecordingPostProcessing,
  waitForRecordingStoppedMetadata,
} from '@/services/recordingStopMetadata';

type SummaryStatus = 'idle' | 'processing' | 'summarizing' | 'regenerating' | 'completed' | 'error';

interface UseRecordingStopReturn {
  handleRecordingStop: (callApi: boolean) => Promise<void>;
  isStopping: boolean;
  isProcessingTranscript: boolean;
  isSavingTranscript: boolean;
  summaryStatus: SummaryStatus;
  setIsStopping: (value: boolean) => void;
}

/**
 * Custom hook for managing recording stop lifecycle.
 * Handles the complex stop sequence: transcription wait → buffer flush → SQLite save → navigation.
 *
 * Features:
 * - Transcription completion polling (5s max, 500ms interval)
 * - Transcript buffer flush coordination
 * - SQLite meeting save with session-keyed metadata from the Rust stop event
 * - Comprehensive analytics tracking (duration, word count, activation)
 * - Auto-navigation to meeting details
 * - Toast notifications for success/error
 * - Session-wide deduplication across UI and tray stop sources
 */
export function useRecordingStop(
  setIsRecording: (value: boolean) => void,
  setIsRecordingDisabled: (value: boolean) => void
): UseRecordingStopReturn {
  // USE global state instead
  const recordingState = useRecordingState();
  const {
    status,
    setStatus,
    isStopping,
    isProcessing: isProcessingTranscript,
    isSaving: isSavingTranscript
  } = recordingState;

  const {
    transcriptsRef,
    flushBuffer,
    clearTranscripts,
    meetingTitle,
    markMeetingAsSaved,
  } = useTranscripts();

  const {
    refetchMeetings,
    setCurrentMeeting,
    setIsMeetingActive,
  } = useSidebar();

  const router = useRouter();

  // Guard to prevent duplicate/concurrent stop calls (e.g., from UI and tray simultaneously)
  const stopInProgressRef = useRef(false);
  const retryPostProcessingRef = useRef<() => void>(() => undefined);

  const stoppedIntelligenceSessionIdRef = useRef<string | null>(null);
  const activeIntelligenceSessionIdRef = useRef<string | null>(
    recordingState.recordingSessionId,
  );

  useEffect(() => {
    const nextSessionId = recordingState.recordingSessionId;
    if (!nextSessionId || nextSessionId === activeIntelligenceSessionIdRef.current) return;
    stoppedIntelligenceSessionIdRef.current = null;
    activeIntelligenceSessionIdRef.current = nextSessionId;
  }, [recordingState.recordingSessionId]);

  // Set up recording-stopped listener for meeting navigation
  useEffect(() => {
    let unlistenFn: (() => void) | undefined;
    let disposed = false;

    const setupRecordingStoppedListener = async () => {
      try {
        console.log('Setting up recording-stopped listener for navigation...');
        const registeredUnlisten = await listen<RecordingStoppedPayload>('recording-stopped', async (event) => {
          const { recording_save_status } = event.payload;
          stoppedIntelligenceSessionIdRef.current = publishRecordingStoppedMetadata(
            event.payload,
            activeIntelligenceSessionIdRef.current,
          );
          if (recording_save_status === 'failed') {
            toast.error('Recording stopped, but local file finalization failed', {
              description: 'The transcript will still be saved locally when possible.',
            });
          }

        });
        if (disposed) {
          registeredUnlisten();
          return;
        }
        unlistenFn = registeredUnlisten;
        console.log('Recording stopped listener setup complete');
      } catch {
        console.error('Failed to setup recording stopped listener');
      }
    };

    setupRecordingStoppedListener();

    return () => {
      disposed = true;
      console.log('Cleaning up recording stopped listener...');
      if (unlistenFn) {
        unlistenFn();
      }
    };
  }, [router]);

  // Main recording stop handler
  const handleRecordingStop = useCallback(async (isCallApi: boolean) => {
    let postProcessingSessionId =
      stoppedIntelligenceSessionIdRef.current ?? activeIntelligenceSessionIdRef.current;
    if (!postProcessingSessionId) {
      const pending = await recoverLatestNativeRecordingStoppedMetadata();
      postProcessingSessionId = pending?.recording_session_id ?? null;
      if (!postProcessingSessionId) return;
      stoppedIntelligenceSessionIdRef.current = postProcessingSessionId;
    }
    // The page and global tray provider each own a hook instance. Claim the
    // opaque session globally so only one of them can persist this recording.
    if (
      stopInProgressRef.current ||
      !claimRecordingPostProcessing(postProcessingSessionId)
    ) {
      return;
    }
    stopInProgressRef.current = true;
    let meetingPersisted = false;
    let recoveryHandoffAcknowledged = false;

    // Set status to STOPPING immediately
    setStatus(RecordingStatus.STOPPING);
    setIsRecording(false);
    setIsRecordingDisabled(true);
    const stopStartTime = Date.now();

    try {
      console.log('Post-stop processing (new implementation)...', {
        stop_initiated_at: new Date(stopStartTime).toISOString(),
        current_transcript_count: transcriptsRef.current.length
      });

      // Note: stop_recording is already called by RecordingControls.stopRecordingAction
      // This function only handles post-stop processing (transcription wait, API call, navigation)
      console.log('Recording already stopped by RecordingControls, processing transcription...');

      // Wait for transcription to complete
      setStatus(RecordingStatus.PROCESSING_TRANSCRIPTS, 'Waiting for transcription...');
      console.log('Waiting for transcription to complete...');

      // Rust has already joined or cancelled the STT worker before this callback.
      // Keep only a short delivery-settle window for queued webview events.
      const MAX_WAIT_TIME = 5000;
      const POLL_INTERVAL = 500; // Check every 500ms
      let elapsedTime = 0;
      let transcriptionComplete = false;

      // Event delivery is an optimization; polling and bounded degradation keep
      // the stop path working if the listener cannot be registered.
      let unlistenComplete: (() => void) | null = null;
      try {
        unlistenComplete = await listen('transcription-complete', () => {
          console.log('Received transcription-complete event');
          transcriptionComplete = true;
        });
      } catch {
        console.warn('Transcription completion listener was unavailable; using status polling');
      }

      // Poll for transcription status
      while (elapsedTime < MAX_WAIT_TIME && !transcriptionComplete) {
        try {
          const status = await transcriptService.getTranscriptionStatus();
          console.log('Transcription status:', status);

          // Check if transcription is complete
          if (!status.is_processing && status.chunks_in_queue === 0) {
            console.log('Transcription complete - no active processing and no chunks in queue');
            transcriptionComplete = true;
            break;
          }

          // If no activity for more than 8 seconds and no chunks in queue, consider it done (increased from 5s to 8s)
          if (!status.is_processing && status.last_activity_ms > 8000 && status.chunks_in_queue === 0) {
            console.log('Transcription likely complete - no recent activity and empty queue');
            transcriptionComplete = true;
            break;
          }

          // Update user with current status
          if (status.chunks_in_queue > 0) {
            console.log(`Processing ${status.chunks_in_queue} remaining audio chunks...`);
            setStatus(RecordingStatus.PROCESSING_TRANSCRIPTS, `Processing ${status.chunks_in_queue} remaining chunks...`);
          }

          // Wait before next check
          await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL));
          elapsedTime += POLL_INTERVAL;
        } catch {
          console.error('Transcription status was unavailable');
          break;
        }
      }

      // Clean up listener
      console.log('🧹 CLEANUP: Cleaning up transcription-complete listener');
      unlistenComplete?.();

      if (!transcriptionComplete && elapsedTime >= MAX_WAIT_TIME) {
        console.warn('⏰ Transcription wait timeout reached after', elapsedTime, 'ms');
        toast.warning('Transcript processing reached its time limit', {
          description: 'The app will save the recording and every transcript segment received so far.',
        });
      } else {
        console.log('✅ Transcription completed after', elapsedTime, 'ms');
        // Allow queued transcript events to settle without extending stop by a minute.
        console.log('⏳ Waiting for late transcript segments...');
        await new Promise(resolve => setTimeout(resolve, 1000));
      }

      // Final buffer flush: process ALL remaining transcripts regardless of timing
      const flushStartTime = Date.now();
      console.log('🔄 Final buffer flush: forcing processing of any remaining transcripts...', {
        flush_started_at: new Date(flushStartTime).toISOString(),
        time_since_stop: flushStartTime - stopStartTime,
        current_transcript_count: transcriptsRef.current.length
      });
      setStatus(RecordingStatus.PROCESSING_TRANSCRIPTS, 'Flushing transcript buffer...');
      flushBuffer();
      const flushEndTime = Date.now();
      console.log('✅ Final buffer flush completed', {
        flush_duration: flushEndTime - flushStartTime,
        total_time_since_stop: flushEndTime - stopStartTime,
        final_transcript_count: transcriptsRef.current.length
      });

      // NOTE: Status remains PROCESSING_TRANSCRIPTS until we start saving

      // Wait a bit more to ensure all transcript state updates have been processed
      console.log('Waiting for transcript state updates to complete...');
      await new Promise(resolve => setTimeout(resolve, 500));

      // Save to SQLite
      // NOTE: enabled to save COMPLETE transcripts after frontend receives all updates
      // This ensures user sees all transcripts streaming in before database save
      // A transcription timeout or status-query failure must not orphan a
      // finalized recording. Persist the available transcript and folder
      // metadata after the bounded wait, even when STT did not signal complete.
      if (isCallApi) {

        setStatus(RecordingStatus.SAVING, 'Saving meeting to database...');

        // Get fresh transcript state (ALL transcripts including late ones)
        let freshTranscripts = [...transcriptsRef.current];

        const stoppedSessionId =
          stoppedIntelligenceSessionIdRef.current ?? activeIntelligenceSessionIdRef.current;
        let stoppedMetadata = stoppedSessionId
          ? await waitForRecordingStoppedMetadata(stoppedSessionId)
          : null;
        if (stoppedSessionId && !stoppedMetadata) {
          stoppedMetadata = await recoverNativeRecordingStoppedMetadata(stoppedSessionId);
        }
        if (stoppedSessionId && !stoppedMetadata) {
          console.warn('Recording stop metadata was unavailable before local meeting save');
        }
        const folderPath = stoppedMetadata?.folder_path ?? null;
        const savedMeetingName = stoppedMetadata?.meeting_name ?? null;
        if (stoppedSessionId) {
          const finalizedTranscripts = await recoverNativeRecordingStoppedTranscripts(
            stoppedSessionId,
          );
          freshTranscripts = mergeFinalizedTranscriptHistory(
            freshTranscripts,
            finalizedTranscripts,
          );
        }

        console.log('Saving completed recording to the local database', {
          transcript_count: freshTranscripts.length,
        });

        try {
          const responseData = await storageService.saveMeeting(
            savedMeetingName || meetingTitle || 'New Meeting',  // PREFER savedMeetingName (backend source)
            freshTranscripts,
            folderPath,
            stoppedSessionId,
          );

          const meetingId = responseData.meeting_id;
          if (!meetingId) {
            console.error('Local meeting save returned no meeting identifier');
            throw new Error('No meeting ID received from save operation');
          }
          meetingPersisted = true;

          const preparedIssueDraft = stoppedSessionId
            ? getCachedLiveIssueDraft(stoppedSessionId)
            : null;
          const structuredIssueDrafts = stoppedMetadata?.issue_drafts?.length
            ? stoppedMetadata.issue_drafts
            : stoppedSessionId
              ? getCachedLiveStructuredIssueDrafts(stoppedSessionId)
              : [];
          if (structuredIssueDrafts.length > 0) {
            try {
              await saveMeetingIssueDrafts(meetingId, structuredIssueDrafts);
              clearCachedLiveStructuredIssueDrafts(stoppedSessionId!);
              clearCachedLiveIssueDraft(stoppedSessionId!);
            } catch {
              toast.warning('Meeting saved without structured issue drafts', {
                description: 'The recording and transcript are safe. Drafts can be regenerated later.',
              });
            }
          } else if (preparedIssueDraft) {
            try {
              const saved = await saveMeetingIssueDraft(meetingId, preparedIssueDraft);
              if (!saved) {
                throw new Error('saved meeting was unavailable for issue draft persistence');
              }
              clearCachedLiveIssueDraft(stoppedSessionId!);
            } catch {
              toast.warning('Meeting saved without the issue draft', {
                description: 'The recording and transcript are safe. You can copy the draft now.',
                action: {
                  label: 'Copy draft',
                  onClick: () => {
                    void navigator.clipboard.writeText(preparedIssueDraft.markdown);
                  },
                },
              });
            }
          }
          let shouldDetectSummaryLanguage = false;
          try {
            shouldDetectSummaryLanguage = !(await applyPinnedSummaryLanguageToMeeting(meetingId));
          } catch {
            console.warn('Failed to apply pinned summary language preference for new meeting');
            toast.warning('Could not apply default summary language', {
              description: 'The meeting was saved, but the default summary language was not applied.',
            });
          }

          if (shouldDetectSummaryLanguage) {
            try {
              await detectAndCacheSummaryLanguage(
                meetingId,
                freshTranscripts.map(t => t.text)
              );
            } catch {
              console.warn('Failed to detect summary language for new meeting');
              toast.warning('Could not detect summary language', {
                description: 'The meeting was saved, but Auto could not detect the summary language.',
              });
            }
          }

          console.log('✅ Successfully saved COMPLETE meeting');
          console.log('   Transcripts:', freshTranscripts.length);

          // Mark meeting as saved in IndexedDB (for recovery system)
          await markMeetingAsSaved(stoppedSessionId ?? undefined);
          recoveryHandoffAcknowledged = true;
          if (stoppedSessionId) {
            clearRecordingStoppedMetadata(stoppedSessionId);
            await acknowledgeNativeRecordingStoppedMetadata(stoppedSessionId);
          }

          // Clean up IndexedDB meeting ID (redundant with markMeetingAsSaved cleanup, but ensures cleanup)
          sessionStorage.removeItem('indexeddb_current_meeting_id');

          // Refetch meetings and set current meeting
          await refetchMeetings();

          try {
            const meetingData = await storageService.getMeeting(meetingId);
            if (meetingData) {
              setCurrentMeeting({
                id: meetingId,
                title: meetingData.title
              });
              console.log('✅ Current meeting selected');
            }
          } catch {
            console.warn('Could not fetch meeting details; using the saved local reference');
            setCurrentMeeting({ id: meetingId, title: savedMeetingName || meetingTitle || 'New Meeting' });
          }

          // Mark as completed
          setStatus(RecordingStatus.COMPLETED);

          // Show success toast with navigation option
          toast.success('Recording saved successfully!', {
            description: `${freshTranscripts.length} transcript segments saved.`,
            action: {
              label: 'View Meeting',
              onClick: () => {
                router.push(`/meeting-details?id=${meetingId}`);
                Analytics.trackButtonClick('view_meeting_from_toast', 'recording_complete');
              }
            },
            duration: 10000,
          });

          // Auto-navigate after a short delay with source parameter
          const completedSessionId = stoppedSessionId;
          setTimeout(() => {
            const currentSessionId = activeIntelligenceSessionIdRef.current;
            if (
              currentSessionId !== null &&
              currentSessionId !== completedSessionId
            ) {
              console.info('Skipping completed-meeting navigation because a new recording started');
              return;
            }
            router.push(`/meeting-details?id=${meetingId}&source=recording`);
            clearTranscripts()
            Analytics.trackPageView('meeting_details');

            // Reset to IDLE after navigation
            setStatus(RecordingStatus.IDLE);
          }, 2000);
          // Track meeting completion analytics
          try {
            // Calculate meeting duration from transcript timestamps
            let durationSeconds = 0;
            if (freshTranscripts.length > 0 && freshTranscripts[0].audio_start_time !== undefined) {
              // Use audio_end_time of last transcript if available
              const lastTranscript = freshTranscripts[freshTranscripts.length - 1];
              durationSeconds = lastTranscript.audio_end_time || lastTranscript.audio_start_time || 0;
            }

            // Calculate word count
            const transcriptWordCount = freshTranscripts
              .map(t => t.text.split(/\s+/).length)
              .reduce((a, b) => a + b, 0);

            // Calculate words per minute
            const wordsPerMinute = durationSeconds > 0 ? transcriptWordCount / (durationSeconds / 60) : 0;

            // Get meetings count today
            const meetingsToday = await Analytics.getMeetingsCountToday();

            // Track meeting completed
            await Analytics.trackMeetingCompleted(meetingId, {
              duration_seconds: durationSeconds,
              transcript_segments: freshTranscripts.length,
              transcript_word_count: transcriptWordCount,
              words_per_minute: wordsPerMinute,
              meetings_today: meetingsToday
            });

            // Update meeting count in analytics.json
            await Analytics.updateMeetingCount();

            // Check for activation (first meeting)
            const { Store } = await import('@tauri-apps/plugin-store');
            const store = await Store.load('analytics.json');
            const totalMeetings = await store.get<number>('total_meetings');

            if (totalMeetings === 1) {
              const daysSinceInstall = await Analytics.calculateDaysSince('first_launch_date');
              await Analytics.track('user_activated', {
                meetings_count: '1',
                days_since_install: daysSinceInstall?.toString() || 'null',
                first_meeting_duration_seconds: durationSeconds.toString()
              });
            }
          } catch {
            console.error('Meeting completion analytics were unavailable');
            // Don't block user flow on analytics errors
          }

        } catch (saveError) {
          if (meetingPersisted) {
            console.error('Meeting was saved but post-save UI processing failed');
            setStatus(RecordingStatus.COMPLETED);
            toast.warning('Meeting saved with a post-processing warning', {
              description: recoveryHandoffAcknowledged
                ? 'The recording and transcript are stored locally. Reopen the meeting from history.'
                : 'The meeting is stored locally, but recovery cleanup still needs to finish.',
              action: recoveryHandoffAcknowledged
                ? undefined
                : {
                    label: 'Retry cleanup',
                    onClick: () => retryPostProcessingRef.current(),
                  },
            });
          } else {
            console.error('Failed to save meeting to the local database');
            setStatus(RecordingStatus.ERROR, 'Meeting could not be saved locally');
            toast.error('Failed to save meeting', {
              description: 'The recording folder remains on this device. Retry the local save when ready.',
              action: {
                label: 'Retry save',
                onClick: () => retryPostProcessingRef.current(),
              },
            });
            throw saveError;
          }
        }
      } else {
        // No save needed, go back to IDLE
        setStatus(RecordingStatus.IDLE);
      }

      setIsMeetingActive(false);
      // isRecording already set to false at function start
      setIsRecordingDisabled(false);
    } catch {
      console.error('Recording post-processing failed');
      setStatus(RecordingStatus.ERROR, 'Recording post-processing failed');
      // isRecording already set to false at function start
      setIsRecordingDisabled(false);
    } finally {
      const completedSessionId =
        stoppedIntelligenceSessionIdRef.current ?? activeIntelligenceSessionIdRef.current;
      if (meetingPersisted && recoveryHandoffAcknowledged) {
        if (completedSessionId) clearRecordingStoppedMetadata(completedSessionId);
        stoppedIntelligenceSessionIdRef.current = null;
        activeIntelligenceSessionIdRef.current = null;
      } else {
        releaseRecordingPostProcessing(postProcessingSessionId);
      }
      // Always reset the guard flag when done
      stopInProgressRef.current = false;
    }
  }, [
    setIsRecording,
    setIsRecordingDisabled,
    setStatus,
    transcriptsRef,
    flushBuffer,
    clearTranscripts,
    meetingTitle,
    markMeetingAsSaved,
    refetchMeetings,
    setCurrentMeeting,
    setIsMeetingActive,
    router,
  ]);

  retryPostProcessingRef.current = () => {
    void handleRecordingStop(true);
  };

  // Derive summaryStatus from RecordingStatus for backward compatibility
  const summaryStatus: SummaryStatus = status === RecordingStatus.PROCESSING_TRANSCRIPTS ? 'processing' : 'idle';

  return {
    handleRecordingStop,
    isStopping,
    isProcessingTranscript,
    isSavingTranscript,
    summaryStatus,
    setIsStopping: (value: boolean) => {
      setStatus(value ? RecordingStatus.STOPPING : RecordingStatus.IDLE);
    },
  };
}
