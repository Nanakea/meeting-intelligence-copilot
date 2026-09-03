import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Switch } from '@/components/ui/switch';
import { Copy, FileDown, FolderOpen, Trash2 } from 'lucide-react';
import { invoke } from '@tauri-apps/api/core';
import { DeviceSelection, SelectedDevices } from '@/components/DeviceSelection';
import Analytics from '@/lib/analytics';
import { toast } from 'sonner';
import { indexedDBService } from '@/services/indexedDBService';
import { useSidebar } from '@/components/Sidebar/SidebarProvider';
import { RecordingStatus, useRecordingState } from '@/contexts/RecordingStateContext';
import { WorkdayReadinessCard } from '@/components/WorkdayReadinessCard';
import {
  applyMeetingRetention,
  deleteAllStoredMeetings,
  exportAggregateDiagnostics,
  getPilotSettings,
  savePilotSettings,
  type MeetingRetention,
} from '@/services/intelligencePilot';

export interface RecordingPreferences {
  save_folder: string;
  auto_save: boolean;
  file_format: string;
  preferred_mic_device: string | null;
  preferred_system_device: string | null;
}

interface RecordingSettingsProps {
  onSave?: (preferences: RecordingPreferences) => void;
}

export function RecordingSettings({ onSave }: RecordingSettingsProps) {
  const router = useRouter();
  const { refetchMeetings, setCurrentMeeting, setIsMeetingActive } = useSidebar();
  const recordingState = useRecordingState();
  const deletionBlocked = recordingState.isRecording || [
    RecordingStatus.STARTING,
    RecordingStatus.STOPPING,
    RecordingStatus.PROCESSING_TRANSCRIPTS,
    RecordingStatus.SAVING,
  ].includes(recordingState.status);
  const [preferences, setPreferences] = useState<RecordingPreferences>({
    save_folder: '',
    auto_save: true,
    file_format: 'mp4',
    preferred_mic_device: null,
    preferred_system_device: null
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showRecordingNotification, setShowRecordingNotification] = useState(true);
  const [retention, setRetention] = useState<MeetingRetention>('forever');
  const [retentionSaving, setRetentionSaving] = useState(false);
  const [meetingDataLocation, setMeetingDataLocation] = useState('');
  const [intelligenceDataLocation, setIntelligenceDataLocation] = useState('');
  const [deletingData, setDeletingData] = useState(false);

  // Load recording preferences on component mount
  useEffect(() => {
    const loadPreferences = async () => {
      try {
        const prefs = await invoke<RecordingPreferences>('get_recording_preferences');
        setPreferences(prefs);
      } catch {
        console.error('Failed to load recording preferences');
        // If loading fails, get default folder path
        try {
          const defaultPath = await invoke<string>('get_default_recordings_folder_path');
          setPreferences(prev => ({ ...prev, save_folder: defaultPath }));
        } catch {
          console.error('Failed to get default folder path');
        }
      } finally {
        setLoading(false);
      }
    };

    loadPreferences();
  }, []);

  useEffect(() => {
    void Promise.all([
      getPilotSettings(),
      invoke<string>('get_database_directory'),
      invoke<string>('get_meeting_intelligence_data_directory'),
    ]).then(([pilotSettings, meetingDirectory, intelligenceDirectory]) => {
      setRetention(pilotSettings.retention);
      setMeetingDataLocation(meetingDirectory);
      setIntelligenceDataLocation(intelligenceDirectory);
    }).catch(() => {
      setRetention('forever');
    });
  }, []);

  // Load recording notification preference
  useEffect(() => {
    const loadNotificationPref = async () => {
      try {
        const { Store } = await import('@tauri-apps/plugin-store');
        const store = await Store.load('preferences.json');
        const show = await store.get<boolean>('show_recording_notification') ?? true;
        setShowRecordingNotification(show);
      } catch {
        console.error('Failed to load notification preference');
      }
    };
    loadNotificationPref();
  }, []);

  const handleAutoSaveToggle = async (enabled: boolean) => {
    const newPreferences = { ...preferences, auto_save: enabled };
    setPreferences(newPreferences);
    await savePreferences(newPreferences);

    // Track auto-save setting change
    await Analytics.track('auto_save_recording_toggled', {
      enabled: enabled.toString()
    });
  };

  const handleDeviceChange = async (devices: SelectedDevices) => {
    const newPreferences = {
      ...preferences,
      preferred_mic_device: devices.micDevice,
      preferred_system_device: devices.systemDevice
    };
    setPreferences(newPreferences);
    await savePreferences(newPreferences);

    // Track default device preference changes
    // Note: Individual device selection analytics are tracked in DeviceSelection component
    await Analytics.track('default_devices_changed', {
      has_preferred_microphone: (!!devices.micDevice).toString(),
      has_preferred_system_audio: (!!devices.systemDevice).toString()
    });
  };

  const handleOpenFolder = async () => {
    try {
      await invoke('open_recordings_folder');
    } catch {
      console.error('Failed to open recordings folder');
    }
  };

  const handleNotificationToggle = async (enabled: boolean) => {
    try {
      setShowRecordingNotification(enabled);
      const { Store } = await import('@tauri-apps/plugin-store');
      const store = await Store.load('preferences.json');
      await store.set('show_recording_notification', enabled);
      await store.save();
      toast.success('Preference saved');
      await Analytics.track('recording_notification_preference_changed', {
        enabled: enabled.toString()
      });
    } catch {
      console.error('Failed to save notification preference');
      toast.error('Failed to save preference');
    }
  };

  const handleOpenDataFolder = async (command: string) => {
    try {
      await invoke(command);
    } catch {
      toast.error('Local data folder could not be opened');
    }
  };

  const handleCopyPath = async (path: string) => {
    try {
      await navigator.clipboard.writeText(path);
      toast.success('Local path copied');
    } catch {
      toast.error('Local path could not be copied');
    }
  };

  const handleRetentionChange = async (value: MeetingRetention) => {
    const previousRetention = retention;
    setRetention(value);
    setRetentionSaving(true);
    let settingsSaved = false;
    try {
      const currentSettings = await getPilotSettings();
      const settings = await savePilotSettings({ ...currentSettings, retention: value });
      settingsSaved = true;
      const result = await applyMeetingRetention(settings);
      if (result.fileCleanupFailures > 0) {
        toast.warning('Retention preference saved with cleanup warnings', {
          description: `${result.fileCleanupFailures} expired recording folders were retained for a safe retry`,
        });
      } else {
        toast.success('Retention preference saved', {
          description: result.deletedMeetings > 0
            ? `${result.deletedMeetings} expired meetings and recording folders removed`
            : 'Saved meetings remain local under the selected policy',
        });
      }
    } catch {
      if (!settingsSaved) {
        setRetention(previousRetention);
        toast.error('Retention preference could not be saved');
      } else {
        toast.warning('Retention preference saved; cleanup will retry', {
          description: 'Some expired local meetings could not be removed now. The app will retry the saved policy on startup.',
        });
      }
    } finally {
      setRetentionSaving(false);
    }
  };

  const handleDiagnosticsExport = async () => {
    try {
      await exportAggregateDiagnostics();
      toast.success('Aggregate diagnostics exported', {
        description: 'The export contains counters and versions only.',
      });
    } catch {
      toast.error('Diagnostics export is unavailable');
    }
  };

  const handleDeleteAllMeetingData = async () => {
    if (deletionBlocked) {
      toast.warning('Stop and finish saving the current recording before deleting local data');
      return;
    }
    if (!window.confirm('Delete all saved meetings, transcripts, summaries, audio folders, recovery data, and usefulness feedback from this device?')) {
      return;
    }
    setDeletingData(true);
    try {
      const result = await deleteAllStoredMeetings();
      const secondaryFailures: string[] = [];
      if (result.fileCleanupFailures === 0) {
        try {
          await indexedDBService.deleteAllMeetings();
        } catch {
          secondaryFailures.push('browser recovery data could not be cleared');
        }
      } else {
        secondaryFailures.push('browser recovery references were retained for a safe retry');
      }
      try {
        await invoke('delete_all_meeting_intelligence_feedback');
      } catch {
        secondaryFailures.push('usefulness feedback could not be cleared');
      }
      await refetchMeetings().catch(() => undefined);
      setCurrentMeeting({ id: 'intro-call', title: '+ New Call' });
      setIsMeetingActive(false);
      router.push('/');
      if (
        result.fileCleanupFailures > 0 ||
        result.recoveryCleanupFailed ||
        secondaryFailures.length > 0
      ) {
        const warnings = [
          result.fileCleanupFailures > 0
            ? `${result.fileCleanupFailures} recording folders or meeting records remain available for a safe retry`
            : null,
          result.recoveryCleanupFailed
            ? 'Transient intelligence recovery data could not be cleared; retry when the local backend is ready'
            : null,
          ...secondaryFailures,
        ].filter((message): message is string => message !== null);
        toast.warning('Some local meeting data was retained', {
          description: warnings.join('. '),
        });
      } else {
        toast.success('Local meeting data deleted', {
          description: `${result.deletedMeetings} stored meetings and recording folders removed`,
        });
      }
    } catch {
      await refetchMeetings().catch(() => undefined);
      toast.error('Local meeting data deletion was incomplete');
    } finally {
      setDeletingData(false);
    }
  };

  const savePreferences = async (prefs: RecordingPreferences) => {
    setSaving(true);
    try {
      await invoke('set_recording_preferences', { preferences: prefs });
      onSave?.(prefs);

      // Show success toast with device details
      const micDevice = prefs.preferred_mic_device || 'Default';
      const systemDevice = prefs.preferred_system_device || 'Default';
      toast.success("Device preferences saved", {
        description: `Microphone: ${micDevice}, System Audio: ${systemDevice}`
      });
    } catch {
      console.error('Failed to save recording preferences');
      toast.error("Failed to save device preferences", {
        description: 'The local recording preference could not be saved.'
      });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="animate-pulse">
        <div className="h-4 bg-gray-200 rounded w-1/4 mb-4"></div>
        <div className="h-8 bg-gray-200 rounded mb-4"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold mb-4">Recording Settings</h3>
        <p className="text-sm text-gray-600 mb-6">
          Configure how your audio recordings are saved during meetings.
        </p>
      </div>

      {/* Auto Save Toggle */}
      <div className="flex items-center justify-between p-4 border rounded-lg">
        <div className="flex-1">
          <div className="font-medium">Save Audio Recordings</div>
          <div className="text-sm text-gray-600">
            Automatically save audio files when recording stops
          </div>
        </div>
        <Switch
          checked={preferences.auto_save}
          onCheckedChange={handleAutoSaveToggle}
          disabled={saving}
        />
      </div>

      {/* Folder Location - Only shown when auto_save is enabled */}
      {preferences.auto_save && (
        <div className="space-y-4">
          <div className="p-4 border rounded-lg bg-gray-50">
            <div className="font-medium mb-2">Save Location</div>
            <div className="text-sm text-gray-600 mb-3 break-all">
              {preferences.save_folder || 'Default folder'}
            </div>
            <button
              onClick={handleOpenFolder}
              className="flex items-center gap-2 px-3 py-2 text-sm border border-gray-300 rounded-md hover:bg-gray-50 transition-colors"
            >
              <FolderOpen className="w-4 h-4" />
              Open Folder
            </button>
          </div>

          <div className="p-4 border rounded-lg bg-blue-50">
            <div className="text-sm text-blue-800">
              <strong>File Format:</strong> {preferences.file_format.toUpperCase()} files
            </div>
            <div className="text-xs text-blue-600 mt-1">
              Recordings are saved with timestamp: recording_YYYYMMDD_HHMMSS.{preferences.file_format}
            </div>
          </div>
        </div>
      )}

      {/* Info when auto_save is disabled */}
      {!preferences.auto_save && (
        <div className="p-4 border rounded-lg bg-yellow-50">
          <div className="text-sm text-yellow-800">
            Audio recording is disabled. Enable &quot;Save Audio Recordings&quot; to automatically save your meeting audio.
          </div>
        </div>
      )}

      {/* Recording Notification Toggle */}
      <div className="flex items-center justify-between p-4 border rounded-lg">
        <div className="flex-1">
          <div className="font-medium">Recording Start Notification</div>
          <div className="text-sm text-gray-600">
            Show reminder to inform participants when recording starts
          </div>
        </div>
        <Switch
          checked={showRecordingNotification}
          onCheckedChange={handleNotificationToggle}
        />
      </div>

      {/* Device Preferences */}
      <div className="space-y-4">
        <div className="border-t pt-6">
          <h4 className="text-base font-medium text-gray-900 mb-4">Default Audio Devices</h4>
          <p className="text-sm text-gray-600 mb-4">
            Set your preferred microphone and system audio devices for recording. These will be automatically selected when starting new recordings.
          </p>

          <div className="border rounded-lg p-4 bg-gray-50">
            <DeviceSelection
              selectedDevices={{
                micDevice: preferences.preferred_mic_device,
                systemDevice: preferences.preferred_system_device
              }}
              onDeviceChange={handleDeviceChange}
              disabled={saving}
            />
          </div>
        </div>
      </div>

      <WorkdayReadinessCard
        microphoneDevice={preferences.preferred_mic_device}
        systemAudioDevice={preferences.preferred_system_device}
      />

      <div className="space-y-4 border-t pt-6">
        <div>
          <h4 className="text-base font-medium text-gray-900">Local privacy and retention</h4>
          <p className="mt-1 text-sm text-gray-600">
            Meeting audio, transcripts, ASK NOW state, and feedback stay on this Windows account. BitLocker is recommended for device-level protection.
          </p>
          <p className="mt-2 rounded-lg border border-sky-100 bg-sky-50 px-3 py-2 text-sm leading-relaxed text-sky-900">
            Live intelligence works with meeting audio captured locally by this app from the selected meeting-output device. It does not use meeting-platform APIs.
          </p>
        </div>

        <label className="block rounded-lg border bg-white p-4">
          <span className="block text-sm font-medium text-gray-900">Keep saved meetings</span>
          <select
            value={retention}
            disabled={retentionSaving}
            onChange={(event) => void handleRetentionChange(event.target.value as MeetingRetention)}
            className="mt-2 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm disabled:cursor-wait disabled:opacity-60"
          >
            <option value="seven_days">7 days</option>
            <option value="thirty_days">30 days</option>
            <option value="ninety_days">90 days</option>
            <option value="forever">Until I delete them</option>
          </select>
        </label>

        <div className="rounded-lg border bg-gray-50 p-4">
          <div className="text-sm font-medium text-gray-900">Local data locations</div>
          <dl className="mt-2 space-y-2 text-xs">
            <div>
              <dt className="font-medium text-gray-700">Meetings, transcripts, summaries, and local models</dt>
              <dd className="mt-0.5 break-all text-gray-600">{meetingDataLocation || 'Application data directory'}</dd>
            </div>
            <div>
              <dt className="font-medium text-gray-700">Live recovery, pilot settings, feedback, and diagnostics</dt>
              <dd className="mt-0.5 break-all text-gray-600">{intelligenceDataLocation || 'Local intelligence data directory'}</dd>
            </div>
            <div>
              <dt className="font-medium text-gray-700">Recording audio</dt>
              <dd className="mt-0.5 break-all text-gray-600">{preferences.save_folder || 'Local recordings directory'}</dd>
            </div>
          </dl>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void handleOpenDataFolder('open_database_folder')}
              className="inline-flex items-center gap-2 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm"
            >
              <FolderOpen className="h-4 w-4" />
              Open app data
            </button>
            <button
              type="button"
              onClick={() => void handleCopyPath(meetingDataLocation)}
              disabled={!meetingDataLocation}
              className="inline-flex items-center gap-2 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm disabled:opacity-50"
            >
              <Copy className="h-4 w-4" />
              Copy app-data path
            </button>
            <button
              type="button"
              onClick={() => void handleOpenDataFolder('open_meeting_intelligence_data_directory')}
              className="inline-flex items-center gap-2 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm"
            >
              <FolderOpen className="h-4 w-4" />
              Open pilot data
            </button>
            <button
              type="button"
              onClick={() => void handleCopyPath(intelligenceDataLocation)}
              disabled={!intelligenceDataLocation}
              className="inline-flex items-center gap-2 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm disabled:opacity-50"
            >
              <Copy className="h-4 w-4" />
              Copy pilot-data path
            </button>
            <button
              type="button"
              onClick={() => void handleOpenDataFolder('open_recordings_folder')}
              className="inline-flex items-center gap-2 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm"
            >
              <FolderOpen className="h-4 w-4" />
              Open recordings
            </button>
            <button
              type="button"
              onClick={() => void handleCopyPath(preferences.save_folder)}
              disabled={!preferences.save_folder}
              className="inline-flex items-center gap-2 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm disabled:opacity-50"
            >
              <Copy className="h-4 w-4" />
              Copy recordings path
            </button>
            <button
              type="button"
              onClick={() => void handleDiagnosticsExport()}
              className="inline-flex items-center gap-2 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm"
            >
              <FileDown className="h-4 w-4" />
              Export aggregate diagnostics
            </button>
          </div>
          <p className="mt-3 text-xs leading-relaxed text-gray-500">
            Files you explicitly export to another location are outside the app retention and delete-all controls.
          </p>
        </div>

        <button
          type="button"
          onClick={() => void handleDeleteAllMeetingData()}
          disabled={deletingData || deletionBlocked}
          title={deletionBlocked ? 'Finish the current recording before deleting local data' : undefined}
          className="inline-flex items-center gap-2 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm font-medium text-red-700 disabled:opacity-50"
        >
          <Trash2 className="h-4 w-4" />
          {deletingData
            ? 'Deleting local meeting data...'
            : deletionBlocked
              ? 'Finish recording before delete all'
              : 'Delete all local meeting data'}
        </button>
      </div>
    </div>
  );
}
