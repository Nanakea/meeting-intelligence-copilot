'use client';

import { invoke } from '@tauri-apps/api/core';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Play, Pause, Square, Mic, AlertCircle, X } from 'lucide-react';
import { SummaryResponse } from '@/types/summary';
import { listen } from '@tauri-apps/api/event';
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import Analytics from '@/lib/analytics';
import { useRecordingState } from '@/contexts/RecordingStateContext';

interface RecordingControlsProps {
  isRecording: boolean;
  barHeights: string[];
  onRecordingStop: (callApi?: boolean) => void;
  onRecordingStart: () => void;
  onTranscriptReceived: (summary: SummaryResponse) => void;
  onTranscriptionError?: (message: string) => void;
  onStopInitiated?: () => void; // Called immediately when stop button is clicked
  isRecordingDisabled: boolean;
  isParentProcessing: boolean;
  selectedDevices?: {
    micDevice: string | null;
    systemDevice: string | null;
  };
  meetingName?: string;
}

export const RecordingControls: React.FC<RecordingControlsProps> = ({
  isRecording,
  barHeights,
  onRecordingStop,
  onRecordingStart,
  onTranscriptionError,
  onStopInitiated,
  isRecordingDisabled,
  isParentProcessing,
}) => {
  // Use global recording state context for pause state (syncs with tray operations)
  const recordingState = useRecordingState();
  const isPaused = recordingState.isPaused;

  const [showPlayback, setShowPlayback] = useState(false);
  const [, setTranscript] = useState<string>('');
  const [isProcessing, setIsProcessing] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [isPausing, setIsPausing] = useState(false);
  const [isResuming, setIsResuming] = useState(false);
  const [, setTranscriptionErrors] = useState(0);
  const [isValidatingModel] = useState(false);
  const [, setSpeechDetected] = useState(false);
  const [deviceError, setDeviceError] = useState<{ title: string, message: string } | null>(null);
  const startInFlightRef = useRef(false);
  const stopInFlightRef = useRef(false);
  const onTranscriptionErrorRef = useRef(onTranscriptionError);

  useEffect(() => {
    onTranscriptionErrorRef.current = onTranscriptionError;
  }, [onTranscriptionError]);

  const currentTime = 0;
  const duration = 0;
  const progress = 0;

  const formatTime = (time: number) => {
    const minutes = Math.floor(time / 60);
    const seconds = Math.floor(time % 60);
    return `${minutes}:${seconds.toString().padStart(2, '0')}`;
  };

  useEffect(() => {
    const checkTauri = async () => {
      try {
        const result = await invoke('is_recording');
        console.log('Tauri is initialized and ready, is_recording result:', result);
      } catch {
        console.error('Tauri initialization failed');
        alert('Failed to initialize recording. Please check the console for details.');
      }
    };
    checkTauri();
  }, []);

  const handleStartRecording = useCallback(async () => {
    if (startInFlightRef.current || isStarting || isValidatingModel) return;
    startInFlightRef.current = true;
    setIsStarting(true);
    console.log('Starting recording...');
    console.log('Current isRecording state:', isRecording);

    setShowPlayback(false);
    setTranscript(''); // Clear any previous transcript
    setSpeechDetected(false); // Reset speech detection on new recording

    try {
      // Call the validation callback which will:
      // 1. Check if model is ready
      // 2. Show appropriate toast/modal
      // 3. Call backend if valid
      // 4. Update UI state
      await onRecordingStart();
    } catch (error) {
      console.error('Failed to start recording');

      // Parse error message to provide user-friendly feedback
      const errorMsg = error instanceof Error ? error.message : String(error);

      // Check for device-related errors
      if (errorMsg.includes('microphone') || errorMsg.includes('mic') || errorMsg.includes('input')) {
        setDeviceError({
          title: 'Microphone Not Available',
          message: 'Unable to access your microphone. Please check that:\n• Your microphone is connected\n• The app has microphone permissions\n• No other app is using the microphone'
        });
      } else if (errorMsg.includes('system audio') || errorMsg.includes('speaker') || errorMsg.includes('output')) {
        setDeviceError({
          title: 'System Audio Not Available',
          message: 'Unable to capture system audio. Please check that:\n• A virtual audio device (like BlackHole) is installed\n• The app has screen recording permissions (macOS)\n• System audio is properly configured'
        });
      } else if (errorMsg.includes('permission')) {
        setDeviceError({
          title: 'Permission Required',
          message: 'Recording permissions are required. Please:\n• Grant microphone access in System Settings\n• Grant screen recording access for system audio (macOS)\n• Restart the app after granting permissions'
        });
      } else {
        setDeviceError({
          title: 'Recording Failed',
          message: 'Unable to start recording. Please check your audio device settings and try again.'
        });
      }
    } finally {
      startInFlightRef.current = false;
      setIsStarting(false);
    }
  }, [onRecordingStart, isStarting, isValidatingModel, isRecording]);

  const stopRecordingAction = useCallback(async () => {
    console.log('Executing stop recording...');
    try {
      setIsProcessing(true);
      console.log('About to call stop_recording command');
      await invoke('stop_recording', {
        args: {
          // Native finalization uses the manager-owned recording folder.
          save_path: ''
        }
      });
      console.log('stop_recording command completed successfully');
      // setShowPlayback(true);
      setIsProcessing(false);
      // Track successful transcription
      Analytics.trackTranscriptionSuccess();
      onRecordingStop(true);
    } catch {
      console.error('Failed to stop recording');
      // Native stop is idempotent and may have committed teardown before an
      // event or cleanup phase failed. Persist every segment available once.
      onRecordingStop(true);
    } finally {
      setIsProcessing(false);
      stopInFlightRef.current = false;
      setIsStopping(false);
    }
  }, [onRecordingStop]);

  const handleStopRecording = useCallback(async () => {
    console.log('handleStopRecording called - isRecording:', isRecording, 'isStarting:', isStarting, 'isStopping:', isStopping);
    if (!isRecording || isStarting || isStopping || stopInFlightRef.current) {
      console.log('Early return from handleStopRecording due to state check');
      return;
    }

    stopInFlightRef.current = true;
    console.log('Stopping recording...');

    // Notify parent immediately (for UI state updates)
    onStopInitiated?.();

    setIsStopping(true);

    // Immediately trigger the stop action
    await stopRecordingAction();
  }, [isRecording, isStarting, isStopping, stopRecordingAction, onStopInitiated]);

  const handlePauseRecording = useCallback(async () => {
    if (!isRecording || isPaused || isPausing) return;

    console.log('Pausing recording...');
    setIsPausing(true);

    try {
      await invoke('pause_recording');
      // isPaused state now managed by RecordingStateContext via events
      console.log('Recording paused successfully');
    } catch {
      console.error('Failed to pause recording');
      alert('Failed to pause recording. Please check the console for details.');
    } finally {
      setIsPausing(false);
    }
  }, [isRecording, isPaused, isPausing]);

  const handleResumeRecording = useCallback(async () => {
    if (!isRecording || !isPaused || isResuming) return;

    console.log('Resuming recording...');
    setIsResuming(true);

    try {
      await invoke('resume_recording');
      // isPaused state now managed by RecordingStateContext via events
      console.log('Recording resumed successfully');
    } catch {
      console.error('Failed to resume recording');
      alert('Failed to resume recording. Please check the console for details.');
    } finally {
      setIsResuming(false);
    }
  }, [isRecording, isPaused, isResuming]);

  useEffect(() => {
    return () => {
      // Cleanup on unmount if needed
    };
  }, []);

  useEffect(() => {
    console.log('Setting up recording event listeners');
    const unsubscribes: (() => void)[] = [];
    let disposed = false;

    const retainListener = (unlisten: () => void): boolean => {
      if (disposed) {
        unlisten();
        return false;
      }
      unsubscribes.push(unlisten);
      return true;
    };

    const setupListeners = async () => {
      try {
        // Transcript error listener - handles both regular and actionable errors
        const transcriptErrorUnsubscribe = await listen('transcript-error', () => {
          console.error('Transcript processing failed');
          const userMessage = 'Speech recognition failed for an audio segment. Recording remains active.';

          Analytics.trackTranscriptionError('speech_recognition_failed');

          setTranscriptionErrors(prev => {
            const newCount = prev + 1;
            console.log('Transcription error count incremented:', newCount);
            return newCount;
          });
          setIsProcessing(false);
          console.warn('Speech recognition reported an error; recording remains active');
          if (onTranscriptionErrorRef.current) {
            onTranscriptionErrorRef.current(userMessage);
          }
        });
        if (!retainListener(transcriptErrorUnsubscribe)) return;

        // Transcription error listener - handles structured error objects with actionable flag
        const transcriptionErrorUnsubscribe = await listen('transcription-error', () => {
          console.error('Transcription failed');

          Analytics.trackTranscriptionError('speech_recognition_failed');

          setTranscriptionErrors(prev => {
            const newCount = prev + 1;
            console.log('Transcription error count incremented:', newCount);
            return newCount;
          });
          setIsProcessing(false);
          console.warn('Speech recognition reported an error; recording remains active');

          // useModalState owns the user-facing message for structured errors.
        });
        if (!retainListener(transcriptionErrorUnsubscribe)) return;

        // Pause/Resume events are now handled by RecordingStateContext
        // No need for duplicate listeners here

        // Speech detected listener - for UX feedback when VAD detects speech
        const speechDetectedUnsubscribe = await listen('speech-detected', () => {
          setSpeechDetected(true);
        });
        if (!retainListener(speechDetectedUnsubscribe)) return;
        console.log('Recording event listeners set up successfully');
      } catch {
        console.error('Failed to set up recording event listeners');
      }
    };

    setupListeners();

    return () => {
      disposed = true;
      console.log('Cleaning up recording event listeners');
      unsubscribes.forEach(unsubscribe => {
        if (unsubscribe && typeof unsubscribe === 'function') {
          unsubscribe();
        }
      });
    };
  }, []);

  return (
    <TooltipProvider>
      <div className="flex flex-col space-y-2">
        <div className="flex items-center space-x-2 bg-white rounded-full shadow-lg px-4 py-2">
          {isProcessing && !isParentProcessing ? (
            <div className="flex items-center space-x-2">
              <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-gray-900"></div>
              <span className="text-sm text-gray-600">Processing recording...</span>
            </div>
          ) : (
            <>
              {showPlayback ? (
                <>
                  <button
                    onClick={handleStartRecording}
                    aria-label="Start a new recording"
                    className="w-10 h-10 flex items-center justify-center bg-red-500 rounded-full text-white hover:bg-red-600 transition-colors"
                  >
                    <Mic size={16} />
                  </button>

                  <div className="w-px h-6 bg-gray-200 mx-1" />

                  <div className="flex items-center space-x-1 mx-2">
                    <div className="text-sm text-gray-600 min-w-[40px]">
                      {formatTime(currentTime)}
                    </div>
                    <div
                      className="relative w-24 h-1 bg-gray-200 rounded-full"
                    >
                      <div
                        className="absolute h-full bg-blue-500 rounded-full"
                        style={{ width: `${progress}%` }}
                      />
                    </div>
                    <div className="text-sm text-gray-600 min-w-[40px]">
                      {formatTime(duration)}
                    </div>
                  </div>

                  <button
                    aria-label="Playback unavailable"
                    className="w-10 h-10 flex items-center justify-center bg-gray-300 rounded-full text-white cursor-not-allowed"
                    disabled
                  >
                    <Play size={16} />
                  </button>
                </>
              ) : (
                <>
                  {!isRecording ? (
                    // Start recording button
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          aria-label="Start recording"
                          onClick={() => {
                            Analytics.trackButtonClick('start_recording', 'recording_controls');
                            handleStartRecording();
                          }}
                          disabled={isStarting || isProcessing || isRecordingDisabled || isValidatingModel}
                          className={`w-12 h-12 flex items-center justify-center ${isStarting || isProcessing || isValidatingModel ? 'bg-gray-400' : 'bg-red-500 hover:bg-red-600'
                            } rounded-full text-white transition-colors relative`}
                        >
                          {isValidatingModel ? (
                            <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-white"></div>
                          ) : (
                            <Mic size={20} />
                          )}
                        </button>
                      </TooltipTrigger>
                      <TooltipContent>
                        <p>Start recording</p>
                      </TooltipContent>
                    </Tooltip>
                  ) : (
                    // Recording controls (pause/resume + stop)
                    <>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button
                            aria-label={isPaused ? 'Resume recording' : 'Pause recording'}
                            onClick={() => {
                              if (isPaused) {
                                Analytics.trackButtonClick('resume_recording', 'recording_controls');
                                handleResumeRecording();
                              } else {
                                Analytics.trackButtonClick('pause_recording', 'recording_controls');
                                handlePauseRecording();
                              }
                            }}
                            disabled={isPausing || isResuming || isStopping}
                            className={`w-10 h-10 flex items-center justify-center ${isPausing || isResuming || isStopping
                              ? 'bg-gray-200 border-2 border-gray-300 text-gray-400'
                              : 'bg-white border-2 border-gray-300 text-gray-600 hover:border-gray-400 hover:bg-gray-50'
                              } rounded-full transition-colors relative`}
                          >
                            {isPaused ? <Play size={16} /> : <Pause size={16} />}
                            {(isPausing || isResuming) && (
                              <div className="absolute -top-8 text-gray-600 font-medium text-xs">
                                {isPausing ? 'Pausing...' : 'Resuming...'}
                              </div>
                            )}
                          </button>
                        </TooltipTrigger>
                        <TooltipContent>
                          <p>{isPaused ? 'Resume recording' : 'Pause recording'}</p>
                        </TooltipContent>
                      </Tooltip>

                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button
                            aria-label="Stop recording"
                            onClick={() => {
                              Analytics.trackButtonClick('stop_recording', 'recording_controls');
                              handleStopRecording();
                            }}
                            disabled={isStopping || isPausing || isResuming}
                            className={`w-10 h-10 flex items-center justify-center ${isStopping || isPausing || isResuming ? 'bg-gray-400' : 'bg-red-500 hover:bg-red-600'
                              } rounded-full text-white transition-colors relative`}
                          >
                            <Square size={16} />
                            {isStopping && (
                              <div className="absolute -top-8 text-gray-600 font-medium text-xs">
                                Stopping...
                              </div>
                            )}
                          </button>
                        </TooltipTrigger>
                        <TooltipContent>
                          <p>Stop recording</p>
                        </TooltipContent>
                      </Tooltip>
                    </>
                  )}

                  <div className="flex items-center space-x-1 mx-4">
                    {barHeights.map((height, index) => (
                      <div
                        key={index}
                        className={`w-1 rounded-full transition-all duration-200 ${isPaused ? 'bg-orange-500' : 'bg-red-500'
                          }`}
                        style={{
                          height: isRecording && !isPaused ? height : '4px',
                          opacity: isPaused ? 0.6 : 1,
                        }}
                      />
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>

        {/* Show validation status only */}
        {isValidatingModel && (
          <div className="text-xs text-gray-600 text-center mt-2">
            Validating speech recognition...
          </div>
        )}

        {/* Device error alert */}
        {deviceError && (
          <Alert variant="destructive" className="mt-4 border-red-300 bg-red-50">
            <AlertCircle className="h-5 w-5 text-red-600" />
            <button
              onClick={() => setDeviceError(null)}
              className="absolute right-3 top-3 text-red-600 hover:text-red-800 transition-colors"
              aria-label="Close alert"
            >
              <X className="h-4 w-4" />
            </button>
            <AlertTitle className="text-red-800 font-semibold mb-2">
              {deviceError.title}
            </AlertTitle>
            <AlertDescription className="text-red-700">
              {deviceError.message.split('\n').map((line, i) => (
                <div key={i} className={i > 0 ? 'ml-2' : ''}>
                  {line}
                </div>
              ))}
            </AlertDescription>
          </Alert>
        )}
      </div>
    </TooltipProvider>
  );
};
