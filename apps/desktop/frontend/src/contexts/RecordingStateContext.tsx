'use client';

import React, { createContext, useContext, useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  recordingService,
  type IntelligenceIngressStatus,
} from '@/services/recordingService';

/**
 * Recording state synchronized with backend
 * This context provides a single source of truth for recording state
 * that automatically syncs with the Rust backend, solving:
 * 1. Page refresh desync (backend recording but UI shows stopped)
 * 2. Pause state visibility across components
 * 3. Comprehensive state for future features (reconnection, etc.)
 */

// Recording lifecycle status enum
export enum RecordingStatus {
  IDLE = 'idle',                          // Not recording
  STARTING = 'starting',                  // Initiating recording
  RECORDING = 'recording',                // Active recording
  STOPPING = 'stopping',                  // Stop initiated, waiting for backend
  PROCESSING_TRANSCRIPTS = 'processing',  // Transcription completion wait
  SAVING = 'saving',                      // Saving to database
  COMPLETED = 'completed',                // Successfully saved
  ERROR = 'error'                         // Error occurred
}

interface RecordingState {
  isRecording: boolean;           // Is a recording session active
  isPaused: boolean;              // Is the recording paused
  isActive: boolean;              // Is actively recording (recording && !paused)
  recordingDuration: number | null;  // Total duration including pauses
  activeDuration: number | null;     // Active recording time (excluding pauses)
  recordingSessionId: string | null;
  intelligenceIngressStatus: IntelligenceIngressStatus;

  // NEW: Lifecycle status
  status: RecordingStatus;
  statusMessage?: string;  // Optional message for current status
}

interface RecordingStateContextType extends RecordingState {
  // NEW: Setters for status management
  setStatus: (status: RecordingStatus, message?: string) => void;

  // Computed helpers (derived from status)
  isStopping: boolean;
  isProcessing: boolean;
  isSaving: boolean;
}

const RecordingStateContext = createContext<RecordingStateContextType | null>(null);

export const useRecordingState = () => {
  const context = useContext(RecordingStateContext);
  if (!context) {
    throw new Error('useRecordingState must be used within a RecordingStateProvider');
  }
  return context;
};

export function RecordingStateProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<RecordingState>({
    isRecording: false,
    isPaused: false,
    isActive: false,
    recordingDuration: null,
    activeDuration: null,
    recordingSessionId: null,
    intelligenceIngressStatus: 'explicit_language_required',
    status: RecordingStatus.IDLE,  // NEW: Initialize with IDLE status
    statusMessage: undefined,       // NEW: No message initially
  });

  const pollingIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // NEW: Status setter with logging
  const setStatus = useCallback((status: RecordingStatus, message?: string) => {
    setState(prev => {
      console.log(`[RecordingState] Status: ${prev.status} → ${status}`, message || '');
      return {
        ...prev,
        status,
        statusMessage: message,
      };
    });
  }, []);

  /**
   * Sync recording state with backend
   * Called on mount (fixes refresh desync) and periodically while recording
   */
  const syncWithBackend = useCallback(async () => {
    try {
      const backendState = await recordingService.getRecordingState();

      setState(prev => ({
        ...prev,
        isRecording: backendState.is_recording,
        isPaused: backendState.is_paused,
        isActive: backendState.is_active,
        recordingDuration: backendState.recording_duration,
        activeDuration: backendState.active_duration,
        recordingSessionId: backendState.recording_session_id,
        intelligenceIngressStatus: backendState.intelligence_ingress_status,
      }));

      console.log('[RecordingStateContext] Synced with backend');
    } catch {
      console.error('[RecordingStateContext] Failed to sync with backend');
      // Don't update state on error - keep current state
    }
  }, []);

  /**
   * Start polling backend state (called when recording starts)
   */
  const startPolling = useCallback(() => {
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current);
    }

    console.log('[RecordingStateContext] Starting state polling (500ms interval)');
    pollingIntervalRef.current = setInterval(syncWithBackend, 500);
  }, [syncWithBackend]);

  /**
   * Stop polling backend state (called when recording stops)
   */
  const stopPolling = useCallback(() => {
    if (pollingIntervalRef.current) {
      console.log('[RecordingStateContext] Stopping state polling');
      clearInterval(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
    }
  }, []);

  /**
   * Set up event listeners for backend state changes
   */
  useEffect(() => {
    console.log('[RecordingStateContext] Setting up event listeners');
    const unsubscribers: (() => void)[] = [];
    let disposed = false;

    const retainListener = (unlisten: () => void): boolean => {
      if (disposed) {
        unlisten();
        return false;
      }
      unsubscribers.push(unlisten);
      return true;
    };

    const setupListeners = async () => {
      try {
        // Recording started
        const unlistenStarted = await recordingService.onRecordingStarted((payload) => {
          console.log('[RecordingStateContext] Recording started event');
          setState(prev => ({
            ...prev,
            isRecording: true,
            isPaused: false,
            isActive: true,
            recordingSessionId: payload.recording_session_id,
            intelligenceIngressStatus: payload.intelligence_ingress_status,
            status: RecordingStatus.RECORDING,  // NEW: Set status to RECORDING
          }));
          startPolling();
        });
        if (!retainListener(unlistenStarted)) return;

        // Recording stopped
        const unlistenStopped = await recordingService.onRecordingStopped(() => {
          console.log('[RecordingStateContext] Recording stopped event');
          setState(prev => {
            // Set status to STOPPING if not already in stop flow
            // This ensures smooth UI transition for tray/keyboard stops
            const newStatus = [
              RecordingStatus.STOPPING,
              RecordingStatus.PROCESSING_TRANSCRIPTS,
              RecordingStatus.SAVING
            ].includes(prev.status)
              ? prev.status  // Already in stop flow
              : RecordingStatus.STOPPING;  // New stop, transition smoothly

            return {
              ...prev,
              status: newStatus,
              statusMessage: newStatus === RecordingStatus.STOPPING ? 'Stopping recording...' : prev.statusMessage,
              isRecording: false,
              isPaused: false,
              isActive: false,
              recordingDuration: null,
              activeDuration: null,
              recordingSessionId: null,
              intelligenceIngressStatus: 'explicit_language_required',
            };
          });
          stopPolling();
        });
        if (!retainListener(unlistenStopped)) return;

        // Recording paused
        const unlistenPaused = await recordingService.onRecordingPaused(() => {
          console.log('[RecordingStateContext] Recording paused event');
          setState(prev => ({
            ...prev,
            isPaused: true,
            isActive: false,
          }));
        });
        if (!retainListener(unlistenPaused)) return;

        // Recording resumed
        const unlistenResumed = await recordingService.onRecordingResumed(() => {
          console.log('[RecordingStateContext] Recording resumed event');
          setState(prev => ({
            ...prev,
            isPaused: false,
            isActive: true,
          }));
        });
        if (!retainListener(unlistenResumed)) return;

        console.log('[RecordingStateContext] Event listeners set up successfully');
      } catch {
        console.error('[RecordingStateContext] Failed to set up event listeners');
      }
    };

    setupListeners();

    return () => {
      disposed = true;
      console.log('[RecordingStateContext] Cleaning up event listeners');
      unsubscribers.forEach(unsub => unsub());
      stopPolling();
    };
  }, [startPolling, stopPolling]);

  /**
   * Initial sync on mount - CRITICAL for fixing refresh desync bug
   * If backend is recording but UI state is false, this will correct it
   */
  useEffect(() => {
    console.log('[RecordingStateContext] Initial mount - syncing with backend');
    syncWithBackend();
  }, [syncWithBackend]);

  // NEW: Computed helpers from status
  const contextValue = useMemo(() => ({
    ...state,
    setStatus,
    isStopping: state.status === RecordingStatus.STOPPING,
    isProcessing: state.status === RecordingStatus.PROCESSING_TRANSCRIPTS,
    isSaving: state.status === RecordingStatus.SAVING,
  }), [state, setStatus]);

  return (
    <RecordingStateContext.Provider value={contextValue}>
      {children}
    </RecordingStateContext.Provider>
  );
}
