import { invoke } from '@tauri-apps/api/core';

export const MEETING_INTELLIGENCE_SOAK_MARKER = 'meeting-intelligence-soak-v1';

export interface MeetingIntelligenceSoakTarget {
  __MEETILY_SOAK_PRELOAD__?: string;
  __MEETILY_SOAK_INVOKE__?: SoakInvoke;
  __MEETILY_SOAK_INSTALL__?: () => () => void;
}

type SoakInvoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

let soakAuthorization: Promise<boolean> | null = null;

const ALLOWED_SOAK_COMMANDS = new Set([
  'emit_meeting_intelligence_soak_update',
  'get_meeting_intelligence_backend_status',
  'get_meeting_intelligence_diagnostics',
  'get_recording_stop_diagnostics',
  'get_recording_state',
  'get_transcription_status',
  'pause_recording',
  'plugin:process|exit',
  'resume_recording',
  'retry_meeting_intelligence_backend',
  'set_language_preference',
  'start_recording_with_devices_and_meeting',
  'stop_recording',
]);

const soakInvoke: SoakInvoke = async (command, args) => {
  if (!ALLOWED_SOAK_COMMANDS.has(command)) {
    throw new Error('soak command denied');
  }
  soakAuthorization ??= invoke<boolean>('is_meeting_intelligence_soak_enabled');
  const enabled = await soakAuthorization;
  if (!enabled) {
    throw new Error('soak bridge unavailable');
  }
  return invoke(command, args);
};

// The packaged-process harness opts in before the application loads. Normal UI
// sessions never expose this test-only reference.
export function installMeetingIntelligenceSoakBridge(
  target: MeetingIntelligenceSoakTarget = window as MeetingIntelligenceSoakTarget,
): () => void {
  if (target.__MEETILY_SOAK_PRELOAD__ !== MEETING_INTELLIGENCE_SOAK_MARKER) {
    return () => undefined;
  }

  Object.defineProperty(target, '__MEETILY_SOAK_INVOKE__', {
    configurable: true,
    value: soakInvoke,
  });

  return () => {
    if (target.__MEETILY_SOAK_INVOKE__ === soakInvoke) {
      delete target.__MEETILY_SOAK_INVOKE__;
    }
  };
}

export function exposeMeetingIntelligenceSoakInstaller(
  target: MeetingIntelligenceSoakTarget = window as MeetingIntelligenceSoakTarget,
): () => void {
  const install = () => installMeetingIntelligenceSoakBridge(target);
  Object.defineProperty(target, '__MEETILY_SOAK_INSTALL__', {
    configurable: true,
    value: install,
  });

  return () => {
    delete target.__MEETILY_SOAK_INVOKE__;
    if (target.__MEETILY_SOAK_INSTALL__ === install) {
      delete target.__MEETILY_SOAK_INSTALL__;
    }
  };
}
